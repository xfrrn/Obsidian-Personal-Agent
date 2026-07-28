import { StrictMode } from "react"
import { createRoot, type Root } from "react-dom/client"
import App, { type AgentAppProps } from "./App"

declare const __AGENT_UI_STYLES__: string

export type MountedAgentApp = {
  render(props: AgentAppProps): void
  unmount(): void
}

/** 用 Shadow DOM 保留原前端的完整样式，同时隔离 Obsidian 的全局 CSS。 */
export function mountAgentApp(element: HTMLElement, props: AgentAppProps, styles = __AGENT_UI_STYLES__): MountedAgentApp {
  const shadow = element.attachShadow({ mode: "open" })
  const style = document.createElement("style")
  const mount = document.createElement("div")
  style.textContent = styles
  mount.id = "root"
  shadow.append(style, mount)
  const root: Root = createRoot(mount)
  const render = (next: AgentAppProps) => root.render(<StrictMode><App {...next} /></StrictMode>)
  render(props)
  return { render, unmount: () => root.unmount() }
}
