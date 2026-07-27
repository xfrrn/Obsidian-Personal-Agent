"""工具系统的协议无关数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """模型可见的函数工具声明。"""

    name: str
    description: str
    parameters: dict[str, Any]

    def as_openai_function(self) -> dict[str, Any]:
        """将内部声明转换为 OpenAI Chat Completions 所需的 JSON 结构。"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class PlanStep:
    step: str
    status: PlanStepStatus

    def as_dict(self) -> dict[str, str]:
        return {"step": self.step, "status": self.status.value}


@dataclass(frozen=True, slots=True)
class PlanUpdate:
    """一次完整计划快照；每次 update_plan 调用都会替换旧快照。"""

    plan: tuple[PlanStep, ...]
    explanation: str | None = None

    @classmethod
    def parse(cls, value: object) -> PlanUpdate:
        if not isinstance(value, dict):
            raise ValueError("参数必须是对象")
        unknown = set(value) - {"explanation", "plan"}
        if unknown:
            raise ValueError(f"包含未知字段: {', '.join(sorted(unknown))}")

        explanation_value = value.get("explanation")
        if explanation_value is not None and not isinstance(explanation_value, str):
            raise ValueError("explanation 必须是字符串")
        explanation = explanation_value.strip() if explanation_value else None
        if explanation is not None and len(explanation) > 2_000:
            raise ValueError("explanation 不能超过 2000 个字符")

        raw_plan = value.get("plan")
        if not isinstance(raw_plan, list) or not raw_plan:
            raise ValueError("plan 必须是非空数组")
        if len(raw_plan) > 100:
            raise ValueError("plan 不能超过 100 步")

        steps: list[PlanStep] = []
        in_progress = 0
        for index, item in enumerate(raw_plan, 1):
            if not isinstance(item, dict) or set(item) != {"step", "status"}:
                raise ValueError(f"plan[{index}] 必须且只能包含 step 和 status")
            step_value = item["step"]
            if not isinstance(step_value, str) or not step_value.strip():
                raise ValueError(f"plan[{index}].step 必须是非空字符串")
            step = step_value.strip()
            if len(step) > 500:
                raise ValueError(f"plan[{index}].step 不能超过 500 个字符")
            try:
                status = PlanStepStatus(item["status"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"plan[{index}].status 必须是 pending、in_progress 或 completed"
                ) from exc
            in_progress += status is PlanStepStatus.IN_PROGRESS
            steps.append(PlanStep(step, status))

        if in_progress > 1:
            raise ValueError("同一计划最多只能有一个 in_progress 步骤")
        return cls(tuple(steps), explanation)

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"plan": [step.as_dict() for step in self.plan]}
        if self.explanation is not None:
            result["explanation"] = self.explanation
        return result

    @property
    def is_complete(self) -> bool:
        return all(step.status is PlanStepStatus.COMPLETED for step in self.plan)


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """工具执行后的文本结果；错误也保留为模型可纠正的结果。"""

    content: str
    is_error: bool = False
    interrupted: bool = False
    plan_update: PlanUpdate | None = None
