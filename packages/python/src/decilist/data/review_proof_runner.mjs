/** Bounded proof executor. Defense in depth, NOT an operating-system sandbox.
 * Caller-supplied, reviewed library modules load normally with Node privileges.
 * The untrusted async proof body receives only tracked assertions, allowlisted
 * module loading, AbortController/AbortSignal, and bounded-lifetime timers.
 */
import vm from 'node:vm';
import nodeAssert from 'node:assert/strict';
import { realpath } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { AsyncLocalStorage } from 'node:async_hooks';

const MAX_INPUT = 20000;
let input = '';
for await (const part of process.stdin) {
  input += part;
  if (input.length > MAX_INPUT) throw new Error('proof input exceeds budget');
}
let assertions = 0;
const assertionFailures = new WeakSet();
const pendingFailures = new Set();
const assertionScopes = new AsyncLocalStorage();
const loadFailures = new Map();
const handles = new Set();
let observation;
const evidence = error => String(error?.message ?? error).slice(0, 4000);
const noPrototype = fn => Object.setPrototypeOf(fn, null);
const trackFailure = (error, scope = assertionScopes.getStore()) => {
  if (!(error instanceof nodeAssert.AssertionError)) return;
  assertionFailures.add(error);
  pendingFailures.add(error);
  for (let current = scope; current; current = current.parent) current.failures.add(error);
};
// Plain expected values originate in the VM, while library values originate in
// Node. Recreate ordinary objects/arrays in this realm before Node's strict deep
// checks, preserving custom class prototypes, aliasing, cycles, and array holes.
const plainValues = (value, seen = new Map()) => {
  if (value === null || typeof value !== 'object') return value;
  if (seen.has(value)) return seen.get(value);
  const proto = Object.getPrototypeOf(value);
  const array = Array.isArray(value) && Array.isArray(proto);
  const ordinary = proto !== null && Object.getPrototypeOf(proto) === null;
  if (!array && !ordinary) return value;
  const copy = array ? new Array(value.length) : {};
  seen.set(value, copy);
  for (const key of Reflect.ownKeys(value)) {
    if (Object.prototype.propertyIsEnumerable.call(value, key)) {
      Object.defineProperty(copy, key, {value: plainValues(value[key], seen), enumerable: true,
                                       configurable: true, writable: true});
    }
  }
  return copy;
};

