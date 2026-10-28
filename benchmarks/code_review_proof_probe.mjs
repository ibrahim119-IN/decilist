// Host-private checks. Do not expose this file or its target labels to participants.
// Historical production modules are imported unchanged. No external requests.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join, resolve} from 'node:path';
import {setTimeout as sleep} from 'node:timers/promises';

const [id, folder, option, value] = process.argv.slice(2);
const mode = option === '--mode' ? value : option;
const root = resolve(folder ?? '.');
const load = path => import(pathToFileURL(join(root, path)).href);
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => Promise.withResolvers();
let checks = 0;
const check = (actual, expected, label) => {assert.deepEqual(actual, expected, label); checks++;};
const rejects = async (promise, test, label) => {await assert.rejects(promise, test, label); checks++;};
const throws = (action, type, label) => {assert.throws(action, type, label); checks++;};
const collect = async iterable => {const result = []; for await (const item of iterable) result.push(item); return result;};
const leaks = [];
const onUnhandled = error => leaks.push(error);
process.on('unhandledRejection', onUnhandled);

function timers() {
  let nextHandle = 0;
  const active = new Map(), scheduled = [], cleared = [];
  return {
    scheduled, cleared, active,
    setTimeout(callback, milliseconds) {
      const handle = ++nextHandle;
      const record = {callback, milliseconds, handle};
      scheduled.push(record); active.set(handle, record);
      return handle;
    },
    clearTimeout(handle) {cleared.push(handle); active.delete(handle);},
    fire() {
      const [handle, record] = active.entries().next().value ?? [];
      if (!record) throw new Error('no controlled timer to fire');
      active.delete(handle); record.callback();
    },
  };
}

async function pMapRegression() {
  const {default: pMap, pMapSkip} = await load('index.js');
  check(await pMap([], value => value), [], 'empty input');
  check(await pMap([Promise.resolve(2), 3], (value, index) => value * 2 + index), [4, 7], 'values, promises, and indexes');
  check(await pMap([1, 2, 3], value => value === 2 ? pMapSkip : value), [1, 3], 'skip omits only selected values');
  for (const concurrency of [0, -1, 1.5, NaN]) await rejects(pMap([1], value => value, {concurrency}), TypeError, 'invalid concurrency');
  await rejects(pMap([1], null), TypeError, 'mapper is required');
  let running = 0, maximum = 0;
  check(await pMap([1, 2, 3, 4], async value => {
    maximum = Math.max(maximum, ++running); await sleep(2); running--; return value;
  }, {concurrency: 2}), [1, 2, 3, 4], 'concurrent results preserve order');
  check(maximum, 2, 'mapper concurrency bound');
  const failure = new Error('mapper failure');
  await rejects(pMap([1], () => {throw failure;}), error => error === failure, 'mapper error identity');
  const errors = [new Error('first'), new Error('second')];
  await rejects(pMap([0, 1], index => {throw errors[index];}, {stopOnError: false, concurrency: 1}),
    error => error instanceof AggregateError && error.errors.length === 2 && error.errors.every((item, index) => item === errors[index]), 'collect mapper errors');
  const sourceError = new Error('source failure');
  function* brokenSource() {yield 1; throw sourceError;}
  await rejects(pMap(brokenSource(), value => value, {concurrency: 1}), error => error === sourceError, 'source errors propagate');
  const reason = new Error('cancelled');
  await rejects(pMap([1], value => value, {signal: AbortSignal.abort(reason)}), error => error === reason, 'cancellation reason');
}

