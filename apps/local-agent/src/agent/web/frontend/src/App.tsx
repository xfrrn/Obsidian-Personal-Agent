import { FormEvent, KeyboardEvent, useEffect, useLayoutEffect, useRef, useState } from "react"
import { Streamdown } from "streamdown"
import {
  Archive,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  CircleDot,
  FileText,
  Menu,
  Moon,
  Plus,
  RefreshCw,
  SendHorizontal,
  ShieldAlert,
  Sun,
  Trash2,
  Undo2,
  X,
} from "lucide-react"
import {
  applyThemeTokens,
  clearThemeTokens,
  SandboxMode,
  subscribeToObsidian,
  ThemeMode,
  updateObsidianSettings,
} from "./obsidian-bridge"

type ModeKind = "default" | "plan"
type MessageRole = "user" | "assistant" | "error" | "notice"
type ToolState = "running" | "success" | "error" | "interrupted"
type PlanStepStatus = "pending" | "in_progress" | "completed"

type PlanState = {
  explanation?: string
  plan: Array<{ step: string; status: PlanStepStatus }>
}

type FileChange = {
  path: string
  added: number
  deleted: number
  binary: boolean
  reverted: boolean
  diff?: string
  diff_truncated?: boolean
}

type ChangeSet = {
  id: string
  submission_id: number
  created_at: number
  added: number
  deleted: number
  files: FileChange[]
}

type AgentEvent = {
  kind: string
  text: string
  data: { delta?: boolean; replace?: boolean; is_final?: boolean; reasoning?: string; arguments?: unknown; is_error?: boolean; status?: Exclude<ToolState, "running">; name?: string; submission_id?: number; call_id?: string; command?: string; justification?: string; mode?: ModeKind; plan?: PlanState["plan"]; id?: string; created_at?: number; added?: number; deleted?: number; files?: FileChange[] }
}

type ChatMessage = {
  id: string
  role: MessageRole
  text: string
  createdAt: number
  streaming?: boolean
}

type Conversation = {
  id: string
  title: string
  created_at: number
  updated_at: number
  model: string
  workspace: string
  last_turn_state: string
  mode: ModeKind
  plan: PlanState | null
}

type ConversationDetail = {
  session: Conversation
  messages: Array<{ id: string; role: "user" | "assistant"; text: string; created_at: number }>
  changes: ChangeSet[]
}

type QueuedMessage = {
  id: string
  text: string
  mode: ModeKind
}

type ApprovalRequest = {
  callId: string
  submissionId: number
  toolName: string
  command: string
  justification: string
  resolving?: boolean
  error?: string
}

type ToolTraceStep = { id: string; kind: "tool"; name: string; label: string; state: ToolState }
type TraceStep = { id: string; kind: "thought"; text: string } | ToolTraceStep
type TraceGroup = Extract<TraceStep, { kind: "thought" }> | { id: string; kind: "tools"; tools: ToolTraceStep[] }

type RunTrace = {
  id: string
  startedAt: number
  steps: TraceStep[]
  responseStarted?: boolean
  completedAt?: number
}

type ConversationView = {
  messages: ChatMessage[]
  traces: RunTrace[]
  changes: ChangeSet[]
}

type TimelineEntry =
  | { kind: "message"; at: number; message: ChatMessage }
  | { kind: "trace"; at: number; trace: RunTrace }
  | { kind: "change"; at: number; change: ChangeSet }

type RuntimePermissions = {
  sandbox_mode: SandboxMode
  approval_policy: "never" | "on-request"
  shell_enabled: boolean
  sandbox_backend: string
  sandbox_network: string
}

