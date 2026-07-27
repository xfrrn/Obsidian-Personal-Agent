import { App, normalizePath, TFile } from "obsidian";

export function extractFileReferencePaths(app: App, text: string): string[] {
  const files = app.vault.getMarkdownFiles().sort((a, b) => a.path.localeCompare(b.path));
  const result: string[] = [];
  for (const label of fileReferenceLabels(text)) {
    const path = resolveFileReference(files, label);
    if (path && !result.includes(path)) result.push(path);
  }
  return result;
}

function fileReferenceLabels(text: string): string[] {
  const labels: string[] = [];
  const pattern = /(^|[\s([{])@(?:\[\[([^\]\n]+)\]\]|([^\s,.;:!?()[\]{}"'`<>，。；：！？（）【】《》]+))/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    labels.push((match[2] ?? match[3] ?? "").split("|", 1)[0].split("#", 1)[0].trim());
  }
  return labels.filter(Boolean);
}

function resolveFileReference(files: readonly TFile[], label: string): string | undefined {
  const target = stripMd(normalizePath(label)).toLocaleLowerCase();
  const matches = files.filter((file) => {
    const name = file.path.split("/").pop() ?? file.path;
    return [file.path, name].some((key) => stripMd(key).toLocaleLowerCase() === target);
  });
  return matches.length === 1 ? matches[0].path : undefined;
}

function stripMd(value: string): string {
  return value.endsWith(".md") ? value.slice(0, -3) : value;
}
