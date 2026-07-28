import styles from "./styles.css?inline"
import "./styles.css"
import type { HostState } from "./host"
import { mountAgentApp } from "./mount"

const previewHost: HostState = {
  theme: {
    mode: "light",
    isDark: false,
    tokens: {
      backgroundPrimary: "", backgroundSecondary: "", backgroundHover: "", border: "",
      textNormal: "", textMuted: "", textAccent: "", textOnAccent: "", textError: "",
      textSuccess: "", fontInterface: "", fontText: "",
    },
  },
  context: { workspace: "", activeFile: null },
}

mountAgentApp(document.getElementById("root")!, {
  agentUrl: "",
  hostState: previewHost,
  onSettingsChange: () => undefined,
}, styles)