const markdownClassName = "text-[15px] leading-7 [&_[data-streamdown=code-block]]:![content-visibility:visible] [&_[data-streamdown=code-block]]:![contain-intrinsic-size:auto]"
const messageClasses: Record<MessageRole, string> = {
  assistant: "",
  user: "ml-auto justify-end",
  notice: "ml-[31px] max-w-[740px]",
  error: "ml-[31px] max-w-[740px]",
}
const messageBodyClasses: Record<MessageRole, string> = {
  assistant: "max-w-[calc(100%-31px)] [overflow-wrap:anywhere]",
  user: "max-w-[min(100%,620px)] rounded-[10px] border border-border bg-muted px-[13px] py-2.5 text-sm leading-[1.55] text-foreground",
  notice: "w-full rounded border-l-2 border-border bg-muted px-3 py-2.5 text-xs text-muted-foreground",
  error: "w-full rounded border-l-2 border-red-500 bg-muted px-3 py-2.5 text-xs text-red-600 dark:text-red-300",
}
function id(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`
}

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  const remaining = seconds % 60
  return minutes ? `${minutes}m ${remaining}s` : `${remaining}s`
}

async function requestJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `请求失败：${response.status}`)
  return data as T
}

async function streamEvents(sessionId: string, text: string, mode: ModeKind, onEvent: (event: AgentEvent) => void) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, mode }),
  })
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.error || `请求失败：${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let pending = ""
  while (true) {
    const { done, value } = await reader.read()
    pending += decoder.decode(value || new Uint8Array(), { stream: !done })
    let boundary = pending.indexOf("\n\n")
    while (boundary !== -1) {
      const frame = pending.slice(0, boundary)
      pending = pending.slice(boundary + 2)
      const payload = frame.split("\n").find((line) => line.startsWith("data:"))
      if (payload) onEvent(JSON.parse(payload.slice(5)) as AgentEvent)
      boundary = pending.indexOf("\n\n")
    }
    if (done) return
  }
}

function groupTraceSteps(steps: TraceStep[]) {
  const groups: TraceGroup[] = []
  for (const step of steps) {
    if (step.kind === "thought") {
      groups.push(step)
      continue
    }
    const previous = groups[groups.length - 1]
    if (previous?.kind === "tools") previous.tools.push(step)
    else groups.push({ id: step.id, kind: "tools", tools: [step] })
  }
  return groups
}

function ToolGroup({ tools }: { tools: ToolTraceStep[] }) {
  const finished = tools.every((tool) => tool.state !== "running")
  const failed = tools.some((tool) => tool.state === "error")
  const interrupted = tools.some((tool) => tool.state === "interrupted")
  const [expanded, setExpanded] = useState(!finished)
  const summary = tools.every((tool) => tool.name === "apply_patch")
    ? tools.length > 1 ? "编辑了多个文件" : "编辑了文件"
    : tools.every((tool) => ["exec_command", "write_stdin"].includes(tool.name))
      ? tools.length > 1 ? "运行了多个命令" : "运行了命令"
      : tools.length > 1 ? "运行了多个工具" : `运行了 ${tools[0].name}`

  return (
    <details className="group/tool mb-2.5" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary className="flex cursor-pointer list-none items-center gap-2 text-[13px] font-semibold [&::-webkit-details-marker]:hidden">
        {finished
          ? failed
            ? <CircleAlert className="size-4 text-red-600" />
            : interrupted
              ? <CircleAlert className="size-4 text-muted-foreground" />
              : <CheckCircle2 className="size-4 text-success" />
          : <CircleDot className="size-4 animate-spin" />}
        <span>{summary}</span><ChevronRight className="size-3.5 text-muted-foreground transition-transform group-open/tool:rotate-90" aria-hidden="true" />
      </summary>
      <div className="grid gap-0.5 pt-1.5 pl-6">
        {tools.map((tool) => (
          <div className="flex min-w-0 items-center gap-2 py-[7px] text-[13px] text-muted-foreground" key={tool.id}>
            {tool.state === "running"
              ? <CircleDot className="size-4 shrink-0 animate-spin" />
              : tool.state === "success"
                ? <CheckCircle2 className="size-4 shrink-0 text-success" />
                : <CircleAlert className={`size-4 shrink-0 ${tool.state === "error" ? "text-red-600" : "text-muted-foreground"}`} />}
            <span>{tool.state === "running" ? "正在运行" : tool.state === "success" ? "已完成" : tool.state === "interrupted" ? "已中断" : "运行失败"}</span>
            <code className="truncate font-mono text-[13px] leading-[1.3] text-muted-foreground">{tool.label}</code>
          </div>
        ))}
      </div>
    </details>
  )
}

function RunTrace({ trace }: { trace: RunTrace }) {
  const [elapsed, setElapsed] = useState(() => Math.floor((Date.now() - trace.startedAt) / 1000))
  const [expanded, setExpanded] = useState(!trace.completedAt)

  useLayoutEffect(() => {
    if (trace.responseStarted || trace.completedAt) {
      setExpanded(false)
    } else if (trace.responseStarted === false) {
      setExpanded(true)
    }
  }, [trace.responseStarted, trace.completedAt])

  useEffect(() => {
    if (trace.completedAt) {
      setElapsed(Math.floor((trace.completedAt - trace.startedAt) / 1000))
      return
    }
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - trace.startedAt) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [trace.startedAt, trace.completedAt])

  const groups = groupTraceSteps(trace.steps)

  return (
    <details className="group/trace mb-[26px] max-w-[760px] text-muted-foreground" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)} aria-live="polite">
      <summary className="flex cursor-pointer list-none items-center gap-1 border-b border-border pb-2.5 text-[13px] font-semibold [&::-webkit-details-marker]:hidden">
        已处理 {formatElapsed(elapsed)}<ChevronRight className="size-[15px] transition-transform group-open/trace:rotate-90" aria-hidden="true" />
      </summary>
      <div className="pt-[18px]">
        {groups.length ? groups.map((group) => group.kind === "thought" ? (
          <Streamdown className={markdownClassName} isAnimating={!trace.completedAt} key={group.id} mode={trace.completedAt ? "static" : "streaming"}>{group.text}</Streamdown>
        ) : (
          <ToolGroup key={`${group.id}:${group.tools.every((tool) => tool.state !== "running")}`} tools={group.tools} />
        )) : !trace.completedAt && (
          <p className="m-0 text-muted-foreground">正在思考</p>
        )}
      </div>
    </details>
  )
}

function PlanPanel({ plan }: { plan: PlanState }) {
  return (
    <details className="agent-plan" aria-label="当前计划">
      <summary>
        <strong>当前计划</strong>
        <span>{plan.plan.filter(({ status }) => status === "completed").length}/{plan.plan.length}</span>
        <ChevronRight aria-hidden="true" />
      </summary>
      {plan.explanation && <p>{plan.explanation}</p>}
      <ol>
        {plan.plan.map((item, index) => (
          <li data-status={item.status} key={`${index}:${item.step}`}>
            <span aria-hidden="true">{item.status === "completed" ? "✓" : item.status === "in_progress" ? "→" : "·"}</span>
            <span>{item.step}</span>
          </li>
        ))}
      </ol>
    </details>
  )
}

