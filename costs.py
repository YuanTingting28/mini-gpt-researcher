"""成本追踪：每跑一次花多少钱。

对照源码：
    gpt_researcher/utils/costs.py
        estimate_llm_cost(71)       按字符数粗估（不精确，但不需要 API 返回 usage）
        calculate_llm_cost(265)     按真实 usage 精确计算      <- 我们只实现这条
        _extract_usage_tokens(233)  从 response 里挖 token 数
        _get_openai_pricing(257)    模型名 -> 单价
    gpt_researcher/agent.py
        :761 get_costs / :769 get_step_costs / :785 add_costs
        看回调是怎么从 LLM 调用一路串到顶层的 —— 我们要的是"分步骤"这一层

我们和上游的三点不同：
  1. 单价是三元组（缓存命中, 缓存未命中, 输出）。DeepSeek 把输入分两档价，
     差 30~50 倍；上游的二元组结构表达不了这件事，它的表里也没有 deepseek。
  2. 区分 peak / off-peak（差 2 倍），默认取 peak —— 宁可高估，不能静默低估。
  3. 找不到单价时记 0 并留痕，在 report() 里显式声明，而不是静默当便宜。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: 单价单位是「每百万 token」。官网按百万报价，照抄能少一次换算错误。
MILLION = 1_000_000

#: 币种、取值日期、出处必须和数字放在一起。
#: 第一版把美元价格标成了「元」，这种错没人能靠读代码发现。
CURRENCY = "USD"
PRICING_AS_OF = "2026-10-09"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"

#: 模型名 -> {时段: (缓存命中输入, 缓存未命中输入, 输出)}，单位 该币种/百万 token
#: 数据来自 DeepSeek 官方 Model Details 价目表。时段的具体划分见该页脚注。
PRICING: dict[str, dict[str, tuple[float, float, float]]] = {
    "deepseek-flash": {
        "off_peak": (0.003, 0.15, 0.6),
        "peak":     (0.006, 0.30, 1.2),
    },
    "deepseek-v4-pro": {
        "off_peak": (0.022, 0.66, 1.98),
        "peak":     (0.044, 1.32, 3.96),
    },
}

#: 默认按 peak 计价：估高了你能看见并去查，估低了你只会以为很便宜
DEFAULT_PERIOD = "peak"

_SYMBOL = {"USD": "$", "CNY": "¥", "RMB": "¥"}.get(CURRENCY, "")


def _money(amount: float) -> str:
    return f"{_SYMBOL}{amount:.5f}"


def _lookup(model: str, period: str = DEFAULT_PERIOD) -> tuple[float, float, float] | None:
    """模型名 + 时段 -> 单价三元组。精确匹配优先，其次最长前缀匹配，都没有返回 None。

    前缀匹配是为了对付 "deepseek-flash-20260101" 这种带日期的名字；
    但绝不做模糊猜测 —— 匹配不到就是没登记，宁可记 0 并报警，
    也不拿别的模型或别的时段的价格顶上去。
    """
    if model in PRICING:
        row = PRICING[model]
    else:
        hits = [name for name in PRICING if model.startswith(name)]
        if not hits:
            return None
        row = PRICING[max(hits, key=len)]
    return row.get(period)


@dataclass
class CostTracker:
    """累加每次 LLM 调用的成本，支持按步骤拆分。"""

    period: str = DEFAULT_PERIOD
    total: float = 0.0
    by_step: dict[str, float] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)
    unpriced: set[str] = field(default_factory=set)

    def add(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        step: str = "default",
        cache_hit_tokens: int = 0,
        reasoning_tokens: int = 0,
        period: str | None = None,
    ) -> float:
        """记一笔，返回本次花费（CURRENCY 单位）。

        reasoning_tokens 只作展示，不参与计费 —— DeepSeek 的思维链 token
        已经算在 completion_tokens 里，再加一次就是重复收费。
        """
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        used_period = period or self.period
        # 命中数不该超过输入总数；超了说明字段对不上，夹住比算错强
        cache_hit = max(0, min(int(cache_hit_tokens or 0), prompt_tokens))

        price = _lookup(model, used_period)
        if price is None:
            self.unpriced.add(model)
            logger.warning(
                "PRICING 表里没有 %r（%s 时段），这次调用按 0 记（合计会被低估）",
                model, used_period,
            )
            cost = 0.0
        else:
            hit_price, miss_price, out_price = price
            miss = prompt_tokens - cache_hit
            cost = (
                hit_price * cache_hit
                + miss_price * miss
                + out_price * completion_tokens
            ) / MILLION

        self.total += cost
        self.by_step[step] = self.by_step.get(step, 0.0) + cost
        self.rows.append(
            {
                "step": step,
                "model": model,
                "period": used_period,
                "prompt_tokens": prompt_tokens,
                "cache_hit_tokens": cache_hit,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": int(reasoning_tokens or 0),
                "cost": cost,
            }
        )
        return cost

    def report(self) -> str:
        """输出明细：逐次调用 + 按步骤汇总，都按金额降序。"""
        if not self.rows:
            return "本次运行没有 LLM 调用记录（llm.USAGE_LOG 是空的）"

        periods = {r["period"] for r in self.rows}
        period_label = "/".join(sorted(periods)) if len(periods) > 1 else self.period
        out = [
            f"成本统计（{CURRENCY}，{period_label} 时段价，取值 {PRICING_AS_OF}）",
            f"单价来源 {PRICING_SOURCE}",
            "",
            f"{'步骤':<8}{'模型':<18}{'输入':>9}{'命中':>9}{'输出':>8}{'费用':>11}",
        ]
        for r in sorted(self.rows, key=lambda r: -r["cost"]):
            out.append(
                f"{r['step']:<8}{r['model']:<18}"
                f"{r['prompt_tokens']:>9,}{r['cache_hit_tokens']:>9,}"
                f"{r['completion_tokens']:>8,}{r['cost']:>11.5f}"
            )

        out += ["", "按步骤汇总："]
        for step, cost in sorted(self.by_step.items(), key=lambda kv: -kv[1]):
            share = cost / self.total * 100 if self.total else 0.0
            out.append(f"  {step:<8} {_money(cost)}   {share:5.1f}%")
        out.append(f"  {'合计':<8} {_money(self.total)}")

        reasoning = sum(r["reasoning_tokens"] for r in self.rows)
        if reasoning:
            out += ["", f"其中思维链 {reasoning:,} tokens，已包含在输出里，不额外计费。"]
        if self.unpriced:
            out += [
                "",
                f"⚠ {len(self.unpriced)} 个模型没登记单价，已按 0 计：{sorted(self.unpriced)}",
                "   请补进 PRICING 表，否则上面的合计是低估的。",
            ]
        return "\n".join(out)