"""操作提交包装，独立于操作本身以保持协议职责单一。"""

from __future__ import annotations

from dataclasses import dataclass

from agent.protocol.op import Op


@dataclass(frozen=True, slots=True)
class Submission:
    """为一次操作附加单调递增编号，便于调度和事件关联。"""

    id: int
    op: Op
