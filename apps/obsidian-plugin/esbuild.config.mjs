import esbuild from "esbuild";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pluginDir = dirname(fileURLToPath(import.meta.url));
const production = process.argv[2] === "production";
const uiAssetDir = join(pluginDir, "ui", "dist", "assets");
const uiCssFiles = readdirSync(uiAssetDir).filter((name) => name.endsWith(".css"));
if (uiCssFiles.length !== 1) throw new Error("Agent UI 构建产物必须包含一个 CSS 文件。");
const uiStyles = readFileSync(join(uiAssetDir, uiCssFiles[0]), "utf8");
const context = await esbuild.context({
  entryPoints: [join(pluginDir, "src/main.ts")],
  bundle: true,
  external: ["obsidian", "electron", "node:child_process", "node:fs"],
  format: "cjs",
  target: "es2022",
  outfile: join(pluginDir, "main.js"),
  sourcemap: production ? false : "inline",
  minify: production,
  jsx: "automatic",
  define: {
    __AGENT_UI_STYLES__: JSON.stringify(uiStyles),
    "process.env.NODE_ENV": JSON.stringify(production ? "production" : "development")
  },
  treeShaking: true,
  logLevel: "info"
});

if (production) {
  await context.rebuild();
  await context.dispose();
} else {
  await context.watch();
}
