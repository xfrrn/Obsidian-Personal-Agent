import esbuild from "esbuild";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pluginDir = dirname(fileURLToPath(import.meta.url));
const production = process.argv[2] === "production";
const context = await esbuild.context({
  entryPoints: [join(pluginDir, "src/main.ts")],
  bundle: true,
  external: ["obsidian"],
  format: "cjs",
  target: "es2018",
  outfile: join(pluginDir, "main.js"),
  sourcemap: production ? false : "inline",
  treeShaking: true,
  logLevel: "info"
});

if (production) {
  await context.rebuild();
  await context.dispose();
} else {
  await context.watch();
}
