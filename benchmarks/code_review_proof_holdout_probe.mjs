// Host-private holdout checks. Never include this file or target labels in participant context.
// Complete upstream production modules are imported unchanged. No external requests.
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
const leaks = [];
const onUnhandled = error => leaks.push(error);
process.on('unhandledRejection', onUnhandled);

async function waitRegression() {
  const {default: pWaitFor, TimeoutError} = await load('index.js');
  check(await pWaitFor(() => true), undefined, 'immediately satisfied condition');
  let calls = 0;
  check(await pWaitFor(async () => ++calls === 3, {interval: 1}), undefined, 'async polling until satisfied');
  check(calls, 3, 'exact completed poll count');
  const value = {answer: 42};
  check(await pWaitFor(() => pWaitFor.resolveWith(value)), value, 'custom resolution value');
  for (const interval of [-1, NaN, Infinity, '1']) await rejects(pWaitFor(() => true, {interval}), TypeError, 'invalid interval');
  for (const timeout of [-1, NaN, {milliseconds: -1}, {milliseconds: NaN}]) await rejects(pWaitFor(() => true, {timeout}), TypeError, 'invalid timeout');
  for (const result of [0, null, undefined, 'yes']) await rejects(pWaitFor(() => result), TypeError, 'condition requires boolean');
  const failure = new Error('condition failed');
  await rejects(pWaitFor(() => {throw failure;}), error => error === failure, 'sync condition error identity');
  await rejects(pWaitFor(async () => {throw failure;}), error => error === failure, 'async condition error identity');
  await rejects(pWaitFor(() => false, {interval: 20, timeout: 3}), TimeoutError, 'timeout while sleeping');
  await rejects(pWaitFor(() => false, {interval: 20, timeout: {milliseconds: 3, message: failure}}), error => error === failure, 'explicit timeout error');
  check(await pWaitFor(() => false, {interval: 20, timeout: {milliseconds: 3, fallback: () => 'fallback'}}), 'fallback', 'timeout fallback result');
  await rejects(pWaitFor(() => false, {interval: 20, timeout: {milliseconds: 3, fallback: () => {throw failure;}}}), error => error === failure, 'fallback error identity');
  const reason = new Error('stop'); let invoked = false;
  await rejects(pWaitFor(() => {invoked = true; return true;}, {signal: AbortSignal.abort(reason)}), error => error === reason, 'already aborted wait');
  check(invoked, false, 'pre-abort skips condition');
  let delayed = false;
  const initial = pWaitFor(() => {delayed = true; return true;}, {before: false, interval: 3});
  check(delayed, false, 'before false delays first poll'); await initial;
  check(delayed, true, 'delayed first poll eventually runs');
}

async function debounceRegression() {
  const {default: pDebounce} = await load('index.js');
  throws(() => pDebounce(() => {}, Infinity), TypeError, 'finite wait required');
  const calls = [];
  const owner = {base: 10, run: pDebounce(function (value) {calls.push(value); return this.base + value;}, 3)};
  check(await Promise.all([owner.run(1), owner.run(2), owner.run(3)]), [13, 13, 13], 'trailing callers share latest arguments and receiver');
  check(calls, [3], 'one trailing execution per batch');
  const failure = new Error('debounced failure');
  await rejects(pDebounce(async () => {throw failure;}, 1)(), error => error === failure, 'time debounce rejection identity');
  const controller = new AbortController(); let invoked = false;
  const canceled = pDebounce(() => {invoked = true;}, 50, {signal: controller.signal})();
  controller.abort(failure);
  await rejects(canceled, error => error === failure, 'abort rejects pending batch');
  check(invoked, false, 'aborted pending batch does not execute');
  const gate = deferred(); let executions = 0;
  const shared = pDebounce.promise(async value => {executions++; await gate.promise; return value;});
  const a = shared('first'), b = shared('ignored');
  gate.resolve(); check(await Promise.all([a, b]), ['first', 'first'], 'pending promise calls share execution result');
  check(executions, 1, 'pending promise calls execute once');
  check(await shared('later'), 'later', 'later call starts fresh');
  check(executions, 2, 'settled execution is released');
  const failing = pDebounce.promise(async () => {throw failure;});
  await rejects(failing(), error => error === failure, 'promise debounce rejection identity');
  await rejects(failing(), error => error === failure, 'failed execution permits retry');
  const afterGate = deferred(), recorded = [];
  const queuedOwner = {prefix: 'v', run: pDebounce.promise(async function (value) {
    recorded.push(value); if (value === 1) await afterGate.promise; return this.prefix + value;
  }, {after: true})};
  const first = queuedOwner.run(1), second = queuedOwner.run(2), third = queuedOwner.run(3);
  afterGate.resolve();
  check(await Promise.all([first, second, third]), ['v1', 'v3', 'v3'], 'queued callers receive latest queued result');
  check(recorded, [1, 3], 'after option runs current then latest queued call');
}