function DiffView({ file }: { file: FileChange }) {
  if (file.binary) return <p className="change-binary">二进制文件已修改，无法显示行级差异。</p>
  const lines = (file.diff || "").split("\n")
  return (
    <div className="change-diff" role="region" aria-label={`${file.path} 的改动`}>
      {lines.map((line, index) => {
        const kind = line.startsWith("@@") ? "hunk"
          : line.startsWith("+++") || line.startsWith("---") ? "header"
            : line.startsWith("+") ? "added"
              : line.startsWith("-") ? "deleted"
                : "context"
        return <code data-kind={kind} key={`${index}:${line}`}>{line || " "}</code>
      })}
      {file.diff_truncated && <p>差异过长，只显示前 500 KB。</p>}
    </div>
  )
}

function ChangeSetCard({ sessionId, change, onUpdated }: { sessionId: string; change: ChangeSet; onUpdated: (change: ChangeSet) => void }) {
  const [detail, setDetail] = useState<ChangeSet | null>(null)
  const [openPath, setOpenPath] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [showAll, setShowAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = change.files.filter((file) => !file.reverted)
  const visible = showAll ? change.files : change.files.slice(0, 4)

  const showDiff = async (path: string) => {
    if (openPath === path) {
      setOpenPath(null)
      return
    }
    setError(null)
    try {
      let loaded = detail
      if (!loaded) {
        loaded = await requestJson<ChangeSet>(`/api/sessions/${encodeURIComponent(sessionId)}/changes/${encodeURIComponent(change.id)}`)
        setDetail(loaded)
      }
      setOpenPath(path)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法加载文件差异")
    }
  }

  const undo = async (paths: string[]) => {
    if (!paths.length || !window.confirm(`撤销所选的 ${paths.length} 个文件？\n\n如果文件后来又被修改，操作会安全地拒绝。`)) return
    setBusy(true)
    setError(null)
    try {
      const updated = await requestJson<ChangeSet>(`/api/sessions/${encodeURIComponent(sessionId)}/changes/${encodeURIComponent(change.id)}/undo`, { paths })
      setDetail(updated)
      setSelected(new Set())
      onUpdated(updated)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "撤销失败")
    } finally {
      setBusy(false)
    }
  }

  const detailedFile = detail?.files.find((file) => file.path === openPath)
  return (
    <article className="change-card">
      <header>
        <FileText aria-hidden="true" />
        <div>
          <strong>{active.length ? `已编辑 ${change.files.length} 个文件` : `已撤销 ${change.files.length} 个文件`}</strong>
          <span><b>+{change.added}</b> <i>-{change.deleted}</i></span>
        </div>
        <button type="button" disabled={busy || !selected.size} onClick={() => void undo([...selected])}><Undo2 />撤销所选</button>
      </header>
      <div className="change-files">
        {visible.map((file) => (
          <div className="change-file" data-reverted={file.reverted} key={file.path}>
            <input
              type="checkbox"
              aria-label={`选择 ${file.path}`}
              checked={selected.has(file.path)}
              disabled={file.reverted || busy}
              onChange={(event) => setSelected((current) => {
                const next = new Set(current)
                if (event.target.checked) next.add(file.path)
                else next.delete(file.path)
                return next
              })}
            />
            <button className="change-file-name" type="button" onClick={() => void showDiff(file.path)} aria-expanded={openPath === file.path}>
              <span>{file.path}</span>
              <small>{file.reverted ? "已撤销" : file.binary ? "二进制" : <><b>+{file.added}</b> <i>-{file.deleted}</i></>}</small>
              <ChevronRight aria-hidden="true" />
            </button>
            {openPath === file.path && detailedFile && <DiffView file={detailedFile} />}
          </div>
        ))}
      </div>
      <footer>
        {change.files.length > 4 && <button type="button" onClick={() => setShowAll((value) => !value)}>{showAll ? "收起文件" : `再显示 ${change.files.length - 4} 个文件`}</button>}
        <span />
        {active.length > 0 && <button type="button" disabled={busy} onClick={() => setSelected(new Set(active.map((file) => file.path)))}>全选</button>}
        {active.length > 0 && <button type="button" disabled={busy} onClick={() => void undo(active.map((file) => file.path))}>撤销全部</button>}
      </footer>
      {error && <p className="change-error" role="alert">{error}</p>}
    </article>
  )
}

