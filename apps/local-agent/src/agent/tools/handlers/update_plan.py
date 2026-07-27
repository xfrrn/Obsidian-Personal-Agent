"""更新执行过程中的当前任务计划。"""

from __future__ import annotations

from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import PlanUpdate, ToolExecution, ToolSpec


class UpdatePlanTool:
    """校验并返回完整计划快照；持久化由 AgentLoop 统一完成。"""

    supports_parallel_tool_calls = False
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="update_plan",
        description=(
            "更新当前任务计划。每次调用都必须提交完整计划；每一步状态只能是 "
            "pending、in_progress 或 completed，且最多一个步骤为 in_progress。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "explanation": {
                    "type": "string",
                    "description": "可选的计划变更原因。",
                },
                "plan": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string", "minLength": 1},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["step", "status"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["plan"],
            "additionalProperties": False,
        },
    )

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolExecution:
        if mode is ModeKind.PLAN:
            raise PermissionError("update_plan 在 Plan Mode 中不可用")
        return ToolExecution("Plan updated", plan_update=PlanUpdate.parse(arguments))
