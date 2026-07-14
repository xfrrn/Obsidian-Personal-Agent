import {
  App,
  getAllTags,
  getFrontMatterInfo,
  normalizePath,
  TFile
} from "obsidian";
import { AgentError } from "../utils/protocol";

const MAX_CATALOG_CHARS = 40_000;
const MAX_NOTE_CHARS = 20_000;
const MAX_CONTEXT_CHARS = 60_000;

export interface SourceDocument {
  path: string;
  title: string;
  headings: string[];
  content: string;
}

export interface CatalogItem {
  path: string;
  title: string;
  type?: string;
  project?: string;
  status?: string;
  tags: string[];
  headings: string[];
  excerpt?: string;
}

export async function getCurrentSource(app: App): Promise<SourceDocument> {
  const file = app.workspace.getActiveFile();
  if (!file) throw new AgentError("请先打开一篇 Markdown 笔记。");

  const content = await app.vault.cachedRead(file);
  return toSource(app, file, content, MAX_CONTEXT_CHARS);
}

export async function getVaultCatalog(app: App): Promise<{
  catalog: string;
  paths: string[];
}> {
  const { items, paths } = await getVaultCatalogItems(app);
  let catalog = JSON.stringify(items);
  if (catalog.length > MAX_CATALOG_CHARS) {
    catalog = JSON.stringify(items.map(({ excerpt: _excerpt, ...item }) => item));
  }
  if (catalog.length > MAX_CATALOG_CHARS) {
    throw new AgentError("知识库目录已超过第一版查询上限，请先使用“当前笔记”范围。");
  }

  return { catalog, paths };
}

export async function getVaultCatalogItems(app: App): Promise<{
  items: CatalogItem[];
  paths: string[];
}> {
  const files = app.vault.getMarkdownFiles().sort((a, b) =>
    a.path.localeCompare(b.path)
  );

  // ponytail: 当前小型 Vault 直接扫描；目录超过提示词上限时再加入本地索引。
  const items = await Promise.all(files.map(async (file) => {
    const content = await app.vault.cachedRead(file);
    return toCatalogItem(app, file, content);
  }));

  return { items, paths: files.map((file) => file.path) };
}

export async function loadSources(
  app: App,
  paths: readonly string[]
): Promise<SourceDocument[]> {
  const sources: SourceDocument[] = [];
  let remaining = MAX_CONTEXT_CHARS;

  for (const path of paths) {
    const file = app.vault.getAbstractFileByPath(path);
    if (!(file instanceof TFile) || file.extension !== "md") continue;
    if (remaining <= 0) break;

    const content = await app.vault.cachedRead(file);
    const allowed = Math.min(MAX_NOTE_CHARS, remaining);
    sources.push(toSource(app, file, content, allowed));
    remaining -= Math.min(content.length, allowed);
  }
  return sources;
}

export function extractFileReferencePaths(app: App, text: string): string[] {
  const files = app.vault.getMarkdownFiles().sort((a, b) =>
    a.path.localeCompare(b.path)
  );
  const result: string[] = [];

  for (const label of fileReferenceLabels(text)) {
    const path = resolveFileReference(files, label);
    if (path && !result.includes(path)) result.push(path);
  }

  return result;
}

function toCatalogItem(app: App, file: TFile, content: string): CatalogItem {
  const cache = app.metadataCache.getFileCache(file);
  const frontmatter = cache?.frontmatter;
  const info = getFrontMatterInfo(content);
  const body = content.slice(info.exists ? info.contentStart : 0);

  return {
    path: file.path,
    title: shortString(frontmatter?.title) ?? file.basename,
    type: shortString(frontmatter?.type),
    project: shortString(frontmatter?.project),
    status: shortString(frontmatter?.status),
    tags: cache ? (getAllTags(cache) ?? []).slice(0, 12) : [],
    headings: (cache?.headings ?? []).slice(0, 16).map((item) => item.heading),
    excerpt: body.replace(/\s+/g, " ").trim().slice(0, 600)
  };
}

function toSource(
  app: App,
  file: TFile,
  content: string,
  limit: number
): SourceDocument {
  const cache = app.metadataCache.getFileCache(file);
  const truncated = content.length > limit
    ? `${content.slice(0, limit)}\n\n[内容因第一版上下文上限而截断]`
    : content;
  return {
    path: file.path,
    title: shortString(cache?.frontmatter?.title) ?? file.basename,
    headings: (cache?.headings ?? []).map((item) => item.heading),
    content: truncated
  };
}

function shortString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim()
    ? value.trim().slice(0, 200)
    : undefined;
}

function fileReferenceLabels(text: string): string[] {
  const labels: string[] = [];
  const pattern = /(^|[\s([{])@(?:\[\[([^\]\n]+)\]\]|([^\s,.;:!?()[\]{}"'`<>，。；：！？（）【】《》]+))/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    labels.push(cleanReferenceLabel(match[2] ?? match[3] ?? ""));
  }
  return labels.filter(Boolean);
}

function cleanReferenceLabel(label: string): string {
  return label.split("|", 1)[0].split("#", 1)[0].trim();
}

function resolveFileReference(files: readonly TFile[], label: string): string | undefined {
  const target = stripMd(normalizePath(label)).toLocaleLowerCase();
  const matches = files.filter((file) => fileReferenceKeys(file).some((key) =>
    stripMd(key).toLocaleLowerCase() === target
  ));
  return matches.length === 1 ? matches[0].path : undefined;
}

function fileReferenceKeys(file: TFile): string[] {
  const name = file.path.split("/").pop() ?? file.path;
  return [file.path, name];
}

function stripMd(value: string): string {
  return value.endsWith(".md") ? value.slice(0, -3) : value;
}
