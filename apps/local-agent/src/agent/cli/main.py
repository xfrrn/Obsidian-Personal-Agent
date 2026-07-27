"""最小交互式终端入口。"""

from __future__ import annotations

import argparse
import asyncio

from agent.config.settings import Settings
from agent.core.loop import start_agent
from agent.core.turn.public_events import PublicEventAdapter
from agent.utils.logging import configure_logging
from agent.protocol.event import EventKind
from agent.protocol.mode import ModeKind
from agent.protocol.op import ResolveApproval, UserInput
from agent.storage import SessionStore


async def console(session_id: str | None = None) -> None:
    """CLI 仅负责输入输出，所有运行时逻辑留在 core/。"""

    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_format)
    store = SessionStore(settings.session_db_path)
    if session_id is None:
        stored = await asyncio.to_thread(store.create, settings.model, settings.workspace)
        session_id = stored.id
    else:
        stored = await asyncio.to_thread(store.load, session_id)
        if stored is None:
            raise ValueError(f"会话不存在或已归档: {session_id}")
    mode = stored.mode
    handle, runner = await start_agent(
        settings, session_id=session_id, store=store
    )
    events = PublicEventAdapter()
    handle.turn_events.subscribe(events)
    print(
        f"Agent 已启动（会话 {session_id}，mode={mode.value}）。"
        "输入 /plan、/default 切换，/exit 退出。"
    )
    printer = asyncio.create_task(_print_events(events), name="agent-console-events")
    try:
        while True:
            text = (await asyncio.to_thread(input, f"you[{mode.value}]> ")).strip()
            if text == "/exit":
                break
            if not text:
                continue
            command, _, inline_text = text.partition(" ")
            if command in {"/plan", "/default"}:
                mode = (
                    ModeKind.PLAN if command == "/plan" else ModeKind.DEFAULT
                )
                text = inline_text.strip()
                print(f"[mode] 已切换到 {mode.value}")
                if not text:
                    continue
            parts = text.split(maxsplit=2)
            if parts[0] in {"/approve", "/deny"}:
                if len(parts) != 3 or not parts[1].isdigit():
                    print("用法：/approve <submission_id> <call_id> 或 /deny <submission_id> <call_id>")
                    continue
                handle.submit(
                    ResolveApproval(
                        parts[2],
                        approved=parts[0] == "/approve",
                        target_submission_id=int(parts[1]),
                    )
                )
                continue
            handle.submit(UserInput(text, mode))
    finally:
        handle.shutdown()
        await runner
        await printer


async def _print_events(events: PublicEventAdapter) -> None:
    """持续输出事件，让下一次 input() 能在当前 turn 结束前到达调度器。"""

    while True:
        event = await events.receive()
        if event.kind is EventKind.ASSISTANT_MESSAGE:
            print(f"agent> {event.text}")
        elif event.kind is EventKind.TOOL_CALL:
            print(f"[tool] {event.text}")
        elif event.kind is EventKind.APPROVAL_REQUESTED:
            submission_id = event.data["submission_id"]
            call_id = event.data["call_id"]
            print(
                "[approval] exec_command 请求一次宿主执行权限\n"
                f"  命令：{event.data['command']}\n"
                f"  原因：{event.data['justification']}\n"
                f"  批准：/approve {submission_id} {call_id}\n"
                f"  拒绝：/deny {submission_id} {call_id}"
            )
        elif event.kind is EventKind.TOOL_RESULT:
            print(f"[tool result] {event.text}")
        elif event.kind is EventKind.PLAN_UPDATED:
            if event.text:
                print(f"[plan] {event.text}")
            for step in event.data["plan"]:
                marker = {"completed": "✓", "in_progress": "→", "pending": "·"}[step["status"]]
                print(f"  {marker} {step['step']}")
        elif event.kind is EventKind.ERROR:
            print(f"[error] {event.text}")
        elif event.kind is EventKind.TURN_INTERRUPTED:
            print("[interrupted] 已根据后续输入停止上一轮。")
        elif event.kind is EventKind.SHUTDOWN:
            return


def main() -> None:
    parser = argparse.ArgumentParser(description="启动或恢复终端 Agent 会话")
    parser.add_argument("--session", help="恢复指定会话 ID")
    parser.add_argument("--list-sessions", action="store_true", help="列出已有会话后退出")
    args = parser.parse_args()
    if args.list_sessions:
        settings = Settings.from_env()
        for session in SessionStore(settings.session_db_path).list():
            print(f"{session.id}\t{session.title}")
        return
    asyncio.run(console(args.session))


if __name__ == "__main__":
    main()
