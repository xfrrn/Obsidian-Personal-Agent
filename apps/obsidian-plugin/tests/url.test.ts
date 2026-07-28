import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeAgentUrl, normalizeApiBaseUrl } from "../src/url";
import { agentLaunchSpec } from "../src/agent-process";
import { parsePanelMessage } from "../src/bridge";
import { parseHostState } from "../../local-agent/src/agent/web/frontend/src/obsidian-bridge";

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

test("accepts only whitelisted iframe setting messages", () => {
  assert.deepEqual(parsePanelMessage({
    source: "codex-agent-ui",
    type: "settings:update",
    settings: { sandboxMode: "read-only", themeMode: "dark" }
  }), {
    type: "settings:update",
    settings: { sandboxMode: "read-only", themeMode: "dark" }
  });
  assert.equal(parsePanelMessage({
    source: "codex-agent-ui",
    type: "settings:update",
    settings: { apiKey: "must-not-cross-the-bridge" }
  }), null);
});

test("validates Obsidian host state before applying theme tokens", () => {
  const tokens = Object.fromEntries([
    "backgroundPrimary", "backgroundSecondary", "backgroundHover", "border",
    "textNormal", "textMuted", "textAccent", "textOnAccent", "textError",
    "textSuccess", "fontInterface", "fontText"
  ].map((name) => [name, name]));
  const state = {
    source: "obsidian-agent-plugin",
    type: "host:state",
    theme: { mode: "system", isDark: true, tokens },
    context: { workspace: "D:/Vault", activeFile: "note.md" },
    settings: { sandboxMode: "workspace-write" }
  };

  assert.equal(parseHostState(state)?.context.activeFile, "note.md");
  assert.equal(parseHostState({ ...state, theme: { ...state.theme, tokens: {} } }), null);
  assert.equal(parseHostState({ ...state, source: "unknown" }), null);
});
