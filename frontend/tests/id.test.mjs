import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { uuid } from '../src/lib/id.js';

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test('uuid works without crypto.randomUUID (plain http:// on a LAN address)', () => {
  const insecure = { getRandomValues: (a) => webcrypto.getRandomValues(a) };
  const ids = new Set(Array.from({ length: 200 }, () => uuid(insecure)));
  assert.equal(ids.size, 200);
  for (const id of ids) assert.match(id, V4);
});

test('uuid prefers crypto.randomUUID when available', () => {
  assert.equal(uuid({ randomUUID: () => 'from-native' }), 'from-native');
  assert.match(uuid(), V4);
});