async function pMapIterableRegression() {
  const {pMapIterable, pMapSkip} = await load('index.js');
  check(await collect(pMapIterable([], value => value)), [], 'empty stream');
  check(await collect(pMapIterable([2, 3], (value, index) => [value * 2, index], {concurrency: 2})), [[4, 0], [6, 1]], 'ordered values and indexes');
  check(await collect(pMapIterable([1, 2, 3], value => value === 2 ? pMapSkip : value, {concurrency: 1})), [1, 3], 'skip in stream');
  throws(() => pMapIterable([1], value => value, {concurrency: 0}), TypeError, 'invalid concurrency');
  throws(() => pMapIterable([1], value => value, {concurrency: 2, backpressure: 1}), TypeError, 'invalid backpressure');
  throws(() => pMapIterable([1], undefined), TypeError, 'missing stream mapper');
  const failure = new Error('stream mapper failed');
  await rejects(collect(pMapIterable([1], () => {throw failure;}, {concurrency: 1})), error => error === failure, 'stream error identity');
  async function* source() {yield 1; yield 2;}
  check(await collect(pMapIterable(source(), async value => value + 1, {concurrency: 1})), [2, 3], 'asynchronous source');
  let running = 0, maximum = 0;
  check(await collect(pMapIterable([1, 2, 3, 4], async value => {
    maximum = Math.max(maximum, ++running); await sleep(2); running--; return value;
  }, {concurrency: 2, backpressure: 3})), [1, 2, 3, 4], 'stream concurrency values');
  check(maximum, 2, 'stream concurrency bound');
}

async function timeoutRegression() {
  const {default: pTimeout, TimeoutError} = await load('index.js');
  check(await pTimeout(Promise.resolve(42), {milliseconds: 100}), 42, 'fulfilled input');
  const failure = new Error('input failed');
  await rejects(pTimeout(Promise.reject(failure), {milliseconds: 100}), error => error === failure, 'input rejection identity');
  for (const milliseconds of [0, -1, NaN, '1']) await rejects(pTimeout(Promise.resolve(1), {milliseconds}), TypeError, 'invalid timeout');
  check(await pTimeout(Promise.resolve(7), {milliseconds: Infinity}), 7, 'infinite timeout still adopts input');
  for (const [message, expected] of [[undefined, 'timeout'], ['custom', 'custom'], [false, 'resolved']]) {
    const clock = timers(); const input = new Promise(() => {}); let canceled = 0; input.cancel = () => canceled++;
    const result = pTimeout(input, {milliseconds: 5, message, customTimers: clock});
    const observed = result.then(value => ({value}), error => ({error}));
    clock.fire(); const outcome = await observed;
    if (expected === 'resolved') check(outcome.value, undefined, 'silent timeout fulfills undefined');
    else {check(outcome.error instanceof TimeoutError, true, 'timeout error type'); if (message) check(outcome.error.message, message, 'timeout message');}
    check(canceled, 1, 'ordinary timeout cancels input');
    check(clock.active.size, 0, 'settled timeout clears timer');
  }
  const explicit = new Error('explicit timeout');
  const explicitClock = timers();
  const explicitResult = pTimeout(new Promise(() => {}), {milliseconds: 3, message: explicit, customTimers: explicitClock});
  const explicitObserved = rejects(explicitResult, error => error === explicit, 'explicit timeout error identity');
  explicitClock.fire(); await explicitObserved;
  for (const fallback of [() => 8, () => Promise.resolve(8), () => {throw failure;}]) {
    const clock = timers(); const result = pTimeout(new Promise(() => {}), {milliseconds: 3, fallback, customTimers: clock});
    const observed = result.then(value => ({value}), error => ({error})); clock.fire(); const outcome = await observed;
    if (outcome.error) check(outcome.error, failure, 'fallback thrown error identity'); else check(outcome.value, 8, 'fallback adoption');
  }
  const clock = timers(), input = deferred();
  const result = pTimeout(input.promise, {milliseconds: 5, customTimers: clock});
  result.clear(); check(clock.active.size, 0, 'clear disables timeout');
  input.resolve(9); check(await result, 9, 'clear still adopts input');
  const controller = new AbortController(), pending = deferred();
  const aborted = pTimeout(pending.promise, {milliseconds: 100, signal: controller.signal});
  const reason = new Error('abort'); controller.abort(reason);
  await rejects(aborted, error => error === reason, 'active abort reason'); pending.resolve(1);
}

