"""阶段18 离线验收：评测逻辑。全程打桩，不联网、不调模型。"""

from __future__ import annotations

import dataclasses
import logging
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent
while not (_ROOT / "evals" / "common.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))
logging.disable(logging.WARNING)

from config import load_config
from context import select_context
from evals.common import (
    build_context,
    build_none_capped,
    eval_cfg,
    split_context_blocks,
)
from evals.judge import (
    context_precision,
    grade_accuracy,
    pairwise_verdict,
    summarize,
)

FAILED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<44} {extra}")
    if not ok:
        FAILED.append(name)


def fake_chat(responses):
    it = iter(responses)

    def _chat(messages, **kwargs):
        return next(it)

    return _chat


CFG = load_config()

print("---- 1. 配置与切块 ----")
cfg = eval_cfg(CFG, strategy="keyword_top10")
check("评测关闭 paper_wiki", cfg.paper_wiki is False)
check("keyword_top10 用 keyword", cfg.context_filter == "keyword")
check("keyword_top10 强制筛选", cfg.compression_threshold == 0)
check("keyword_top10 取 10 块", cfg.keyword_max_results == 10)

ctx = "[1] A\nURL: u1\n正文: x\n\n[2] B\nURL: u2\n正文: y\n"
blocks = split_context_blocks(ctx)
check("切出 2 块", len(blocks) == 2, str(len(blocks)))
check("第一块以 [1] 开头", blocks[0].startswith("[1]"))
check("第二块以 [2] 开头", blocks[1].startswith("[2]"))

print("---- 2. none / none_capped ----")
pages = [
    {"title": f"P{i}", "url": f"https://x/{i}", "raw_content": f"mark{i} " + "x" * 2000}
    for i in range(3)
]
cfg_none = dataclasses.replace(
    CFG,
    context_filter="none",
    compression_threshold=0,
)
ctx_none = select_context("mark1", pages, cfg=cfg_none)
check(
    "none 保留全部页面",
    all(f"mark{i}" in ctx_none for i in range(3)),
)

small = [
    {"title": "P0", "url": "u0", "raw_content": "a" * 100},
    {"title": "P1", "url": "u1", "raw_content": "b" * 100},
]
ctx_cap = build_none_capped(small, cap=120)
check("none_capped 第一篇完整", "a" * 100 in ctx_cap)
check("none_capped 第二篇被截断", "b" * 20 in ctx_cap and "b" * 21 not in ctx_cap)

print("---- 3. 策略构建 ----")
ctx_keyword = build_context(
    "keyword_top10",
    "mark1",
    pages,
    cfg=CFG,
)
check("keyword 策略有输出", bool(ctx_keyword.strip()))
check("keyword 策略不是全文", "mark0" not in ctx_keyword or "mark2" not in ctx_keyword)

print("---- 4. 裁判 ----")
check(
    "pairwise 双序都赢 -> win",
    pairwise_verdict(fake_chat(["1", "2"]), CFG, "m", "q", "cand", "base") == "win",
)
check(
    "pairwise 双序都输 -> loss",
    pairwise_verdict(fake_chat(["2", "1"]), CFG, "m", "q", "cand", "base") == "loss",
)
check(
    "pairwise 顺序不一致 -> tie",
    pairwise_verdict(fake_chat(["1", "1"]), CFG, "m", "q", "cand", "base") == "tie",
)
check(
    "pairwise 乱输出 -> tie",
    pairwise_verdict(fake_chat(["x", "y"]), CFG, "m", "q", "cand", "base") == "tie",
)

precision = context_precision(
    fake_chat(['{"relevant": [0, 2]}']),
    CFG,
    "m",
    "q",
    "[1] A\nURL: u1\n正文: x\n\n[2] B\nURL: u2\n正文: y\n\n[3] C\nURL: u3\n正文: z\n",
)
check("precision = 2/3", abs(precision - 2 / 3) < 1e-9, str(precision))

check(
    "accuracy A -> CORRECT",
    grade_accuracy(fake_chat(['{"grade": "A"}']), CFG, "m", "q", "ans", "report")
    == "CORRECT",
)
check(
    "accuracy B -> INCORRECT",
    grade_accuracy(fake_chat(['{"grade": "B"}']), CFG, "m", "q", "ans", "report")
    == "INCORRECT",
)
check(
    "accuracy 乱输出 -> NOT_ATTEMPTED",
    grade_accuracy(fake_chat(["not json"]), CFG, "m", "q", "ans", "report")
    == "NOT_ATTEMPTED",
)

print("---- 5. 汇总 ----")
scored = [
    {
        "id": "q1",
        "strategy": "none_capped",
        "error": None,
        "context_tokens": 100,
        "filter_seconds": 0.0,
        "write_cost": 0.01,
        "total_cost": 0.01,
    },
    {
        "id": "q1",
        "strategy": "keyword",
        "error": None,
        "context_tokens": 50,
        "filter_seconds": 0.1,
        "write_cost": 0.01,
        "total_cost": 0.01,
        "precision": 0.5,
        "pairwise": "win",
        "grade": "CORRECT",
    },
    {
        "id": "q2",
        "strategy": "keyword",
        "error": None,
        "context_tokens": 60,
        "filter_seconds": 0.2,
        "write_cost": 0.02,
        "total_cost": 0.02,
        "precision": 0.7,
        "pairwise": "loss",
        "grade": "INCORRECT",
    },
]
rows = summarize(scored, ["none_capped", "keyword"], "none_capped")
keyword = next(r for r in rows if r["strategy"] == "keyword")
check("汇总 win=1", keyword["pairwise_vs_none_capped"]["win"] == 1)
check("汇总 loss=1", keyword["pairwise_vs_none_capped"]["loss"] == 1)
check("汇总 accuracy=0.5", keyword["accuracy"] == 0.5)
check("汇总 precision=0.6", keyword["precision"] == 0.6)

print()
if FAILED:
    print(f"{len(FAILED)} 项失败: {FAILED}")
    sys.exit(1)
print("阶段 18 离线验收全部通过")