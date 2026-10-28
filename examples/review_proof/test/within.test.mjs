import assert from 'node:assert/strict';
import test from 'node:test';
import { within } from '../within.mjs';

test('a value inside the interval is included', () => {
  assert.equal(within(3, 1, 5), true);
});

test('a value below the interval is excluded', () => {
  assert.equal(within(0, 1, 5), false);
});

test('a value above the interval is excluded', () => {
  assert.equal(within(6, 1, 5), false);
});

test('the upper endpoint is included', () => {
  assert.equal(within(5, 1, 5), true);
});