try {
  const request = JSON.parse(input);
  if (typeof request.code !== 'string' || request.code.length < 1 || request.code.length > 4000
      || !Array.isArray(request.entry_paths) || request.entry_paths.length < 1) {
    throw new Error('invalid bounded proof request');
  }
  // Conservative direct and common obfuscated capability rejection. This is an
  // additional boundary, not a claim that lexical filtering proves isolation.
  const forbidden = /\b(?:import|require|export|process|globalThis|global|window|document|navigator|self|fetch|XMLHttpRequest|WebSocket|Worker|SharedWorker|Deno|Bun|eval|constructor|__proto__|prototype|Reflect|Proxy|module|Buffer|child_process|filesystem|readFile|writeFile|execFile|spawn|execSync|readFileSync|writeFileSync|openSync|createConnection)\b|node:|\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2})/i;
  if (forbidden.test(request.code) || /\bFunction\b/.test(request.code)) throw new Error('proof requests a prohibited capability');
  const workspace = await realpath(request.workspace);
  const entries = new Map();
  for (const entry of request.entry_paths) {
    if (typeof entry !== 'string' || !entry || entry.includes('\\') || entry.includes(':')
        || entry.split('/').some(part => !part || part === '.' || part === '..') || path.isAbsolute(entry)) {
      throw new Error('invalid allowlisted entry');
    }
    const filename = await realpath(path.resolve(workspace, entry));
    const relative = path.relative(workspace, filename);
    if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('entry escapes workspace');
    entries.set(entry, filename);
  }
  const errorConstructors = new Map();
  const runTracked = (name, operation) => {
    assertions += 1;
    const scope = {parent: assertionScopes.getStore(), failures: new Set()};
    const success = value => {
      // A successful throws/rejects can intentionally consume a nested tracked
      // assertion. Catching an assertion in the proof itself never erases it.
      if (name === 'throws' || name === 'rejects') {
        for (const error of scope.failures) pendingFailures.delete(error);
      }
      return value;
    };
    try {
      const result = assertionScopes.run(scope, operation);
      if (result && typeof result.then === 'function') {
        return result.then(success, error => { trackFailure(error, scope); throw error; });
      }
      return success(result);
    } catch (error) { trackFailure(error, scope); throw error; }
  };
  const tracked = noPrototype(function (...args) {
    return runTracked('ok', () => nodeAssert(...args));
  });
  for (const name of ['ok', 'equal', 'notEqual', 'strictEqual', 'notStrictEqual', 'deepEqual', 'notDeepEqual',
                      'deepStrictEqual', 'notDeepStrictEqual', 'fail', 'throws', 'doesNotThrow', 'rejects',
                      'doesNotReject', 'ifError', 'match', 'doesNotMatch', 'partialDeepStrictEqual']) {
    if (typeof nodeAssert[name] !== 'function') continue;
    Object.defineProperty(tracked, name, {value: noPrototype(function (...args) {
      return runTracked(name, () => {
        if (['throws', 'rejects', 'doesNotThrow', 'doesNotReject'].includes(name) && errorConstructors.has(args[1])) {
          const localConstructor = args[1];
          const hostConstructor = errorConstructors.get(localConstructor);
          args[1] = error => error instanceof localConstructor || error instanceof hostConstructor;
        }
        if (['deepEqual', 'notDeepEqual', 'deepStrictEqual', 'notDeepStrictEqual', 'partialDeepStrictEqual'].includes(name)) {
          args[0] = plainValues(args[0]);
          args[1] = plainValues(args[1]);
        }
        return nodeAssert[name](...args);
      });
    })});
  }
  Object.defineProperty(tracked, 'strict', {value: tracked});
  Object.freeze(tracked);
  const load = noPrototype(async function (entry) {
    try {
      const canonical = typeof entry === 'string' && entry.startsWith('./') ? entry.slice(2) : entry;
      if (typeof canonical !== 'string' || !entries.has(canonical)) throw new Error('entry is not allowlisted');
      return await import(pathToFileURL(entries.get(canonical)).href);
    } catch (error) {
      loadFailures.set(error, error instanceof SyntaxError ? 'syntax_error' : 'import_error');
      throw error;
    }
  });
  // Timer IDs are stable even when earlier timers complete.
  const timerIds = new Map();
  let nextTimerId = 0;
  const safeTimeout = noPrototype(function (callback, milliseconds = 0, ...args) {
    if (typeof callback !== 'function' || !Number.isFinite(milliseconds) || milliseconds < 0 || milliseconds > 120000) {
      throw new Error('invalid proof timer');
    }
    const id = ++nextTimerId;
    const handle = setTimeout(() => { handles.delete(handle); timerIds.delete(id); callback(...args); }, milliseconds);
    handles.add(handle);
    timerIds.set(id, handle);
    return id;
  });
  const safeClearTimeout = noPrototype(function (id) {
    const handle = timerIds.get(id);
    if (handle) { clearTimeout(handle); handles.delete(handle); timerIds.delete(id); }
  });
  const context = vm.createContext(Object.assign(Object.create(null), {
    assert: tracked, load, AbortController, AbortSignal,
    setTimeout: safeTimeout, clearTimeout: safeClearTimeout,
  }), {codeGeneration: {strings: false, wasm: false}});
  const localErrorConstructors = vm.runInContext('({Error, TypeError, RangeError, SyntaxError, ReferenceError, URIError, EvalError, AggregateError})', context);
  for (const [name, Constructor] of Object.entries(localErrorConstructors)) {
    errorConstructors.set(Constructor, globalThis[name]);
  }
  let script;
  try {
    script = new vm.Script(`(async () => {\n"use strict";\n${request.code}\n})()`,
                           {filename: 'reproduction.cjs'});
  } catch (error) {
    observation = {outcome: 'syntax_error', assertions, evidence: evidence(error)};
  }
  if (script) {
    // Keep a handle alive even if the proof awaits a promise that never settles;
    // the parent process owns the final hard deadline and reaps this process.
    const lifetime = setTimeout(() => {}, 121000);
    handles.add(lifetime);
    try {
      await script.runInContext(context, {timeout: Math.max(1, Math.min(120000, request.timeout_ms || 4000))});
      const swallowed = pendingFailures.values().next().value;
      observation = {outcome: swallowed ? 'assertion_failure' : 'pass', assertions,
                     evidence: swallowed ? evidence(swallowed) : 'reproduction completed'};
    } catch (error) {
      const outcome = assertionFailures.has(error) ? 'assertion_failure'
        : loadFailures.get(error) ?? (error?.code === 'ERR_SCRIPT_EXECUTION_TIMEOUT' ? 'timeout' : 'error');
      observation = {outcome, assertions, evidence: evidence(error)};
    }
  }
} catch (error) {
  observation = {outcome: 'error', assertions, evidence: evidence(error)};
} finally {
  for (const handle of handles) clearTimeout(handle);
}
process.stdout.write(JSON.stringify(observation));