async function someRegression() {
  const {default: pSome} = await load('index.js');
  check(await pSome([1, Promise.resolve(2)], {count: 2}), [1, 2], 'collect required values');
  check(await pSome([1, 2, 3], {count: 1, filter: value => value > 1}), [2], 'successful filter');
  await rejects(pSome([1], {count: Infinity}), TypeError, 'count must be finite');
  await rejects(pSome([1], {count: 2}), RangeError, 'input too short');
  const failure = new Error('all failed');
  await rejects(pSome([Promise.reject(failure)], {count: 1}),
    error => error instanceof pSome.AggregateError && [...error].includes(failure), 'rejected input preserved in aggregate');
  let canceled = 0;
  const pending = new Promise(() => {}); pending.cancel = () => canceled++;
  check(await pSome([Promise.resolve(4), pending], {count: 1}), [4], 'first successful value');
  check(canceled, 1, 'settlement cancels pending input');
  const pending2 = new Promise(() => {}); pending2.cancel = () => canceled++;
  const result = pSome([pending2], {count: 1});
  const observed = result.then(() => undefined, error => error);
  result.cancel(); check((await observed).name, 'CancelError', 'explicit cancellation rejects');
  check(canceled, 2, 'explicit cancellation reaches input');
  const a = deferred(), b = deferred();
  const ordered = pSome([a.promise, b.promise], {count: 2});
  b.resolve('b'); await tick(); a.resolve('a');
  check(await ordered, ['b', 'a'], 'fulfillment order');
}

async function delayRegression() {
  const {default: delay} = await load('index.js');
  check(await delay(1, {value: 4}), 4, 'ordinary delayed value');
  const failure = new Error('delayed failure');
  await rejects(delay.reject(1, {value: failure}), error => error === failure, 'delayed rejection identity');
  const clock = timers(); const custom = delay.createWithTimers(clock);
  const first = custom(12, {value: 'first'});
  check(clock.scheduled[0].milliseconds, 12, 'custom delay uses supplied timer');
  clock.fire(); check(await first, 'first', 'custom delayed value');
  const second = custom.reject(13, {value: failure});
  const observed = rejects(second, error => error === failure, 'custom rejection identity');
  clock.fire(); await observed;
  const third = custom(14, {value: 'cleared'}); third.clear();
  check(await third, 'cleared', 'clear settles early'); check(clock.active.size, 0, 'clear cancels supplied timer');
  await rejects(delay(100, {signal: AbortSignal.abort()}), error => error.name === 'AbortError', 'already aborted delay');
  const controller = new AbortController();
  const canceled = custom(15, {signal: controller.signal});
  controller.abort(); await rejects(canceled, error => error.name === 'AbortError', 'active delay abort');
  check(clock.active.size, 0, 'abort clears supplied timer');
  check(await delay.range(1, 1, {value: 'range'}), 'range', 'ordinary range supports fixed boundary');
}

