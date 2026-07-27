"""Run opt-in apply_patch reliability checks against the configured live model."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
import tempfile

from agent.config.loader import load_system_instructions
from agent.config.settings import Settings
from agent.core.loop import start_agent
from agent.core.turn.public_events import PublicEventAdapter
from agent.protocol.event import EventKind
from agent.protocol.op import UserInput


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    initial: dict[str, str]
    prompt: str
    expected: dict[str, str | None]


SCENARIOS = (
    Scenario(
        "update",
        {"config.py": "TIMEOUT = 30\nDEBUG = False\n"},
        "把 config.py 的 TIMEOUT 从 30 改为 45，只做这一项修改并验证结果。",
        {"config.py": "TIMEOUT = 45\nDEBUG = False\n"},
    ),
    Scenario(
        "add_nested",
        {},
        "新增 docs/guide.md，内容严格为两行：第一行 `# Guide`，第二行 `Ready.`。",
        {"docs/guide.md": "# Guide\nReady.\n"},
    ),
    Scenario(
        "append_eof",
        {"CHANGELOG.md": "# Changes\n\n- initial\n"},
        "在 CHANGELOG.md 文件末尾追加一行 `- fixed parser`，保留已有内容。",
        {"CHANGELOG.md": "# Changes\n\n- initial\n- fixed parser\n"},
    ),
    Scenario(
        "multi_hunk",
        {
            "service.py": (
                "def first():\n"
                "    return \"old-first\"\n\n"
                "def untouched():\n"
                "    return True\n\n"
                "def second():\n"
                "    return \"old-second\"\n"
            )
        },
        "修改 service.py：first 返回 `new-first`，second 返回 `new-second`，其他内容不变。",
        {
            "service.py": (
                "def first():\n"
                "    return \"new-first\"\n\n"
                "def untouched():\n"
                "    return True\n\n"
                "def second():\n"
                "    return \"new-second\"\n"
            )
        },
    ),
    Scenario(
        "move",
        {"notes.txt": "# Notes\nkeep\n"},
        "把 notes.txt 移动为 archive/notes.md，并把标题改成 `# Archived Notes`。",
        {"notes.txt": None, "archive/notes.md": "# Archived Notes\nkeep\n"},
    ),
    Scenario(
        "delete",
        {"obsolete.txt": "remove me\n", "keep.txt": "keep me\n"},
        "删除 obsolete.txt，不要修改 keep.txt。",
        {"obsolete.txt": None, "keep.txt": "keep me\n"},
    ),
    Scenario(
        "unicode_whitespace",
        {"message.py": "message = “hello”—ready  \n"},
        "把 message.py 的唯一一行严格替换为纯 ASCII `message = \"goodbye\"-ready`，"
        "并保留一个末尾换行，不要保留尾随空格。",
        {"message.py": 'message = "goodbye"-ready\n'},
    ),
    Scenario(
        "crlf",
        {"settings.ini": "[core]\r\nenabled=false\r\n"},
        "把 settings.ini 中 enabled 的值改为 true，保留文件原有换行风格。",
        {"settings.ini": "[core]\r\nenabled=true\r\n"},
    ),
    Scenario(
        "empty_file",
        {"index.html": ""},
        "填写空文件 index.html，内容严格为一行 `<h1>Hello</h1>`。",
        {"index.html": "<h1>Hello</h1>\n"},
    ),
    Scenario(
        "unique_context",
        {
            "features.ini": (
                "[alpha]\n"
                "enabled = false\n\n"
                "[beta]\n"
                "enabled = false\n"
            )
        },
        "只把 features.ini 的 beta 区段 enabled 改为 true，alpha 保持 false。",
        {
            "features.ini": (
                "[alpha]\n"
                "enabled = false\n\n"
                "[beta]\n"
                "enabled = true\n"
            )
        },
    ),
)


async def _run_scenario(base: Settings, scenario: Scenario, run_number: int) -> tuple[int, int, bool, str]:
    with tempfile.TemporaryDirectory(prefix=f"apply-patch-{run_number:02d}-") as directory:
        workspace = Path(directory)
        for relative, content in scenario.initial.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))

        settings = replace(
            base,
            workspace=workspace,
            system_prompt=load_system_instructions("concise"),
            shell_enabled=True,
        )
        handle, runner = await start_agent(settings)
        events = PublicEventAdapter()
        handle.turn_events.subscribe(events)
        calls = successes = 0
        errors: list[str] = []
        last_patch = ""
        patches: list[str] = []
        try:
            handle.submit(
                UserInput(
                    "完成下面任务。必须使用 apply_patch 执行所有写入、移动和删除；"
                    "exec_command 只允许读取与验证，不能修改文件。\n\n"
                    + scenario.prompt
                )
            )
            while True:
                event = await asyncio.wait_for(events.receive(), timeout=180)
                if event.kind is EventKind.TOOL_CALL and event.text == "apply_patch":
                    calls += 1
                    last_patch = str(event.data.get("arguments", {}).get("patch", ""))
                    patches.append(last_patch)
                elif event.kind is EventKind.TOOL_RESULT and event.data.get("name") == "apply_patch":
                    if event.data.get("is_error"):
                        errors.append(event.text)
                        print(
                            f"FAILURE {scenario.name}: patch={last_patch!r}, error={event.text}",
                            flush=True,
                        )
                    else:
                        successes += 1
                elif event.kind is EventKind.ERROR:
                    errors.append(event.text)
                    break
                elif event.kind in {EventKind.TURN_FINISHED, EventKind.TURN_INTERRUPTED}:
                    break
        finally:
            handle.shutdown()
            await runner

        task_passed = all(
            (not (workspace / relative).exists())
            if expected is None
            else (workspace / relative).is_file()
            and (workspace / relative).read_bytes() == expected.encode("utf-8")
            for relative, expected in scenario.expected.items()
        )
        if not task_passed:
            actual = {
                relative: (
                    (workspace / relative).read_bytes().decode("utf-8", errors="replace")
                    if (workspace / relative).is_file()
                    else None
                )
                for relative in scenario.expected
            }
            errors.append(f"patches={patches!r}; actual={actual!r}")
        return calls, successes, task_passed, " | ".join(errors)


async def _main(runs: int, minimum: float, start: int) -> int:
    base = Settings.from_env()
    if not base.api_key:
        raise RuntimeError("OPENAI_API_KEY 未配置")

    total_calls = total_successes = passed_tasks = 0
    for index in range(runs):
        scenario_index = start + index
        scenario = SCENARIOS[scenario_index % len(SCENARIOS)]
        calls, successes, task_passed, error = await _run_scenario(
            base, scenario, scenario_index + 1
        )
        total_calls += calls
        total_successes += successes
        passed_tasks += task_passed
        print(
            f"{index + 1:02d} {scenario.name}: "
            f"apply_patch={successes}/{calls}, task={'PASS' if task_passed else 'FAIL'}"
            + (f", error={error}" if error else ""),
            flush=True,
        )

    success_rate = total_successes / total_calls if total_calls else 0.0
    print(
        f"SUMMARY calls={total_successes}/{total_calls} rate={success_rate:.3f} "
        f"tasks={passed_tasks}/{runs}",
        flush=True,
    )
    return 0 if total_calls and success_rate > minimum and passed_tasks == runs else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--minimum", type=float, default=0.9)
    parser.add_argument("--start", type=int, default=0)
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(_main(arguments.runs, arguments.minimum, arguments.start)))