async function memoizeRegression() {
  const {default: pMemoize} = await load('index.js');
  let calls = 0;
  const memoized = pMemoize(async value => {calls++; return value * 2;});
  check(await memoized(2), 4, 'first successful call');
  check(await memoized(2), 4, 'cached successful call');
  check(calls, 1, 'sequential cache hit avoids call');
  check(await memoized(3), 6, 'different key computes');
  check(calls, 2, 'different key executes');
  pMemoize.clear(memoized); check(await memoized(2), 4, 'clear permits recomputation');
  check(calls, 3, 'clear drops completed cache');
  throws(() => pMemoize.clear(() => {}), Error, 'cannot clear ordinary function');
  for (const maxAge of [NaN, Infinity, 1.5]) throws(() => pMemoize(async () => 1, {maxAge}), TypeError, 'maxAge integer validation');
  let rejectedCalls = 0;
  const failure = new Error('memoized failure');
  const failing = pMemoize(async () => {rejectedCalls++; throw failure;});
  await rejects(failing(), error => error === failure, 'first rejection identity');
  await rejects(failing(), error => error === failure, 'rejected promise retries by default');
  check(rejectedCalls, 2, 'default skips rejection cache');
  let cachedRejections = 0;
  const cachedFailure = pMemoize(async () => {cachedRejections++; throw failure;}, {cachePromiseRejection: true});
  await rejects(cachedFailure(), error => error === failure, 'opt-in rejection first call');
  await rejects(cachedFailure(), error => error === failure, 'opt-in rejection cache hit');
  check(cachedRejections, 1, 'opt-in caches rejection');
  const owner = {base: 10, compute: pMemoize(async function (a, b) {return this.base + a + b;}, {cacheKey: JSON.stringify})};
  check(await owner.compute(1, 2), 13, 'custom key and receiver');
  check(await owner.compute(1, 3), 14, 'custom key considers all arguments');
  const cache = new Map();
  const custom = pMemoize(async value => value, {cache});
  check(await custom('key'), 'key', 'custom cache value');
  check(cache.has('key'), true, 'custom cache receives entry');
  pMemoize.clear(custom); check(cache.size, 0, 'custom cache clear');
  let expiredCalls = 0;
  const expiring = pMemoize(async () => ++expiredCalls, {maxAge: 5});
  check(await expiring(), 1, 'expiring cache initial call');
  check(await expiring(), 1, 'expiring cache immediate hit');
  await sleep(12); check(await expiring(), 2, 'expired cache recomputes');
}

async function lruRegression() {
  const {default: QuickLRU} = await load('index.js');
  for (const maxSize of [0, -1, undefined]) throws(() => new QuickLRU({maxSize}), TypeError, 'capacity greater than zero required');
  const lru = new QuickLRU({maxSize: 3}), key = {key: 1}, value = {value: 1};
  check(lru.size, 0, 'initial size');
  check(lru.set(key, value), lru, 'set returns instance');
  check(lru.get(key), value, 'object keys and values');
  check(lru.has(key), true, 'has stored key');
  lru.set('a', 1).set('b', 2);
  check(lru.size, 3, 'capacity filled');
  lru.set('a', 7); check(lru.get('a'), 7, 'repeated key updates value');
  check(lru.size, 3, 'duplicate writes count once');
  check(lru.peek(key), value, 'peek stored object');
  check(lru.get('missing'), undefined, 'missing get');
  check(lru.has('missing'), false, 'missing has');
  check(lru.delete('missing'), false, 'missing deletion');
  check(lru.delete(key), true, 'existing deletion');
  check(lru.size, 2, 'deletion reduces count');
  check(new Set(lru.keys()), new Set(['a', 'b']), 'key iteration');
  check(new Set(lru.values()), new Set([7, 2]), 'value iteration');
  check(new Map(lru).get('a'), 7, 'entry iteration');
  lru.clear(); check(lru.size, 0, 'clear size'); check([...lru], [], 'clear iteration');
  const recent = new QuickLRU({maxSize: 2});
  recent.set('a', 1).set('b', 2); recent.get('a'); recent.set('c', 3);
  check(recent.has('a'), true, 'read refreshes recency');
  check(recent.has('b'), false, 'older key is evicted after rollover');
}

