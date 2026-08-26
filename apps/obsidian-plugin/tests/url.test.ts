import assert from 'node:assert/strict';
import { readFileSync } from "node:fs";
import test from 'node:test';
import { agentPortFromUrl, normalizeAgentUrl, normalizeApiBaseUrl } from "../src/url";
import { agentLaunchSpec } from "../src/agent-process";
import { isThemeMode } from "../src/theme";
import { nextDailyRun, normalizeScheduledTasks } from "../src/scheduled-tasks";

test('accepts only normalized loopback Agent URLs', () => {
  assert.equal(normalizeAgentUrl('http://127.0.0.1:8000/path?q=1'), 'http://127.0.0.1:8000');
  assert.equal(normalizeAgentUrl('http://localhost:8765'), 'http://localhost:8765');
  assert.equal(agentPortFromUrl('http://127.0.0.1:3344/path?q=1'), '3344');
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

test("calculates and restores daily scheduled tasks without duplicate running state", () => {
  const morning = new Date(2026, 0, 2, 8, 0, 0, 0).getTime();
  assert.equal(nextDailyRun("09:30", morning), new Date(2026, 0, 2, 9, 30, 0, 0).getTime());
  assert.equal(
    nextDailyRun("09:30", new Date(2026, 0, 2, 10, 0, 0, 0).getTime()),
    new Date(2026, 0, 3, 9, 30, 0, 0).getTime()
  );
  const [task] = normalizeScheduledTasks([{
    id: "daily",
    name: "整理",
    enabled: true,
    time: "09:30",
    instruction: "整理 Inbox",
    permissions: { access: "read-only", allowWeb: false },
    nextRunAt: morning,
    lastRun: { startedAt: morning, status: "running" }
  }], true);
  assert.equal(task.permissions.access, "read-only");
  assert.equal(task.lastRun?.status, "failed");
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

test("groups plugin settings and keeps one sticky save bar", () => {
  const settings = readFileSync("apps/obsidian-plugin/src/settings.ts", "utf8");
  const main = readFileSync("apps/obsidian-plugin/src/main.ts", "utf8");
  const styles = readFileSync("apps/obsidian-plugin/styles.css", "utf8");
  for (const heading of ["Agent 服务", "模型配置", "互联网搜索", "工作区与数据", "定时任务", "执行与安全", "外观", "技能（Skills）"]) {
    assert.ok(settings.includes(`setHeading("${heading}")`));
  }
  assert.match(settings, /配置已修改/);
  assert.match(settings, /\.setValue\(!disabled\.has\(skill\.name\)\)/);
  assert.match(settings, /renderSkillState\(container/);
  assert.match(settings, /pka-memory-editor/);
  assert.match(settings, /class McpServerModal extends Modal/);
  assert.match(settings, /setButtonText\("添加服务"\)/);
  assert.match(settings, /setButtonText\("高级配置"\)/);
  assert.match(settings, /Tavily API Keys/);
  assert.match(settings, /addWebKeyInputs/);
  assert.match(settings, /keys\[activeIndex\]/);
  assert.match(settings, /"chevron-left"/);
  assert.match(settings, /revealed \? "eye-off" : "eye"/);
  assert.match(settings, /pka-web-key-counter/);
  assert.match(settings, /type: "password"/);
  assert.doesNotMatch(settings, /setName\("搜索供应商"\)|setName\("正文读取供应商"\)/);
  assert.match(main, /WEB_API_KEY_SECRET_IDS/);
  assert.match(main, /secretStorage\.setSecret\(WEB_API_KEY_SECRET_IDS\[provider\]/);
  assert.doesNotMatch(main, /saveData\(this\.webApiKeys\)/);
  assert.match(main, /web_search_provider: "auto"/);
  assert.match(main, /web_fetch_provider: "auto"/);
  assert.match(main, /web_api_keys: \{/);
  assert.match(main, /disabled_skills: this\.settings\.disabledSkills/);
  assert.match(main, /async mutateMcpServer\(/);
  assert.match(main, /method: "POST"[\s\S]*JSON\.stringify\(\{ memory \}\)/);
  assert.match(styles, /\.pka-settings-actions\s*{[^}]*position: sticky;/s);
  assert.match(styles, /\.pka-settings-group\s*{[^}]*border:/s);
  assert.match(styles, /\.pka-settings-group\s*{[^}]*padding-top:/s);
  assert.match(styles, /\.pka-skill-toolbar\s*{/);
  assert.match(styles, /\.pka-mcp-toolbar\s*{/);
});
