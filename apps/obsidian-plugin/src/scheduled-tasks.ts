export type ScheduledTaskAccess = "read-only" | "workspace-write";

export interface ScheduledTaskPermissions {
  access: ScheduledTaskAccess;
  allowWeb: boolean;
}

export interface ScheduledTaskRun {
  startedAt: number;
  finishedAt?: number;
  status: "running" | "success" | "failed";
  error?: string;
}

export interface ScheduledTask {
  id: string;
  name: string;
  enabled: boolean;
  time: string;
  instruction: string;
  permissions: ScheduledTaskPermissions;
  sessionId?: string;
  nextRunAt: number;
  lastRun?: ScheduledTaskRun;
}

const DAILY_TIME = /^([01]\d|2[0-3]):([0-5]\d)$/;

export function nextDailyRun(time: string, from = Date.now()): number {
  const match = DAILY_TIME.exec(time);
  if (!match) throw new Error("执行时间必须是 HH:mm 格式。");
  const next = new Date(from);
  next.setHours(Number(match[1]), Number(match[2]), 0, 0);
  if (next.getTime() <= from) next.setDate(next.getDate() + 1);
  return next.getTime();
}

export function normalizeScheduledTasks(
  value: unknown,
  recoverInterrupted = false
): ScheduledTask[] {
  if (!Array.isArray(value)) return [];
  const now = Date.now();
  return value.flatMap((candidate): ScheduledTask[] => {
    if (!candidate || typeof candidate !== "object") return [];
    const task = candidate as Partial<ScheduledTask>;
    if (
      typeof task.id !== "string" || !task.id.trim()
      || typeof task.name !== "string" || !task.name.trim()
      || typeof task.instruction !== "string" || !task.instruction.trim()
      || typeof task.time !== "string" || !DAILY_TIME.test(task.time)
    ) return [];
    const access = task.permissions?.access === "read-only" ? "read-only" : "workspace-write";
    const lastRun = normalizeLastRun(task.lastRun, now, recoverInterrupted);
    return [{
      id: task.id.trim(),
      name: task.name.trim(),
      enabled: task.enabled !== false,
      time: task.time,
      instruction: task.instruction.trim(),
      permissions: {
        access,
        allowWeb: task.permissions?.allowWeb === true
      },
      ...(typeof task.sessionId === "string" && task.sessionId ? { sessionId: task.sessionId } : {}),
      nextRunAt: finiteTimestamp(task.nextRunAt) ?? nextDailyRun(task.time, now),
      ...(lastRun ? { lastRun } : {})
    }];
  });
}

function normalizeLastRun(
  value: unknown,
  now: number,
  recoverInterrupted: boolean
): ScheduledTaskRun | undefined {
  if (!value || typeof value !== "object") return undefined;
  const run = value as Partial<ScheduledTaskRun>;
  const startedAt = finiteTimestamp(run.startedAt);
  const status = run.status;
  if (!startedAt || (status !== "running" && status !== "success" && status !== "failed")) return undefined;
  if (status === "running" && recoverInterrupted) {
    return {
      startedAt,
      finishedAt: now,
      status: "failed",
      error: "Obsidian 在任务完成前关闭。"
    };
  }
  const finishedAt = finiteTimestamp(run.finishedAt);
  return {
    startedAt,
    ...(finishedAt ? { finishedAt } : {}),
    status,
    ...(typeof run.error === "string" && run.error ? { error: run.error } : {})
  };
}

function finiteTimestamp(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : undefined;
}