async function target() {
  if (id === 'H01') {
    const {default: pWaitFor, TimeoutError} = await load('index.js');
    for (const branch of ['abort', 'timeout', 'fallback']) {
      const gate = deferred(), controller = new AbortController();
      const reason = new Error('stop pending condition'); let calls = 0;
      const options = branch === 'abort' ? {signal: controller.signal}
        : {timeout: branch === 'fallback' ? {milliseconds: 3, fallback: () => 'fallback'} : 3};
      const waiting = pWaitFor(() => {calls++; return gate.promise;}, options);
      const settled = waiting.then(value => ({value}), error => ({error}));
      if (branch === 'abort') controller.abort(reason);
      const outcome = await Promise.race([settled, sleep(40).then(() => ({pending: true}))]);
      // Let historical parent polls finish too; the observation above remains unchanged.
      gate.resolve(false); await settled;
      check(outcome.pending, undefined, 'cancellation must settle while async condition is pending');
      if (branch === 'abort') check(outcome.error, reason, 'abort reason identity');
      else if (branch === 'timeout') check(outcome.error instanceof TimeoutError, true, 'pending condition times out');
      else check(outcome.value, 'fallback', 'pending condition timeout fallback');
      check(calls, 1, 'canceled condition cannot start another poll');
    }
  } else if (id === 'H02') {
    const {default: pDebounce} = await load('index.js');
    for (const reason of [undefined, null, false, 0, '', NaN]) {
      const debounced = pDebounce.promise(async () => {throw reason;});
      check(await Promise.allSettled([debounced(), debounced()]),
        [{status: 'rejected', reason}, {status: 'rejected', reason}], 'all callers preserve rejection even with falsy reason');
    }
    const gate = deferred();
    const queued = pDebounce.promise(async value => {if (value === 'first') {await gate.promise; throw null;} return value;}, {after: true});
    const outcomes = Promise.allSettled([queued('first'), queued('queued')]); gate.resolve();
    check(await outcomes, [{status: 'rejected', reason: null}, {status: 'fulfilled', value: 'queued'}], 'queued work survives falsy initial rejection');
    check(await queued('next'), 'next', 'subsequent execution after falsy failure');
  } else if (id === 'H03') {
    const {default: pMemoize} = await load('index.js');
    for (const cacheKey of [undefined, arguments_ => JSON.stringify(arguments_)]) {
      const gate = deferred(); let calls = 0;
      const memoized = pMemoize(async value => {calls++; await gate.promise; return value + calls;}, {cacheKey});
      const first = memoized(4), second = memoized(4); gate.resolve();
      check(await Promise.all([first, second]), [5, 5], 'concurrent same-key calls share one result');
      check(calls, 1, 'pending same-key work is memoized');
      check(await memoized(4), 5, 'completed shared result remains cached');
    }
    const gate = deferred(), failure = new Error('shared failure'); let calls = 0;
    const failing = pMemoize(async () => {calls++; await gate.promise; throw failure;});
    const outcomes = Promise.allSettled([failing(), failing()]); gate.resolve();
    check(await outcomes, [{status: 'rejected', reason: failure}, {status: 'rejected', reason: failure}], 'concurrent failure propagates to all callers');
    check(calls, 1, 'failing pending execution is shared');
  } else if (id === 'H04') {
    const {default: QuickLRU} = await load('index.js');
    for (const maxSize of [2, 3, 5]) {
      const lru = new QuickLRU({maxSize});
      for (let index = 0; index < maxSize + 1; index++) lru.set(`key${index}`, index);
      check(lru.size, maxSize, 'reported size respects capacity after rollover');
      lru.set(`key${maxSize}`, 'updated');
      check(lru.size, maxSize, 'replacement after rollover does not inflate count');
      check(lru.get(`key${maxSize}`), 'updated', 'replacement retains value');
      lru.clear(); check(lru.size, 0, 'clear after rollover');
      lru.set('new', 1); check(lru.size, 1, 'size remains accurate below capacity');
    }
  } else throw new Error('unknown holdout case');
}

try {
  if (!['regression', 'target'].includes(mode)) throw new Error('expected regression or target mode');
  if (mode === 'target') await target();
  else if (id === 'H01') await waitRegression();
  else if (id === 'H02') await debounceRegression();
  else if (id === 'H03') await memoizeRegression();
  else if (id === 'H04') await lruRegression();
  else throw new Error('unknown holdout case');
  await tick(); check(leaks.length, 0, 'no unhandled promise rejections');
  console.log(JSON.stringify({status: 'passed', mode, checks}));
} catch (error) {
  console.error(JSON.stringify({status: 'failed', mode, checks, name: error.name,
    message: error.message, stack: String(error.stack).slice(0, 2500)}));
  process.exitCode = 1;
} finally {
  process.removeListener('unhandledRejection', onUnhandled);
}
