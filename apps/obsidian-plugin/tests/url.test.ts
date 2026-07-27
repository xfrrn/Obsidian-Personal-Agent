import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeAgentUrl } from "../src/url";

test('accepts only normalized loopback Agent URLs', () => {
  assert.equal(normalizeAgentUrl('http://127.0.0.1:8000/path?q=1'), 'http://127.0.0.1:8000');
  assert.equal(normalizeAgentUrl('http://localhost:8765'), 'http://localhost:8765');
  assert.throws(() => normalizeAgentUrl('https://example.com'), /回环地址/);
  assert.throws(() => normalizeAgentUrl('file:///tmp/agent'), /HTTP/);
});