async function target() {
  if (id === 'P01') {
    const {default: pMap} = await load('index.js');
    for (const stopOnError of [true, false]) {
      let index = 0, exhausted = false, extraPulls = 0;
      const source = {[Symbol.asyncIterator]() {return {async next() {
        if (exhausted) {extraPulls++; return {done: true};}
        if (index < 3) return {done: false, value: index++};
        exhausted = true; return {done: true};
      } };} };
      const result = pMap(source, async value => {await sleep(3); if (!stopOnError) throw new Error(`failed ${value}`); return value;}, {concurrency: 2, stopOnError});
      if (stopOnError) check(await result, [0, 1, 2], 'exhaustible async source results');
      else await rejects(result, error => error instanceof AggregateError && error.errors.length === 3, 'exhaustible source aggregates errors');
      check(extraPulls, 0, 'must not pull exhausted async source');
    }
  } else if (id === 'P02') {
    const {pMapIterable} = await load('index.js');
    for (const concurrency of [2, 3]) {
      const a = deferred(), b = deferred(), c = deferred();
      const result = collect(pMapIterable([a.promise, b.promise, c.promise], (value, index) => [value, index], {concurrency}));
      await tick(); b.resolve('b'); await tick(); c.resolve('c'); await tick(); a.resolve('a');
      check(await result, [['a', 0], ['b', 1], ['c', 2]], 'promise settlement order cannot change input index');
    }
  } else if (id === 'P03') {
    const {default: pTimeout} = await load('index.js');
    const reason = new Error('already aborted'), signal = AbortSignal.abort(reason);
    for (const milliseconds of [10, Infinity]) {
      const failure = new Error('input rejected');
      const input = Promise.reject(failure);
      await rejects(pTimeout(input, {milliseconds, signal}), error => error === reason, 'abort wins and retains reason');
      await tick();
    }
    check(leaks.length, 0, 'already-aborted operation still observes input rejection');
    let observed = 0;
    const input = {then(resolve, reject) {observed++; reject(new Error('observed thenable failure'));}};
    await rejects(pTimeout(input, {milliseconds: 10, signal}), error => error === reason, 'thenable abort reason');
    check(observed, 1, 'aborted operation subscribes to input');
  } else if (id === 'P04') {
    const {default: pTimeout, TimeoutError} = await load('index.js');
    for (const milliseconds of [2 ** 31, (2 ** 31) + 1, Number.MAX_SAFE_INTEGER, Infinity]) {
      const clock = timers(), input = deferred();
      const result = pTimeout(input.promise, {milliseconds, customTimers: clock});
      input.resolve('completed'); check(await result, 'completed', 'large timeout adopts fulfilled input');
      check(clock.scheduled.length, 0, 'unrepresentable timeout cannot schedule overflowing native timer');
      check(clock.cleared.length, 0, 'timerless operation does not clear undefined handle');
    }
    const boundaryClock = timers();
    const boundary = pTimeout(new Promise(() => {}), {milliseconds: (2 ** 31) - 1, customTimers: boundaryClock});
    const observed = rejects(boundary, TimeoutError, 'largest representable delay still times out');
    check(boundaryClock.scheduled[0].milliseconds, (2 ** 31) - 1, 'representable boundary is unchanged');
    boundaryClock.fire(); await observed;
  } else if (id === 'P05') {
    const {default: pSome} = await load('index.js');
    for (const values of [[1, 2, 3], [3, 2, 1]]) {
      const failure = new Error('input rejected');
      const inputs = values.map(value => value === 3 ? Promise.reject(failure) : Promise.resolve(value));
      const result = pSome(inputs, {count: 2, filter: value => value > 1});
      const outcome = await Promise.race([result.then(value => ({value}), error => ({error})), sleep(80).then(() => ({pending: true}))]);
      check(outcome.pending, undefined, 'mixed filtered and rejected values cannot leave impossible count pending');
      check(outcome.error instanceof pSome.AggregateError, true, 'impossible mixed count rejects aggregate');
      check([...outcome.error].includes(failure), true, 'original rejected input remains represented');
    }
  } else if (id === 'P06') {
    const {default: delay} = await load('index.js');
    const clock = timers(), custom = delay.createWithTimers(clock);
    for (const [minimum, maximum] of [[7, 7], [3, 9]]) {
      const result = custom.range(minimum, maximum, {value: 'range'});
      const record = clock.scheduled.at(-1);
      check(Number.isInteger(record.milliseconds) && record.milliseconds >= minimum && record.milliseconds <= maximum, true, 'custom range schedules inclusive integer delay');
      clock.fire(); check(await result, 'range', 'custom range value');
    }
    const early = custom.range(2, 2, {value: 'early'}); early.clear();
    check(await early, 'early', 'custom range supports clear');
    check(clock.active.size, 0, 'range clear removes custom timer');
    await rejects(custom.range(1, 1, {signal: AbortSignal.abort()}), error => error.name === 'AbortError', 'range supports aborted signal');
  } else throw new Error('unknown probe case');
}

try {
  if (!['regression', 'target'].includes(mode)) throw new Error('expected regression or target probe mode');
  if (mode === 'target') await target();
  else if (id === 'P01') await pMapRegression();
  else if (id === 'P02') {await pMapRegression(); await pMapIterableRegression();}
  else if (id === 'P03' || id === 'P04') await timeoutRegression();
  else if (id === 'P05') await someRegression();
  else if (id === 'P06') await delayRegression();
  else throw new Error('unknown probe case');
  await tick();
  check(leaks.length, 0, 'no unexpected unhandled rejections');
  console.log(JSON.stringify({status: 'passed', mode, checks}));
} catch (error) {
  console.error(JSON.stringify({status: 'failed', mode, checks, name: error.name,
    message: error.message, stack: String(error.stack).slice(0, 2500)}));
  process.exitCode = 1;
} finally {
  process.removeListener('unhandledRejection', onUnhandled);
}
