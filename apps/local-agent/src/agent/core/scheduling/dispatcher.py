"""将排队的用户输入调度为可取消的回合任务。"""

from __future__ import annotations

import asyncio

from agent.config.loader import build_mode_system_prompt
from agent.core.agent_loop import AgentTurnTask
from agent.core.session import Session
from agent.core.turn.context import TurnContext
from agent.core.turn.running import RunningTask
from agent.protocol.op import UserInput
from agent.skills.injection import collect_explicit_mentions


async def user_input_or_turn(session: Session) -> None:
    """处理一条用户输入：中断旧回合后，启动带配置快照的新回合。"""

    submission = await session.input_queue.get()
    if not isinstance(submission.op, UserInput):
        raise TypeError("InputQueue 只能调度 UserInput")

    if session.active_task is not None:
        await session.active_task.cancel_and_wait()

    session.mode = submission.op.mode or session.mode
    await session.persist_mode()
    skill_snapshot = session.skills_service.snapshot()
    context = TurnContext(
        submission_id=submission.id,
        user_text=submission.op.text,
        system_prompt=build_mode_system_prompt(session.config.system_prompt, session.mode),
        file_references=submission.op.references,
        skill_snapshot=skill_snapshot,
        mentioned_skills=collect_explicit_mentions(submission.op.text, skill_snapshot),
        mode=session.mode,
        unattended_policy=submission.op.unattended_policy,
    )
    session_task = AgentTurnTask(session, context)
    asyncio_task = asyncio.create_task(session_task.run(), name=f"agent-turn-{submission.id}")
    running_task = RunningTask(context, asyncio_task)
    session.active_task = running_task
    asyncio_task.add_done_callback(lambda finished: _clear_active_task(session, running_task, finished))


def _clear_active_task(
    session: Session, running_task: RunningTask, finished: asyncio.Task[None]
) -> None:
    """只有当前任务可以清理自身，避免旧回合覆盖刚启动的新回合状态。"""

    if session.active_task is running_task and running_task.task is finished:
        session.active_task = None
