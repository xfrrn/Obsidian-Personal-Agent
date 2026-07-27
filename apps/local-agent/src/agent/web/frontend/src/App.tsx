import { FormEvent, KeyboardEvent, useEffect, useLayoutEffect, useRef, useState } from "react"
import { Streamdown } from "streamdown"
import {
  Activity,
  Archive,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Clock3,
  Command,
  Gauge,
  LayoutDashboard,
  Menu,
  MessageSquareText,
  MoreHorizontal,
  Moon,
  PanelLeftClose,
  Pencil,
  Plus,
  SendHorizontal,
  ShieldAlert,
  Sparkles,
  Sun,
  Trash2,
  Wrench,
  ListX,
} from "lucide-react"

type Tab = "chat" | "monitor"
type ModeKind = "default" | "plan"
type SandboxMode = "read-only" | "workspace-write" | "danger-full-access"
type MessageRole = "user" | "assistant" | "error" | "notice"
type ToolState = "running" | "success" | "error" | "interrupted"
type PlanStepStatus = "pending" | "in_progress" | "completed"

type PlanState = {
  explanation?: string
  plan: Array<{ step: string; status: PlanStepStatus }>
}

type AgentEvent = {
  kind: string
  text: string
  data: { delta?: boolean; replace?: boolean; is_final?: boolean; reasoning?: string; arguments?: unknown; is_error?: boolean; status?: Exclude<ToolState, "running">; name?: string; submission_id?: number; call_id?: string; command?: string; justification?: string; mode?: ModeKind; plan?: PlanState["plan"] }
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
}

type TimelineEntry =
  | { kind: "message"; at: number; message: ChatMessage }
  | { kind: "trace"; at: number; trace: RunTrace }

type Metrics = {
  uptime_seconds: number
  active_turns: number
  turns: {
    started: number
    finished: number
    errored: number
    interrupted: number
    average_latency_ms: number
    last_latency_ms: number | null
  }
  tokens: { prompt: number; completion: number; total: number; requests: number; streamed_responses: number }
  tools: {
    calls: number
    success: number
    error: number
    interrupted: number
    success_rate: number
    by_name: Record<string, { calls: number; success: number; error: number; interrupted: number; success_rate: number }>
  }
  skills: { invocations: number; by_name: Record<string, number> }
  recent: Array<{ kind: string; text: string; at_ms: number }>
}

type RuntimePermissions = {
  sandbox_mode: SandboxMode
  approval_policy: "never" | "on-request"
  shell_enabled: boolean
  sandbox_backend: string
  sandbox_network: string
}

const emptyMetrics: Metrics = {
  uptime_seconds: 0,
  active_turns: 0,
  turns: { started: 0, finished: 0, errored: 0, interrupted: 0, average_latency_ms: 0, last_latency_ms: null },
  tokens: { prompt: 0, completion: 0, total: 0, requests: 0, streamed_responses: 0 },
  tools: { calls: 0, success: 0, error: 0, interrupted: 0, success_rate: 0, by_name: {} },
  skills: { invocations: 0, by_name: {} },
  recent: [],
}

const formatNumber = new Intl.NumberFormat("zh-CN")
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
const panelClassName = "rounded-[9px] border border-border bg-background p-5 max-[780px]:p-[17px]"
const toolTableGridClassName = "grid min-w-[620px] grid-cols-[minmax(150px,1.5fr)_repeat(5,minmax(68px,.55fr))] items-center gap-2.5"

function activityDotClass(kind: string) {
  const color = ["success", "turn_finished", "llm_response"].includes(kind)
    ? "bg-success"
    : ["error", "turn_error"].includes(kind)
      ? "bg-red-500"
      : ["tool_requested", "turn_started"].includes(kind)
        ? "bg-blue-500"
        : "bg-zinc-400"
  return `size-[7px] shrink-0 rounded-full ${color}`
}

