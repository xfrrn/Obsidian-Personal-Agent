// 优先使用用户点击上下文中的选区复制；失败时再尝试权限受限的 Clipboard API。
export async function writeCodeToClipboard(text: string, legacyCopy: () => boolean, modernCopy: (value: string) => Promise<void>) {
  if (!legacyCopy()) await modernCopy(text)
}

// 未标注语言的代码块按纯文本下载，语言名仅保留 Windows 文件名可接受的字符。
export function codeDownloadName(language?: string) {
  return `file.${language?.replace(/[^a-z0-9+#.-]/gi, "") || "txt"}`
}
