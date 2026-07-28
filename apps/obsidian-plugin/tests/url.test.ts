import assert from 'node:assert/strict';
import { readFileSync } from "node:fs";
import test from 'node:test';
import { normalizeAgentUrl, normalizeApiBaseUrl } from "../src/url";
import { agentLaunchSpec } from "../src/agent-process";
import { isThemeMode } from "../src/theme";

test('accepts only normalized loopback Agent URLs', () => {
  assert.equal(normalizeAgentUrl('http://127.0.0.1:8000/path?q=1'), 'http://127.0.0.1:8000');
  assert.equal(normalizeAgentUrl('http://localhost:8765'), 'http://localhost:8765');
  assert.throws(() => normalizeAgentUrl('https://example.com'), /回环地址/);
  assert.throws(() => normalizeAgentUrl('file:///tmp/agent'), /HTTP/);
});

test("normalizes model API URLs without accepting embedded credentials", () => {
  assert.equal(normalizeApiBaseUrl("https://api.openai.com/v1/"), "https://api.openai.com/v1");
  assert.throws(() => normalizeApiBaseUrl("https://user:pass@example.com/v1"), /内嵌凭据/);
});

test("builds the local Python Agent command from the configured URL", () => {
  assert.deepEqual(agentLaunchSpec("http://localhost:8765"), {
    command: "python",
    args: ["-m", "agent.web.server", "--host", "127.0.0.1", "--port", "8765"]
  });
  assert.throws(() => agentLaunchSpec("https://127.0.0.1:8000"), /只支持 HTTP/);
  assert.throws(() => agentLaunchSpec("http://[::1]:8000"), /IPv6/);
  assert.deepEqual(agentLaunchSpec("http://127.0.0.1:8000", "C:\\plugin\\agent\\codex-agent.exe"), {
    command: "C:\\plugin\\agent\\codex-agent.exe",
    args: ["--host", "127.0.0.1", "--port", "8000"]
  });
});

test("accepts only supported native panel theme modes", () => {
  assert.equal(isThemeMode("system"), true);
  assert.equal(isThemeMode("dark"), true);
  assert.equal(isThemeMode("iframe"), false);
});

test("mounts the React UI directly in the Obsidian ItemView", () => {
  const view = readFileSync("apps/obsidian-plugin/src/codex-agent-view.ts", "utf8");
  const mount = readFileSync("apps/obsidian-plugin/ui/src/mount.tsx", "utf8");
  const app = readFileSync("apps/obsidian-plugin/ui/src/App.tsx", "utf8");
  const styles = readFileSync("apps/obsidian-plugin/ui/src/styles.css", "utf8");
  assert.match(view, /mountAgentApp\(root, this\.appProps\(\)\)/);
  assert.match(mount, /attachShadow\(\{ mode: "open" \}\)/);
  assert.match(styles, /:host,\s*#root\s*{[^}]*height: 100%;[^}]*overflow: hidden;/s);
  assert.match(styles, /:host\s*{[^}]*inset: 0;[^}]*position: absolute;/s);
  assert.match(app, /<section className="min-h-0 flex-1 overflow-y-auto"/);
  assert.match(app, /<form className="shrink-0 /);
  assert.doesNotMatch(view, /iframe|postMessage/);
});