function id(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`
}

function formatDuration(milliseconds: number | null) {
  if (milliseconds === null) return "—"
  return milliseconds < 1000 ? `${milliseconds} ms` : `${(milliseconds / 1000).toFixed(1)} s`
}

function formatUptime(seconds: number) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return hours ? `${hours}小时 ${minutes}分` : `${minutes} 分钟`
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

function MetricCard({ icon: Icon, label, value, hint }: { icon: typeof Activity; label: string; value: string; hint: string }) {
  return (
    <section className="min-w-0 rounded-[9px] border border-border bg-background p-4 max-[440px]:p-[13px]">
      <div className="flex items-center justify-between gap-2 text-xs font-semibold text-muted-foreground">
        <span>{label}</span><Icon className="size-[15px]" aria-hidden="true" />
      </div>
      <strong className="mt-3.5 mb-[3px] block truncate text-[22px] tracking-[-.035em] text-foreground max-[440px]:text-[19px]">{value}</strong>
      <small className="block truncate text-[11px] text-muted-foreground">{hint}</small>
    </section>
  )
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
    <aside className="mx-auto mb-3 w-full max-w-[820px] rounded-[9px] border border-border bg-background px-4 py-3" aria-label="当前计划">
      <div className="mb-2 flex items-center justify-between gap-3">
        <strong className="text-[13px]">当前计划</strong>
        <span className="text-[11px] text-muted-foreground">{plan.plan.filter(({ status }) => status === "completed").length}/{plan.plan.length}</span>
      </div>
      {plan.explanation && <p className="mt-0 mb-2 text-xs text-muted-foreground">{plan.explanation}</p>}
      <ol className="m-0 grid list-none gap-1.5 p-0">
        {plan.plan.map((item, index) => (
          <li className={`flex gap-2 text-[13px] ${item.status === "completed" ? "text-muted-foreground line-through" : item.status === "in_progress" ? "font-semibold text-cyan-700 dark:text-cyan-400" : "text-muted-foreground"}`} key={`${index}:${item.step}`}>
            <span className="w-3.5 shrink-0 text-center" aria-hidden="true">{item.status === "completed" ? "✓" : item.status === "in_progress" ? "→" : "·"}</span>
            <span>{item.step}</span>
          </li>
        ))}
      </ol>
    </aside>
  )
}

function Chat({ messages, traces, approvals, busy, mode, plan, onModeChange, onResolveApproval, onSend }: { messages: ChatMessage[]; traces: RunTrace[]; approvals: ApprovalRequest[]; busy: boolean; mode: ModeKind; plan: PlanState | null; onModeChange: (mode: ModeKind) => void; onResolveApproval: (approval: ApprovalRequest, approved: boolean) => Promise<void>; onSend: (text: string, mode: ModeKind) => Promise<void> }) {
  const [input, setInput] = useState("")
  const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>([])
  const [openQueueMenu, setOpenQueueMenu] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const timeline: TimelineEntry[] = [
    ...messages.map((message) => ({ kind: "message" as const, at: message.createdAt, message })),
    ...traces.map((trace) => ({ kind: "trace" as const, at: trace.startedAt, trace })),
  ].sort((left, right) => left.at - right.at)

  useEffect(() => {
    // 某些浏览器实现会让 scrollIntoView 返回 Promise；effect 只能返回清理函数。
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" })
  }, [messages, traces])

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
    setOpenQueueMenu(null)
    await onSend(message.text, message.mode)
  }

  const editQueuedMessage = (message: QueuedMessage) => {
    setInput(message.text)
    setQueuedMessages((messages) => messages.filter(({ id }) => id !== message.id))
    setOpenQueueMenu(null)
  }

  const removeQueuedMessage = (messageId: string) => {
    setQueuedMessages((messages) => messages.filter(({ id }) => id !== messageId))
    setOpenQueueMenu(null)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  return (
    <div className="flex h-[calc(100vh-166px)] min-h-[460px] flex-col overflow-hidden max-[780px]:h-[calc(100vh-148px)]">
      {plan && <PlanPanel plan={plan} />}
      <section className="min-h-0 flex-1 overflow-y-auto" aria-live="polite">
        <div className="mx-auto min-h-full w-full max-w-[820px] px-7 pt-[26px] pb-9 max-[780px]:px-4 max-[780px]:pt-[18px] max-[780px]:pb-7">
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
        <section className="mx-auto grid w-full max-w-[820px] gap-2 px-7 pb-1 max-[780px]:px-4" aria-label="待审批请求">
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
        <section className="mx-auto grid w-full max-w-[820px] gap-2 px-7 pb-1 max-[780px]:px-4" aria-label="待引导消息">
          {queuedMessages.map((message) => (
            <article className="relative flex min-h-[52px] items-center gap-3 rounded-[10px] border border-border bg-background py-[9px] pr-2.5 pl-3.5 shadow-[0_4px_14px_rgb(0_0_0/.035)] before:text-[15px] before:text-muted-foreground before:content-['⠿']" key={message.id}>
              <p className="m-0 min-w-0 flex-1 truncate text-sm leading-[1.45] text-foreground">{message.text}</p>
              <div className="relative flex shrink-0 items-center gap-1">
                <button className="cursor-pointer rounded-md bg-transparent px-2 py-1.5 text-[13px] font-semibold text-muted-foreground hover:bg-accent hover:text-foreground" type="button" onClick={() => void guideMessage(message)}>引导</button>
                <button className="grid size-[30px] cursor-pointer place-items-center rounded-md bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground [&>svg]:size-4" type="button" title="删除排队消息" aria-label="删除排队消息" onClick={() => removeQueuedMessage(message.id)}><Trash2 /></button>
                <button className="grid size-[30px] cursor-pointer place-items-center rounded-md bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground [&>svg]:size-4" type="button" title="更多操作" aria-label="更多操作" aria-expanded={openQueueMenu === message.id} onClick={() => setOpenQueueMenu((current) => current === message.id ? null : message.id)}><MoreHorizontal /></button>
                {openQueueMenu === message.id && (
                  <div className="absolute top-[calc(100%+6px)] right-0 z-[2] grid min-w-[142px] rounded-lg border border-border bg-background p-1 shadow-[0_10px_24px_rgb(0_0_0/.14)]" role="menu">
                    <button className="flex cursor-pointer items-center gap-2 rounded-[5px] bg-transparent p-2 text-left text-[13px] text-muted-foreground hover:bg-accent hover:text-foreground [&>svg]:size-[15px]" role="menuitem" type="button" onClick={() => editQueuedMessage(message)}><Pencil />编辑消息</button>
                    <button className="flex cursor-pointer items-center gap-2 rounded-[5px] bg-transparent p-2 text-left text-[13px] text-muted-foreground hover:bg-accent hover:text-foreground [&>svg]:size-[15px]" role="menuitem" type="button" onClick={() => removeQueuedMessage(message.id)}><ListX />关闭排队</button>
                  </div>
                )}
              </div>
            </article>
          ))}
        </section>
      )}
      <form className="bg-[var(--page)] px-7 pt-3 pb-7 max-[780px]:px-4 max-[780px]:pt-2.5 max-[780px]:pb-[18px]" onSubmit={submit}>
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

function Monitor({ metrics }: { metrics: Metrics }) {
  const skills = Object.entries(metrics.skills.by_name)
  const tools = Object.entries(metrics.tools.by_name)
  const completedTools = metrics.tools.success + metrics.tools.error

  return (
    <div className="grid gap-[18px]">
      <div className="grid grid-cols-6 gap-3 max-[1150px]:grid-cols-3 max-[780px]:grid-cols-2 max-[780px]:gap-2.5">
        <MetricCard icon={Command} label="累计 Token" value={formatNumber.format(metrics.tokens.total)} hint={`${formatNumber.format(metrics.tokens.prompt)} 输入 · ${formatNumber.format(metrics.tokens.completion)} 输出`} />
        <MetricCard icon={Activity} label="运行中回合" value={String(metrics.active_turns)} hint={`累计启动 ${metrics.turns.started} 个回合`} />
        <MetricCard icon={Wrench} label="工具调用" value={String(metrics.tools.calls)} hint={`${metrics.tools.success} 成功 · ${metrics.tools.error} 失败`} />
        <MetricCard icon={CheckCircle2} label="工具成功率" value={`${metrics.tools.success_rate}%`} hint={completedTools ? `${completedTools} 次已完成调用` : "暂无已完成调用"} />
        <MetricCard icon={Sparkles} label="Skill 调用" value={String(metrics.skills.invocations)} hint="按显式注入 Skill 统计" />
        <MetricCard icon={Clock3} label="平均回合耗时" value={formatDuration(metrics.turns.average_latency_ms)} hint={`最近一次 ${formatDuration(metrics.turns.last_latency_ms)}`} />
      </div>

      <div className="grid grid-cols-[1.25fr_.75fr] gap-[18px] max-[780px]:grid-cols-1 max-[780px]:gap-3">
        <section className={panelClassName}>
          <div className="mb-[22px] flex justify-between gap-4">
            <div><h2 className="mb-[5px] text-[15px] tracking-[-.02em]">模型与工具</h2><p className="m-0 text-[13px] leading-normal text-muted-foreground">服务端返回 usage 时才计入 Token。</p></div>
            <Gauge className="size-[17px] text-muted-foreground" aria-hidden="true" />
          </div>
          <div className="flex justify-between text-[13px] text-muted-foreground"><span>工具成功率</span><strong className="text-foreground">{metrics.tools.success_rate}%</strong></div>
          <div className="my-2.5 mb-5 h-[7px] overflow-hidden rounded-full bg-accent"><i className="block h-full min-w-0 rounded-[inherit] bg-foreground transition-[width] duration-250" style={{ width: `${metrics.tools.success_rate}%` }} /></div>
          <dl className="m-0 grid grid-cols-2 gap-3">
            <div className="flex justify-between border-t border-border pt-[11px]"><dt className="text-xs text-muted-foreground">模型请求</dt><dd className="m-0 text-[13px] font-semibold">{metrics.tokens.requests}</dd></div>
            <div className="flex justify-between border-t border-border pt-[11px]"><dt className="text-xs text-muted-foreground">流式响应</dt><dd className="m-0 text-[13px] font-semibold">{metrics.tokens.streamed_responses}</dd></div>
            <div className="flex justify-between border-t border-border pt-[11px]"><dt className="text-xs text-muted-foreground">工具中断</dt><dd className="m-0 text-[13px] font-semibold">{metrics.tools.interrupted}</dd></div>
            <div className="flex justify-between border-t border-border pt-[11px]"><dt className="text-xs text-muted-foreground">失败回合</dt><dd className="m-0 text-[13px] font-semibold">{metrics.turns.errored}</dd></div>
          </dl>
        </section>
        <section className={panelClassName}>
          <div className="mb-[22px] flex justify-between gap-4">
            <div><h2 className="mb-[5px] text-[15px] tracking-[-.02em]">Skill 使用</h2><p className="m-0 text-[13px] leading-normal text-muted-foreground">仅统计用户显式提及并注入模型上下文的 Skill。</p></div>
            <Sparkles className="size-[17px] text-muted-foreground" aria-hidden="true" />
          </div>
          {skills.length
            ? <ul className="m-0 list-none p-0">{skills.map(([name, count]) => <li className="flex items-center justify-between border-t border-border py-2.5 text-[13px]" key={name}><code className="font-mono">${name}</code><span className="text-xs text-muted-foreground">{count} 次</span></li>)}</ul>
            : <p className="m-0 text-[13px] text-muted-foreground">当前还没有使用 Skill。</p>}
        </section>
      </div>

      <section className={panelClassName}>
        <div className="mb-[22px] flex justify-between gap-4">
          <div><h2 className="mb-[5px] text-[15px] tracking-[-.02em]">工具调用明细</h2><p className="m-0 text-[13px] leading-normal text-muted-foreground">按工具名统计调用次数、结果和成功率。</p></div>
          <Wrench className="size-[17px] text-muted-foreground" aria-hidden="true" />
        </div>
        {tools.length ? (
          <div className="overflow-x-auto" role="table" aria-label="工具调用明细">
            <div className={`${toolTableGridClassName} pb-[9px] text-[11px] font-semibold text-muted-foreground`} role="row"><span>工具</span><span>调用</span><span>成功</span><span>失败</span><span>中断</span><span>成功率</span></div>
            {tools.map(([name, tool]) => <div className={`${toolTableGridClassName} border-t border-border py-[11px] text-[13px] text-muted-foreground`} role="row" key={name}><code className="font-mono text-foreground">{name}</code><span>{tool.calls}</span><span>{tool.success}</span><span>{tool.error}</span><span>{tool.interrupted}</span><strong className="text-foreground">{tool.success_rate}%</strong></div>)}
          </div>
        ) : <p className="m-0 text-[13px] text-muted-foreground">当前还没有工具调用。</p>}
      </section>

      <section className={`${panelClassName} min-h-[215px]`}>
        <div className="mb-[22px] flex justify-between gap-4">
          <div><h2 className="mb-[5px] text-[15px] tracking-[-.02em]">运行事件</h2><p className="m-0 text-[13px] leading-normal text-muted-foreground">只保留最近 20 条安全元数据，不包含提示词或工具输出。</p></div>
          <Activity className="size-[17px] text-muted-foreground" aria-hidden="true" />
        </div>
        {metrics.recent.length
          ? <ul className="m-0 grid list-none gap-[11px] p-0">{metrics.recent.map((event, index) => <li className="flex items-center gap-2.5 text-[13px] text-muted-foreground" key={`${event.at_ms}-${index}`}><span className={activityDotClass(event.kind)} /><span>{event.text}</span></li>)}</ul>
          : <p className="m-0 text-[13px] text-muted-foreground">等待 Agent 开始运行。</p>}
      </section>
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState<Tab>("chat")
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [traces, setTraces] = useState<RunTrace[]>([])
  const [metrics, setMetrics] = useState<Metrics>(emptyMetrics)
  const [permissions, setPermissions] = useState<RuntimePermissions | null>(null)
  const [runningTurns, setRunningTurns] = useState<Record<string, number>>({})
  const [pendingApprovals, setPendingApprovals] = useState<Record<string, ApprovalRequest[]>>({})
  const [creatingConversation, setCreatingConversation] = useState(false)
  const [archivingConversationId, setArchivingConversationId] = useState<string | null>(null)
  const [updatingPermissions, setUpdatingPermissions] = useState(false)
  const [conversationVersion, setConversationVersion] = useState(0)
  const [dark, setDark] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const initialized = useRef(false)
  const activeConversationIdRef = useRef<string | null>(null)
  const conversationViews = useRef<Record<string, ConversationView>>({})

  const refreshMetrics = async () => {
    try {
      setMetrics(await requestJson<Metrics>("/api/metrics"))
    } catch {
      // 指标拉取失败不应中断正在进行的对话。
    }
  }

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
      return
    }
    setMessages([])
    setTraces([])
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
    }
    conversationViews.current[sessionId] = view
    if (activeConversationIdRef.current !== sessionId) return
    setMessages(view.messages)
    setTraces(view.traces)
  }

  const newConversation = async () => {
    setCreatingConversation(true)
    try {
      const conversation = await requestJson<Conversation>("/api/sessions", {})
      setConversations((current) => [conversation, ...current])
      activeConversationIdRef.current = conversation.id
      setActiveConversationId(conversation.id)
      conversationViews.current[conversation.id] = { messages: [], traces: [] }
      setMessages([])
      setTraces([])
      setConversationVersion((version) => version + 1)
      setSidebarOpen(false)
      await refreshMetrics()
    } catch (error) {
      setMessages([{ id: id("error"), role: "error", text: error instanceof Error ? error.message : "无法创建新对话", createdAt: Date.now() }])
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

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true
      void (async () => {
        try {
          const existing = await refreshConversations()
          if (existing.length) await openConversation(existing[0].id)
          else await newConversation()
        } catch (error) {
          setMessages([{ id: id("error"), role: "error", text: error instanceof Error ? error.message : "无法加载会话", createdAt: Date.now() }])
        }
      })()
    }
    void refreshMetrics()
    void refreshPermissions()
    const timer = window.setInterval(() => void refreshMetrics(), 3_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
  }, [dark])

  const sendMessage = async (text: string, mode: ModeKind) => {
    const sessionId = activeConversationId
    if (!sessionId) return
    const traceId = id("trace")
    const updateSessionMessages = (update: (messages: ChatMessage[]) => ChatMessage[]) => {
      const view = conversationViews.current[sessionId] ?? { messages: [], traces: [] }
      const nextMessages = update(view.messages)
      conversationViews.current[sessionId] = { ...view, messages: nextMessages }
      if (activeConversationIdRef.current === sessionId) setMessages(nextMessages)
    }
    const updateSessionTraces = (update: (traces: RunTrace[]) => RunTrace[]) => {
      const view = conversationViews.current[sessionId] ?? { messages: [], traces: [] }
      const nextTraces = update(view.traces)
      conversationViews.current[sessionId] = { ...view, traces: nextTraces }
      if (activeConversationIdRef.current === sessionId) setTraces(nextTraces)
    }
    const updateTrace = (update: (trace: RunTrace) => RunTrace) => {
      updateSessionTraces((traces) => traces.map((trace) => trace.id === traceId ? update(trace) : trace))
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
      void refreshMetrics()
    }
  }

  const switchTab = (next: Tab) => {
    setTab(next)
    setSidebarOpen(false)
  }

  const busy = activeConversationId !== null && Boolean(runningTurns[activeConversationId])
  const runningConversationCount = Object.keys(runningTurns).length
  const anyBusy = runningConversationCount > 0
  const activeConversation = conversations.find(({ id }) => id === activeConversationId)
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
      setPermissions(await requestJson<RuntimePermissions>("/api/permissions", {
        sandbox_mode: sandboxMode,
        confirmed: sandboxMode === "danger-full-access",
      }))
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

  return (
    <div className="flex min-h-screen min-w-80 bg-[var(--page)] font-sans text-foreground [--accent:#eaeae7] [--background:#fff] [--border:#dfdfdb] [--card:#fff] [--card-foreground:#20201e] [--foreground:#20201e] [--input:#dfdfdb] [--muted:#f1f1ef] [--muted-foreground:#73736f] [--page:#f7f7f5] [--primary:#20201e] [--primary-foreground:#fff] [--radius:0.625rem] [--success:#23865a] [color-scheme:light] dark:[--accent:#30302d] dark:[--background:#1f1f1d] dark:[--border:#373733] dark:[--card:#1f1f1d] dark:[--card-foreground:#f4f4f0] dark:[--foreground:#f4f4f0] dark:[--input:#373733] dark:[--muted:#292927] dark:[--muted-foreground:#a7a7a1] dark:[--page:#171716] dark:[--primary:#f4f4f0] dark:[--primary-foreground:#1d1d1b] dark:[--success:#58c98a] dark:[color-scheme:dark]">
      <button className="fixed top-3.5 left-3.5 z-[4] hidden size-9 place-items-center rounded-[7px] border border-border bg-background text-foreground max-[780px]:grid [&>svg]:size-[17px]" onClick={() => setSidebarOpen((open) => !open)} type="button" aria-label="切换导航"><Menu /></button>
      <aside className={`flex min-h-screen w-64 shrink-0 flex-col border-r border-border bg-background max-[780px]:fixed max-[780px]:top-0 max-[780px]:left-0 max-[780px]:z-10 max-[780px]:transition-transform max-[780px]:duration-200 ${sidebarOpen ? "max-[780px]:translate-x-0 max-[780px]:shadow-[12px_0_30px_rgb(0_0_0/.12)]" : "max-[780px]:-translate-x-full"}`}>
        <div className="flex h-16 items-center gap-[11px] border-b border-border px-5 text-[17px] font-semibold tracking-[-.02em]">
          <div className="grid size-[30px] place-items-center rounded-lg bg-primary text-primary-foreground"><Command className="w-[17px]" /></div>
          <span>Agent Console</span>
          <button className="ml-auto hidden bg-transparent text-muted-foreground max-[780px]:inline-flex" type="button" onClick={() => setSidebarOpen(false)} aria-label="收起导航"><PanelLeftClose /></button>
        </div>
        <nav className="p-6 px-3">
          <p className="mx-2 mt-0 mb-2 text-[11px] font-bold tracking-[.075em] text-muted-foreground uppercase">工作台</p>
          <button className={`flex w-full cursor-pointer items-center gap-3 rounded-[7px] p-2.5 text-left text-muted-foreground transition-colors hover:bg-accent hover:text-foreground [&>svg]:size-[17px] ${tab === "chat" ? "bg-accent text-foreground" : ""}`} onClick={() => switchTab("chat")} type="button"><MessageSquareText />对话</button>
          <button className={`flex w-full cursor-pointer items-center gap-3 rounded-[7px] p-2.5 text-left text-muted-foreground transition-colors hover:bg-accent hover:text-foreground [&>svg]:size-[17px] ${tab === "monitor" ? "bg-accent text-foreground" : ""}`} onClick={() => switchTab("monitor")} type="button"><LayoutDashboard />运行监控</button>
        </nav>
        <section className="min-h-0 flex-1 border-t border-border px-3 py-4">
          <p className="mx-2 mt-0 mb-2 text-[11px] font-bold tracking-[.075em] text-muted-foreground uppercase">会话</p>
          <div className="grid max-h-full gap-1 overflow-y-auto">
            {conversations.map((conversation) => {
              const running = Boolean(runningTurns[conversation.id])
              const archiving = archivingConversationId === conversation.id
              return (
                <div className={`group flex items-center rounded-[7px] transition-colors hover:bg-accent ${conversation.id === activeConversationId ? "bg-accent" : ""}`} key={conversation.id}>
                  <button
                    className={`min-w-0 flex-1 truncate bg-transparent px-2.5 py-2 text-left text-[13px] hover:text-foreground ${conversation.id === activeConversationId ? "font-semibold text-foreground" : "text-muted-foreground"}`}
                    onClick={() => void openConversation(conversation.id)}
                    title={conversation.title}
                    type="button"
                  >
                    {conversation.title}
                  </button>
                  <button
                    className="mr-1 grid size-7 shrink-0 place-items-center rounded-md bg-transparent text-muted-foreground opacity-0 hover:bg-background hover:text-foreground focus-visible:opacity-100 disabled:cursor-not-allowed disabled:opacity-30 group-hover:opacity-100 max-[780px]:opacity-100 [&>svg]:size-[14px]"
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
        <div className="mt-auto flex items-center gap-[9px] border-t border-border px-[22px] py-[17px] text-xs text-muted-foreground"><span className={`size-2 rounded-full bg-success ${anyBusy ? "animate-pulse" : ""}`} /><span>{anyBusy ? `${runningConversationCount} 个会话运行中` : "Agent 就绪"}</span></div>
      </aside>
      {sidebarOpen && <button className="fixed inset-0 z-[8] hidden bg-black/42 max-[780px]:block" type="button" onClick={() => setSidebarOpen(false)} aria-label="关闭导航" />}
      <main className="min-w-0 flex-1">
        <header className="flex h-16 items-center justify-between border-b border-border bg-background px-6 max-[780px]:pr-4 max-[780px]:pl-[62px]">
          <div className="flex items-center gap-[7px] text-sm max-[780px]:hidden"><span className="text-muted-foreground">Agent Console</span><ChevronRight className="size-[15px] text-muted-foreground" /><strong className="font-medium">{tab === "chat" ? "对话" : "运行监控"}</strong></div>
          <div className="flex items-center gap-2 max-[780px]:ml-auto">
            <select
              className="h-9 rounded-[7px] border border-border bg-background px-2 text-xs font-semibold text-foreground outline-none disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="沙盒权限"
              disabled={!permissions || updatingPermissions || anyBusy}
              onChange={(event) => void changeSandboxMode(event.target.value as SandboxMode)}
              title={permissions ? `审批：${permissions.approval_policy} · 网络：${permissions.sandbox_network}` : "权限接口不可用"}
              value={permissions?.sandbox_mode || ""}
            >
              {!permissions && <option value="">权限不可用</option>}
              <option value="read-only">只读</option>
              <option value="workspace-write">工作区写入</option>
              <option value="danger-full-access">完全访问</option>
            </select>
            <button type="button" className="inline-flex size-9 cursor-pointer items-center justify-center rounded-[7px] bg-transparent text-muted-foreground transition-colors hover:bg-accent hover:text-foreground [&>svg]:size-4" onClick={() => setDark((value) => !value)} aria-label="切换主题">{dark ? <Sun /> : <Moon />}</button>
            <button type="button" className="inline-flex cursor-pointer items-center justify-center gap-[7px] rounded-[7px] border border-border bg-transparent px-[11px] py-[9px] text-[13px] font-semibold text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50 max-[440px]:size-9 max-[440px]:p-0 [&>svg]:size-4" onClick={() => void newConversation()} disabled={creatingConversation} aria-label="新对话"><Plus /><span className="max-[440px]:hidden">新对话</span></button>
          </div>
        </header>
        <div className="mx-auto max-w-[1160px] p-7 max-[780px]:px-4 max-[780px]:py-6">
          <div className="mb-6 flex items-start justify-between gap-4 max-[780px]:mb-[17px]">
            <div><h1 className="mt-0 mb-[7px] text-2xl tracking-[-.035em] max-[780px]:text-[21px]">{tab === "chat" ? activeConversation?.title || "与 Agent 协作" : "Agent 运行监控"}</h1><p className="m-0 text-[13px] leading-normal text-muted-foreground">{tab === "chat" ? "流式接收模型回答，工具调用在同一时间线中可见。" : `进程已运行 ${formatUptime(metrics.uptime_seconds)}，每 3 秒自动刷新。`}</p></div>
            <span className={`mt-1 inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-background px-2 py-1 text-[11px] font-semibold text-muted-foreground before:size-1.5 before:rounded-full before:bg-success before:content-[''] ${busy ? "before:animate-pulse" : ""}`}>{busy ? "生成中" : "实时"}</span>
          </div>
          <div className={tab === "chat" ? "" : "hidden"}>
            <Chat key={conversationVersion} messages={messages} traces={traces} approvals={activeConversationId ? pendingApprovals[activeConversationId] || [] : []} busy={busy} mode={activeConversation?.mode ?? "default"} plan={activeConversation?.plan ?? null} onModeChange={setActiveMode} onResolveApproval={(approval, approved) => activeConversationId ? resolveApproval(activeConversationId, approval, approved) : Promise.resolve()} onSend={sendMessage} />
          </div>
          <div className={tab === "monitor" ? "" : "hidden"}>
            <Monitor metrics={metrics} />
          </div>
        </div>
      </main>
    </div>
  )
}
