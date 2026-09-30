"""成本追踪：每跑一次花多少钱。

对照源码：gpt_researcher/utils/costs.py（286 行）
    estimate_llm_cost(71)      按字符数粗估（不精确，但不需要 API 返回 usage）
    calculate_llm_cost(265)    按真实 usage 精确计算
    _extract_usage_tokens(233) 从 response 里挖 token 数
    _get_openai_pricing(257)   模型名 -> 单价

    agent.py:761 get_costs / :769 get_step_costs / :785 add_costs
        —— 看回调是怎么从 LLM 调用一路串到顶层的，这个模式值得抄

注意：DeepSeek 的单价要自己填，原项目表里没有。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 模型名 -> (输入 元/百万token, 输出 元/百万token)
#: TODO(阶段 7)：按 DeepSeek 官网价格填，并注明取值日期
PRICING: dict[str, tuple[float, float]] = {}


@dataclass
class CostTracker:
    """累加每次 LLM 调用的成本，支持按步骤拆分。"""

    total: float = 0.0
    by_step: dict[str, float] = field(default_factory=dict)

    def add(self, model: str, prompt_tokens: int, completion_tokens: int, *, step: str = "default") -> float:
        """记一笔，返回本次花费。

        TODO(阶段 7)：
          1. 从 PRICING 查单价，查不到就警告并用 0
          2. cost = prompt_tokens * 输入单价 + completion_tokens * 输出单价，注意除以 1_000_000
          3. 累加到 self.total 和 self.by_step[step]
        """
        raise NotImplementedError("阶段 7：实现 CostTracker.add")

    def report(self) -> str:
        """输出明细。TODO(阶段 7)：总花费 + 各步骤花费，按金额降序。"""
        raise NotImplementedError("阶段 7：实现 CostTracker.report")