function Chat({ sessionId, messages, traces, changes, approvals, busy, mode, plan, onChangeUpdated, onModeChange, onResolveApproval, onSend }: { sessionId: string; messages: ChatMessage[]; traces: RunTrace[]; changes: ChangeSet[]; approvals: ApprovalRequest[]; busy: boolean; mode: ModeKind; plan: PlanState | null; onChangeUpdated: (change: ChangeSet) => void; onModeChange: (mode: ModeKind) => void; onResolveApproval: (approval: ApprovalRequest, approved: boolean) => Promise<void>; onSend: (text: string, mode: ModeKind) => Promise<void> }) {
  const [input, setInput] = useState("")
  const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>([])
  const endRef = useRef<HTMLDivElement>(null)
  const timeline: TimelineEntry[] = [
    ...messages.map((message) => ({ kind: "message" as const, at: message.createdAt, message })),
    ...traces.map((trace) => ({ kind: "trace" as const, at: trace.startedAt, trace })),
    ...changes.map((change) => ({ kind: "change" as const, at: change.created_at, change })),
  ].sort((left, right) => left.at - right.at)

  useEffect(() => {
    // 某些浏览器实现会让 scrollIntoView 返回 Promise；effect 只能返回清理函数。
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" })
  }, [messages, traces, changes])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const text = input.trim()
    if (!text) return
    setInput("")
    if (busy) {
      setQueuedMessages((messages) => [...messages, { id: id("queued"), text, mode }])
      return
    }
    await onSend(text, mode)
  }

  const guideMessage = async (message: QueuedMessage) => {
    setQueuedMessages((messages) => messages.filter(({ id }) => id !== message.id))
    await onSend(message.text, message.mode)
  }

  const removeQueuedMessage = (messageId: string) => {
    setQueuedMessages((messages) => messages.filter(({ id }) => id !== messageId))
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {plan && <PlanPanel plan={plan} />}
      <section className="min-h-0 flex-1 overflow-y-auto" aria-live="polite">
        <div className="mx-auto min-h-full w-full max-w-[820px] px-4 pt-5 pb-7">
          {messages.length === 0 && (
            <div className="mx-auto my-[13vh] max-w-[390px] text-center text-muted-foreground">
              <div className="mx-auto mb-[15px] grid size-10 place-items-center rounded-full border border-border bg-background text-foreground">
                <Bot className="w-[21px]" aria-hidden="true" />
              </div>
              <h2 className="mb-1.5 text-[17px] text-foreground">开始一段 Agent 对话</h2>
              <p className="m-0 text-sm leading-[1.6]">输入任务后，回复与执行步骤会按时间线呈现。</p>
            </div>
          )}
          {timeline.map((item) => {
            if (item.kind === "trace") return <RunTrace key={`${item.trace.id}:${item.trace.completedAt ?? "running"}`} trace={item.trace} />
            if (item.kind === "change") return <ChangeSetCard sessionId={sessionId} change={item.change} onUpdated={onChangeUpdated} key={item.change.id} />
            const message = item.message
            return (
              <article key={message.id} className={`mb-[26px] flex max-w-[760px] gap-2.5 ${messageClasses[message.role]}`}>
                {message.role === "assistant" && <Bot className="mt-[3px] size-[21px] shrink-0 text-foreground" aria-hidden="true" />}
                <div className={messageBodyClasses[message.role]}>
                  {message.role === "assistant" ? (
                    <Streamdown className={markdownClassName} isAnimating={Boolean(message.streaming)} mode={message.streaming ? "streaming" : "static"}>{message.text}</Streamdown>
                  ) : (
                    <p className="m-0 whitespace-pre-wrap">{message.text}</p>
                  )}
                </div>
              </article>
            )
          })}
          <div ref={endRef} />
        </div>
      </section>
      {approvals.length > 0 && (
        <section className="mx-auto grid w-full max-w-[820px] gap-2 px-4 pb-1" aria-label="待审批请求">
          {approvals.map((approval) => (
            <article className="rounded-[10px] border border-border bg-background p-3.5 shadow-[0_4px_14px_rgb(0_0_0/.035)]" key={approval.callId}>
              <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold"><ShieldAlert className="size-4 text-amber-600" aria-hidden="true" />需要批准 {approval.toolName}</div>
              <code className="block max-h-28 overflow-auto whitespace-pre-wrap rounded-md bg-muted px-3 py-2 text-xs leading-5 text-foreground">{approval.command || "未提供命令"}</code>
              {approval.justification && <p className="my-2 text-xs leading-5 text-muted-foreground">{approval.justification}</p>}
              {approval.error && <p className="my-2 text-xs text-red-600 dark:text-red-300" role="alert">{approval.error}</p>}
              <div className="mt-2.5 flex justify-end gap-2">
                <button className="rounded-[7px] border border-border bg-transparent px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-accent disabled:opacity-50" disabled={approval.resolving} type="button" onClick={() => void onResolveApproval(approval, false)}>拒绝</button>
                <button className="rounded-[7px] bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50" disabled={approval.resolving} type="button" onClick={() => void onResolveApproval(approval, true)}>{approval.resolving ? "提交中…" : "仅允许本次"}</button>
              </div>
            </article>
          ))}
        </section>
      )}
      {queuedMessages.length > 0 && (
        <section className="mx-auto grid w-full max-w-[820px] gap-2 px-4 pb-1" aria-label="待引导消息">
          {queuedMessages.map((message) => (
            <article className="relative flex min-h-[52px] items-center gap-3 rounded-[10px] border border-border bg-background py-[9px] pr-2.5 pl-3.5 shadow-[0_4px_14px_rgb(0_0_0/.035)] before:text-[15px] before:text-muted-foreground before:content-['⠿']" key={message.id}>
              <p className="m-0 min-w-0 flex-1 truncate text-sm leading-[1.45] text-foreground">{message.text}</p>
              <div className="flex shrink-0 items-center gap-1">
                <button className="cursor-pointer rounded-md bg-transparent px-2 py-1.5 text-[13px] font-semibold text-muted-foreground hover:bg-accent hover:text-foreground" type="button" onClick={() => void guideMessage(message)}>引导</button>
                <button className="grid size-[30px] cursor-pointer place-items-center rounded-md bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground [&>svg]:size-4" type="button" title="删除排队消息" aria-label="删除排队消息" onClick={() => removeQueuedMessage(message.id)}><Trash2 /></button>
              </div>
            </article>
          ))}
        </section>
      )}
      <form className="bg-[var(--page)] px-3 pt-2.5 pb-3" onSubmit={submit}>
        <div className="mx-auto w-full max-w-[820px] rounded-xl border border-border bg-background px-3.5 pt-[13px] pb-2.5 shadow-[0_8px_24px_rgb(0_0_0/.04)]">
          <textarea
            className="min-h-[58px] w-full resize-y border-0 bg-transparent p-0 leading-normal text-foreground outline-0 placeholder:text-muted-foreground"
            aria-label="消息"
            maxLength={20_000}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="给 Agent 下达任务…"
            value={input}
          />
          <div className="flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
            <div className="flex items-center gap-2">
              <select className="rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground outline-none disabled:cursor-not-allowed disabled:opacity-50" aria-label="协作模式" disabled={busy} onChange={(event) => onModeChange(event.target.value as ModeKind)} value={mode}>
                <option value="default">Default</option>
                <option value="plan">Plan</option>
              </select>
              <span className="max-[520px]:hidden">Enter 发送 · Shift + Enter 换行</span>
            </div>
            <button className="inline-flex cursor-pointer items-center gap-[7px] rounded-[7px] bg-primary px-[11px] py-2 text-[13px] font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-45 [&>svg]:size-[15px]" disabled={!input.trim()} type="submit">
              {busy ? <Plus aria-hidden="true" /> : <SendHorizontal aria-hidden="true" />}
              {busy ? "加入队列" : "发送"}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [traces, setTraces] = useState<RunTrace[]>([])
  const [changes, setChanges] = useState<ChangeSet[]>([])
  const [permissions, setPermissions] = useState<RuntimePermissions | null>(null)
  const [runningTurns, setRunningTurns] = useState<Record<string, number>>({})
  const [pendingApprovals, setPendingApprovals] = useState<Record<string, ApprovalRequest[]>>({})
  const [creatingConversation, setCreatingConversation] = useState(false)
  const [archivingConversationId, setArchivingConversationId] = useState<string | null>(null)
  const [updatingPermissions, setUpdatingPermissions] = useState(false)
  const [conversationVersion, setConversationVersion] = useState(0)
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => {
    const saved = localStorage.getItem("codex-agent-theme")
    return saved === "light" || saved === "dark" ? saved : "system"
  })
  const [hostDark, setHostDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches)
  const [activeFile, setActiveFile] = useState<string | null>(null)
  const [connectionState, setConnectionState] = useState<"connecting" | "online" | "offline">("connecting")
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const initialized = useRef(false)
  const activeConversationIdRef = useRef<string | null>(null)
  const conversationViews = useRef<Record<string, ConversationView>>({})

  const refreshPermissions = async () => {
    try {
      setPermissions(await requestJson<RuntimePermissions>("/api/permissions"))
    } catch {
      // 权限接口不可用时只禁用切换，不影响兼容旧后端的对话能力。
    }
  }

  const refreshConversations = async () => {
    const data = await requestJson<{ sessions: Conversation[] }>("/api/sessions")
    setConversations(data.sessions)
    return data.sessions
  }

  const openConversation = async (sessionId: string) => {
    activeConversationIdRef.current = sessionId
    setActiveConversationId(sessionId)
    setConversationVersion((version) => version + 1)
    setSidebarOpen(false)
    const cached = conversationViews.current[sessionId]
    if (cached) {
      setMessages(cached.messages)
      setTraces(cached.traces)
      setChanges(cached.changes)
      return
    }
    setMessages([])
    setTraces([])
    setChanges([])
    const detail = await requestJson<ConversationDetail>(`/api/sessions/${encodeURIComponent(sessionId)}`)
    setConversations((current) => current.map((item) => item.id === sessionId ? detail.session : item))
    const view: ConversationView = {
      messages: detail.messages.map((message) => ({
        id: message.id,
        role: message.role,
        text: message.text,
        createdAt: message.created_at,
      })),
      traces: [],
      changes: detail.changes,
    }
    conversationViews.current[sessionId] = view
    if (activeConversationIdRef.current !== sessionId) return
    setMessages(view.messages)
    setTraces(view.traces)
    setChanges(view.changes)
  }

  const newConversation = async () => {
    setCreatingConversation(true)
    try {
      const conversation = await requestJson<Conversation>("/api/sessions", {})
      setConversations((current) => [conversation, ...current])
      activeConversationIdRef.current = conversation.id
      setActiveConversationId(conversation.id)
      conversationViews.current[conversation.id] = { messages: [], traces: [], changes: [] }
      setMessages([])
      setTraces([])
      setChanges([])
      setConversationVersion((version) => version + 1)
      setSidebarOpen(false)
      setConnectionState("online")
      return true
    } catch (error) {
      setConnectionState("offline")
      setMessages([{ id: id("error"), role: "error", text: error instanceof Error ? error.message : "无法创建新对话", createdAt: Date.now() }])
      return false
    } finally {
      setCreatingConversation(false)
    }
  }

  const clearSessionApprovals = (sessionId: string) => {
    setPendingApprovals((current) => {
      const next = { ...current }
      delete next[sessionId]
      return next
    })
  }

  const archiveConversation = async (conversation: Conversation) => {
    if (archivingConversationId || runningTurns[conversation.id]) return
    if (!window.confirm(`归档会话“${conversation.title}”？\n\n归档后会从会话列表隐藏。`)) return

    setArchivingConversationId(conversation.id)
    try {
      await requestJson(`/api/sessions/${encodeURIComponent(conversation.id)}/archive`, {})
      const remaining = conversations.filter(({ id: sessionId }) => sessionId !== conversation.id)
      delete conversationViews.current[conversation.id]
      clearSessionApprovals(conversation.id)
      setConversations(remaining)

      if (activeConversationIdRef.current === conversation.id) {
        activeConversationIdRef.current = null
        setActiveConversationId(null)
        setMessages([])
        setTraces([])
        setChanges([])
        setConversationVersion((version) => version + 1)
        if (remaining.length) await openConversation(remaining[0].id)
        else await newConversation()
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "无法归档会话")
    } finally {
      setArchivingConversationId(null)
    }
  }

  const resolveApproval = async (sessionId: string, approval: ApprovalRequest, approved: boolean) => {
    setPendingApprovals((current) => ({
      ...current,
      [sessionId]: (current[sessionId] || []).map((item) => item.callId === approval.callId
        ? { ...item, resolving: true, error: undefined }
        : item),
    }))
    try {
      await requestJson(`/api/sessions/${encodeURIComponent(sessionId)}/approvals`, {
        call_id: approval.callId,
        approved,
        submission_id: approval.submissionId,
      })
      setPendingApprovals((current) => ({
        ...current,
        [sessionId]: (current[sessionId] || []).filter((item) => item.callId !== approval.callId),
      }))
    } catch (error) {
      setPendingApprovals((current) => ({
        ...current,
        [sessionId]: (current[sessionId] || []).map((item) => item.callId === approval.callId
          ? { ...item, resolving: false, error: error instanceof Error ? error.message : "审批答复发送失败" }
          : item),
      }))
    }
  }

  const initialize = async () => {
    setConnectionState("connecting")
    try {
      const existing = await refreshConversations()
      if (existing.length) await openConversation(existing[0].id)
      else if (!await newConversation()) return
      void refreshPermissions()
      setConnectionState("online")
    } catch (error) {
      setConnectionState("offline")
      setMessages([{ id: id("error"), role: "error", text: error instanceof Error ? error.message : "无法连接 Agent", createdAt: Date.now() }])
    }
  }

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true
      void initialize()
    }
    return subscribeToObsidian((state) => {
      if (state.theme.mode === "system") applyThemeTokens(state.theme.tokens)
      else clearThemeTokens()
      setHostDark(state.theme.isDark)
      setThemeMode(state.theme.mode)
      setActiveFile(state.context.activeFile)
    })
  }, [])

  const dark = themeMode === "system" ? hostDark : themeMode === "dark"
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    localStorage.setItem("codex-agent-theme", themeMode)
  }, [dark, themeMode])

  const sendMessage = async (text: string, mode: ModeKind) => {
    const sessionId = activeConversationId
    if (!sessionId) return
    const traceId = id("trace")
    const updateSessionMessages = (update: (messages: ChatMessage[]) => ChatMessage[]) => {
      const view = conversationViews.current[sessionId] ?? { messages: [], traces: [], changes: [] }
      const nextMessages = update(view.messages)
      conversationViews.current[sessionId] = { ...view, messages: nextMessages }
      if (activeConversationIdRef.current === sessionId) setMessages(nextMessages)
    }
    const updateSessionTraces = (update: (traces: RunTrace[]) => RunTrace[]) => {
      const view = conversationViews.current[sessionId] ?? { messages: [], traces: [], changes: [] }
      const nextTraces = update(view.traces)
      conversationViews.current[sessionId] = { ...view, traces: nextTraces }
      if (activeConversationIdRef.current === sessionId) setTraces(nextTraces)
    }
    const updateTrace = (update: (trace: RunTrace) => RunTrace) => {
      updateSessionTraces((traces) => traces.map((trace) => trace.id === traceId ? update(trace) : trace))
    }
    const updateSessionChanges = (update: (changes: ChangeSet[]) => ChangeSet[]) => {
      const view = conversationViews.current[sessionId] ?? { messages: [], traces: [], changes: [] }
      const nextChanges = update(view.changes)
      conversationViews.current[sessionId] = { ...view, changes: nextChanges }
      if (activeConversationIdRef.current === sessionId) setChanges(nextChanges)
    }

    updateSessionMessages((current) => [...current, { id: id("user"), role: "user", text, createdAt: Date.now() }])
    updateSessionTraces((traces) => [...traces, { id: traceId, startedAt: Date.now(), steps: [] }])
    setRunningTurns((current) => ({
      ...current,
      [sessionId]: (current[sessionId] || 0) + 1,
    }))
    let roundMessageId: string | null = null
    let roundText = ""
    let completed = false
    try {
      await streamEvents(sessionId, text, mode, (event) => {
        if (event.kind === "turn_started" && event.data.mode) {
          setConversations((current) => current.map((conversation) =>
            conversation.id === sessionId ? { ...conversation, mode: event.data.mode! } : conversation
          ))
        }
        if (event.kind === "assistant_message") {
          if (event.data.delta) {
            roundText += event.text
            if (!roundMessageId) {
              const messageId = id("assistant")
              roundMessageId = messageId
              updateTrace((trace) => ({ ...trace, responseStarted: true }))
              updateSessionMessages((current) => [...current, {
                id: messageId,
                role: "assistant",
                text: event.text,
                createdAt: Date.now(),
                streaming: true,
              }])
            } else {
              const messageId = roundMessageId
              updateSessionMessages((current) => current.some((message) => message.id === messageId)
                ? current.map((message) => message.id === messageId
                  ? { ...message, text: message.text + event.text }
                  : message)
                : [...current, { id: messageId, role: "assistant", text: roundText, createdAt: Date.now(), streaming: true }])
            }
            return
          }
          const responseText = event.text || roundText
          if (event.data.reasoning) {
            updateTrace((trace) => ({ ...trace, steps: [...trace.steps, { id: id("trace-thought"), kind: "thought", text: event.data.reasoning! }] }))
          }
          if (event.data.is_final) {
            updateTrace((trace) => ({ ...trace, responseStarted: true }))
            if (roundMessageId) {
              const messageId = roundMessageId
              updateSessionMessages((current) => current.some((message) => message.id === messageId)
                ? current.map((message) => message.id === messageId
                  ? { ...message, text: responseText, streaming: false }
                  : message)
                : responseText
                  ? [...current, { id: messageId, role: "assistant", text: responseText, createdAt: Date.now() }]
                  : current)
            } else if (responseText) {
              updateSessionMessages((current) => [...current, { id: id("assistant"), role: "assistant", text: responseText, createdAt: Date.now() }])
            }
            roundMessageId = null
            roundText = ""
            return
          }
          if (roundMessageId) {
            const messageId = roundMessageId
            updateSessionMessages((current) => current.filter((message) => message.id !== messageId))
          }
          updateTrace((trace) => ({
            ...trace,
            responseStarted: false,
            steps: responseText ? [...trace.steps, { id: id("trace-thought"), kind: "thought", text: responseText }] : trace.steps,
          }))
          roundMessageId = null
          roundText = ""
        }
        if (event.kind === "tool_call") {
          const command = (event.data.arguments as { command?: unknown } | undefined)?.command
          updateTrace((trace) => ({
            ...trace,
            steps: [...trace.steps, { id: event.data.call_id || id("trace-tool"), kind: "tool", name: event.text, label: typeof command === "string" ? command : event.text, state: "running" }],
          }))
        }
        if (event.kind === "approval_requested" && event.data.call_id && event.data.submission_id) {
          const approval: ApprovalRequest = {
            callId: event.data.call_id,
            submissionId: event.data.submission_id,
            toolName: event.data.name || "工具",
            command: event.data.command || "",
            justification: event.data.justification || "",
          }
          setPendingApprovals((current) => ({
            ...current,
            [sessionId]: (current[sessionId] || []).some(({ callId }) => callId === approval.callId)
              ? current[sessionId]
              : [...(current[sessionId] || []), approval],
          }))
        }
        if (event.kind === "tool_result") {
          updateTrace((trace) => {
            const pending = event.data.call_id
              ? trace.steps.find((step) => step.kind === "tool" && step.id === event.data.call_id)
              : [...trace.steps].reverse().find((step) => step.kind === "tool" && step.state === "running" && (!event.data.name || step.name === event.data.name))
            return pending ? {
              ...trace,
              steps: trace.steps.map((step) => step.id === pending.id && step.kind === "tool" ? { ...step, state: event.data.status === "interrupted" ? "interrupted" : event.data.is_error ? "error" : "success" } : step),
            } : trace
          })
        }
        if (event.kind === "plan_updated" && Array.isArray(event.data.plan)) {
          const plan: PlanState = { plan: event.data.plan, ...(event.text ? { explanation: event.text } : {}) }
          setConversations((current) => current.map((conversation) =>
            conversation.id === sessionId ? { ...conversation, plan } : conversation
          ))
        }
        if (event.kind === "file_changes" && event.data.id && event.data.created_at && Array.isArray(event.data.files)) {
          const change = event.data as ChangeSet
          updateSessionChanges((current) => current.some(({ id: changeId }) => changeId === change.id)
            ? current.map((item) => item.id === change.id ? change : item)
            : [...current, change])
        }
        if (event.kind === "turn_finished") {
          clearSessionApprovals(sessionId)
          completed = true
          const completedAt = Date.now()
          updateTrace((trace) => ({ ...trace, completedAt }))
        }
        if (event.kind === "error") {
          clearSessionApprovals(sessionId)
          updateSessionTraces((traces) => traces.filter((trace) => trace.id !== traceId))
          if (roundMessageId) {
            const messageId = roundMessageId
            updateSessionMessages((current) => current.map((message) => message.id === messageId ? { ...message, streaming: false } : message))
          }
          updateSessionMessages((current) => [...current, { id: id("error"), role: "error", text: event.text, createdAt: Date.now() }])
        }
        if (event.kind === "turn_interrupted") {
          clearSessionApprovals(sessionId)
          updateSessionTraces((traces) => traces.filter((trace) => trace.id !== traceId))
          if (roundMessageId) {
            const messageId = roundMessageId
            updateSessionMessages((current) => current.map((message) => message.id === messageId ? { ...message, streaming: false } : message))
          }
        }
        if (event.kind === "shutdown") {
          clearSessionApprovals(sessionId)
          updateSessionTraces((traces) => traces.filter((trace) => trace.id !== traceId))
          updateSessionMessages((current) => [...current, { id: id("notice"), role: "notice", text: "Agent 运行时已关闭。", createdAt: Date.now() }])
        }
      })
    } catch (error) {
      updateSessionTraces((traces) => traces.filter((trace) => trace.id !== traceId))
      updateSessionMessages((current) => [...current, { id: id("error"), role: "error", text: error instanceof Error ? error.message : "请求失败", createdAt: Date.now() }])
    } finally {
      if (!completed) {
        updateSessionTraces((traces) => traces.filter((trace) => trace.id !== traceId))
        if (roundMessageId) {
          const messageId = roundMessageId
          updateSessionMessages((current) => current.map((message) => message.id === messageId ? { ...message, streaming: false } : message))
        }
      }
      setRunningTurns((current) => {
        const next = { ...current }
        if (next[sessionId] > 1) next[sessionId] -= 1
        else delete next[sessionId]
        return next
      })
      void refreshConversations()
    }
  }

  const busy = activeConversationId !== null && Boolean(runningTurns[activeConversationId])
  const runningConversationCount = Object.keys(runningTurns).length
  const anyBusy = runningConversationCount > 0
  const activeConversation = conversations.find(({ id }) => id === activeConversationId)
  const updateVisibleChangeSet = (updated: ChangeSet) => {
    const sessionId = activeConversationIdRef.current
    if (!sessionId) return
    const view = conversationViews.current[sessionId]
    if (!view) return
    const nextChanges = view.changes.map((change) => change.id === updated.id ? updated : change)
    conversationViews.current[sessionId] = { ...view, changes: nextChanges }
    setChanges(nextChanges)
  }
  const changeSandboxMode = async (sandboxMode: SandboxMode) => {
    if (!permissions || sandboxMode === permissions.sandbox_mode) return
    if (anyBusy) {
      window.alert("运行中的回合结束后才能切换权限")
      return
    }
    const confirmed = sandboxMode !== "danger-full-access" || window.confirm(
      "切换到完全访问后，Agent 工具可以直接使用宿主用户权限。确定仅为当前服务进程启用吗？",
    )
    if (!confirmed) return

    setUpdatingPermissions(true)
    try {
      const updated = await requestJson<RuntimePermissions>("/api/permissions", {
        sandbox_mode: sandboxMode,
        confirmed: sandboxMode === "danger-full-access",
      })
      setPermissions(updated)
      updateObsidianSettings({ sandboxMode: updated.sandbox_mode })
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "无法切换权限")
    } finally {
      setUpdatingPermissions(false)
    }
  }
  const setActiveMode = (mode: ModeKind) => {
    if (!activeConversationId) return
    setConversations((current) => current.map((conversation) =>
      conversation.id === activeConversationId ? { ...conversation, mode } : conversation
    ))
  }
  const toggleTheme = () => {
    const next: ThemeMode = dark ? "light" : "dark"
    clearThemeTokens()
    setThemeMode(next)
    updateObsidianSettings({ themeMode: next })
  }

  useEffect(() => {
    if (!sidebarOpen) return
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false)
    }
    window.addEventListener("keydown", closeOnEscape)
    return () => window.removeEventListener("keydown", closeOnEscape)
  }, [sidebarOpen])

  return (
    <div className="agent-shell">
      <header className="agent-toolbar">
        <button className="agent-icon-button" onClick={() => setSidebarOpen(true)} type="button" aria-label="打开会话列表"><Menu /></button>
        <div className="agent-title" title={activeConversation?.title}>
          <strong>{activeConversation?.title || "CodeX Agent"}</strong>
          <span>{activeFile || (connectionState === "online" ? "当前知识库" : "Agent 未连接")}</span>
        </div>
        <select
          className="agent-permission"
          aria-label="沙盒权限"
          disabled={!permissions || updatingPermissions || anyBusy}
          onChange={(event) => void changeSandboxMode(event.target.value as SandboxMode)}
          title={permissions ? `审批：${permissions.approval_policy} · 网络：${permissions.sandbox_network}` : "权限接口不可用"}
          value={permissions?.sandbox_mode || ""}
        >
          {!permissions && <option value="">权限</option>}
          <option value="read-only">只读</option>
          <option value="workspace-write">可写</option>
          <option value="danger-full-access">完全</option>
        </select>
        <button type="button" className="agent-icon-button" onClick={toggleTheme} aria-label={dark ? "切换到亮色" : "切换到暗色"} title={themeMode === "system" ? "当前跟随 Obsidian；点击后固定主题" : "切换主题"}>{dark ? <Sun /> : <Moon />}</button>
        <button type="button" className="agent-icon-button" onClick={() => void newConversation()} disabled={creatingConversation} aria-label="新对话"><Plus /></button>
      </header>

      <aside className={`session-drawer ${sidebarOpen ? "is-open" : ""}`} aria-hidden={!sidebarOpen}>
        <div className="session-drawer-header">
          <strong>会话</strong>
          <button className="agent-icon-button" type="button" onClick={() => setSidebarOpen(false)} aria-label="关闭会话列表"><X /></button>
        </div>
        <section className="session-list">
          <div>
            {conversations.map((conversation) => {
              const running = Boolean(runningTurns[conversation.id])
              const archiving = archivingConversationId === conversation.id
              return (
                <div className={`session-row ${conversation.id === activeConversationId ? "is-active" : ""}`} key={conversation.id}>
                  <button
                    className="session-open"
                    onClick={() => void openConversation(conversation.id)}
                    title={conversation.title}
                    type="button"
                  >
                    {conversation.title}
                  </button>
                  <button
                    className="session-archive"
                    disabled={running || archiving}
                    onClick={() => void archiveConversation(conversation)}
                    title={running ? "运行中的会话不能归档" : "归档会话"}
                    type="button"
                    aria-label={`归档会话：${conversation.title}`}
                  >
                    <Archive />
                  </button>
                </div>
              )
            })}
          </div>
        </section>
        <div className="agent-status"><span data-state={connectionState} className={anyBusy ? "is-busy" : ""} /><span>{connectionState === "offline" ? "Agent 未连接" : anyBusy ? `${runningConversationCount} 个会话运行中` : connectionState === "connecting" ? "正在连接" : "Agent 就绪"}</span></div>
      </aside>
      {sidebarOpen && <button className="session-backdrop" type="button" onClick={() => setSidebarOpen(false)} aria-label="关闭会话列表" />}

      <main className="agent-main">
        {connectionState === "offline" && (
          <div className="connection-banner" role="alert">
            <span>无法连接本地 Agent。</span>
            <button type="button" onClick={() => void initialize()}><RefreshCw />重连</button>
          </div>
        )}
        {activeConversationId && <Chat key={conversationVersion} sessionId={activeConversationId} messages={messages} traces={traces} changes={changes} approvals={pendingApprovals[activeConversationId] || []} busy={busy} mode={activeConversation?.mode ?? "default"} plan={activeConversation?.plan ?? null} onChangeUpdated={updateVisibleChangeSet} onModeChange={setActiveMode} onResolveApproval={(approval, approved) => resolveApproval(activeConversationId, approval, approved)} onSend={sendMessage} />}
      </main>
    </div>
  )
}
