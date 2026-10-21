"""阶段18 Step 3：LLM 当裁判。

三个信号：
  precision  上下文里被裁判判为相关的块占比
  pairwise   盲评，两个顺序都跑；只有两边都赢才算 win
  accuracy   带标准答案的 fact 题，直接判 CORRECT / INCORRECT / NOT_ATTEMPTED
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config
from evals.common import load_json, split_context_blocks, write_json
from llm import chat

NO_THINKING = {"reasoning_effort": "none"}


def ask(chat_fn, cfg, model, prompt, *, json_mode=False, max_tokens=400):
    return chat_fn(
        [{"role": "user", "content": prompt}],
        cfg=cfg,
        model=model,
        temperature=0,
        max_tokens=max_tokens,
        json_mode=json_mode,
        extra_body=NO_THINKING,
        step="评测裁判",
    )


PAIRWISE = """你在比较两篇回答同一问题的研究报告。

问题：{query}

=== 报告 1 ===
{a}

=== 报告 2 ===
{b}

先看事实准确性和具体性，再看相关性（不要奖励凑字数），最后看完整性。
只输出 1、2 或 tie。"""


def pairwise_verdict(chat_fn, cfg, model, query, candidate, baseline) -> str:
    """candidate 对 baseline：win / loss / tie。"""
    first = ask(
        chat_fn, cfg, model,
        PAIRWISE.format(query=query, a=candidate, b=baseline),
        max_tokens=200,
    )
    second = ask(
        chat_fn, cfg, model,
        PAIRWISE.format(query=query, a=baseline, b=candidate),
        max_tokens=200,
    )

    first = (first or "").strip().strip('"').lower()
    second = (second or "").strip().strip('"').lower()

    if first.startswith("1") and second.startswith("2"):
        return "win"
    if first.startswith("2") and second.startswith("1"):
        return "loss"
    return "tie"


def parse_relevant(text: str, n: int) -> set[int] | None:
    try:
        raw = re.search(r"\{.*\}|\[.*\]", text or "", re.S).group(0)
        data = json.loads(raw)
    except Exception:
        return None

    if isinstance(data, dict):
        data = data.get("relevant") or data.get("indices") or []
    if not isinstance(data, list):
        return None

    out: set[int] = set()
    for item in data:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < n:
            out.add(idx)
    return out


PRECISION = """研究问题：{query}

下面是 {n} 个编号片段。判断每个片段是否包含有助于准确回答问题的信息。
只输出 JSON：{{"relevant": [0, 2]}}。

{listing}"""


def context_precision(chat_fn, cfg, model, query, context, max_blocks=25):
    blocks = split_context_blocks(context)
    if not blocks:
        return 0.0

    blocks = blocks[:max_blocks]
    listing = "\n\n".join(
        f"[{i}] {block[:1000]}" for i, block in enumerate(blocks)
    )
    text = ask(
        chat_fn, cfg, model,
        PRECISION.format(query=query, n=len(blocks), listing=listing),
        json_mode=True,
        max_tokens=300,
    )
    relevant = parse_relevant(text, len(blocks))
    if relevant is None:
        return None
    return len(relevant) / len(blocks)


ACCURACY = """问题：{query}
标准答案：{answer}
报告：
{report}

报告是否包含标准答案？
A = 正确
B = 错误
C = 未回答或未尝试
只输出 JSON：{{"grade": "A"}}"""


def grade_accuracy(chat_fn, cfg, model, query, answer, report) -> str:
    text = ask(
        chat_fn, cfg, model,
        ACCURACY.format(query=query, answer=answer, report=report),
        json_mode=True,
        max_tokens=200,
    )
    try:
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        grade = str(data.get("grade", "")).strip().upper()[:1]
    except Exception:
        grade = ""

    return {
        "A": "CORRECT",
        "B": "INCORRECT",
        "C": "NOT_ATTEMPTED",
    }.get(grade, "NOT_ATTEMPTED")


def summarize(scored, strategies, baseline):
    rows = []
    for strategy in strategies:
        rs = [r for r in scored if r["strategy"] == strategy]
        pairwise = [r["pairwise"] for r in rs if r.get("pairwise")]
        precision = [r["precision"] for r in rs if r.get("precision") is not None]
        grades = [r["grade"] for r in rs if r.get("grade")]

        rows.append(
            {
                "strategy": strategy,
                "runs": len(rs),
                "errors": sum(1 for r in rs if r.get("error")),
                "accuracy": (
                    round(sum(g == "CORRECT" for g in grades) / len(grades), 3)
                    if grades
                    else None
                ),
                "precision": (
                    round(statistics.mean(precision), 3)
                    if precision
                    else None
                ),
                f"pairwise_vs_{baseline}": (
                    {k: pairwise.count(k) for k in ("win", "tie", "loss")}
                    if pairwise
                    else None
                ),
                "median_context_tokens": (
                    statistics.median(r["context_tokens"] for r in rs)
                    if rs
                    else None
                ),
                "median_filter_seconds": (
                    statistics.median(r["filter_seconds"] for r in rs)
                    if rs
                    else None
                ),
                "mean_write_cost": (
                    round(statistics.mean(r["write_cost"] for r in rs), 5)
                    if rs
                    else None
                ),
                "mean_total_cost": (
                    round(statistics.mean(r["total_cost"] for r in rs), 5)
                    if rs
                    else None
                ),
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results",
        type=Path,
        default=ROOT / "evals" / "results",
    )
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--precision-model", default=None)
    parser.add_argument("--baseline", default="none_capped")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    cfg = load_config()
    judge_model = args.judge_model or cfg.smart_llm
    precision_model = args.precision_model or cfg.fast_llm
    baseline = args.baseline

    by_strategy = defaultdict(dict)
    for path in args.results.glob("*/*.json"):
        r = load_json(path)
        by_strategy[r["strategy"]][r["id"]] = r

    strategies = sorted(
        by_strategy,
        key=lambda s: (s != baseline, s),
    )
    if args.only:
        strategies = [
            s for s in strategies if s in args.only or s == baseline
        ]

    runs = []
    for strategy in strategies:
        values = list(by_strategy[strategy].values())
        if args.limit:
            values = values[: args.limit]
        runs.extend(values)

    def score(r):
        out = dict(r)

        out["precision"] = context_precision(
            chat, cfg, precision_model, r["query"], r.get("context") or ""
        )

        if r.get("kind") == "fact" and r.get("answer"):
            out["grade"] = grade_accuracy(
                chat, cfg, judge_model,
                r["query"], r["answer"], r.get("report") or "",
            )

        base = by_strategy[baseline].get(r["id"])
        if (
            r["strategy"] != baseline
            and base
            and r.get("report")
            and base.get("report")
        ):
            out["pairwise"] = pairwise_verdict(
                chat, cfg, judge_model,
                r["query"], r["report"], base["report"],
            )

        return out

    scored = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [pool.submit(score, r) for r in runs]
        for future in as_completed(futures):
            try:
                scored.append(future.result())
            except Exception as exc:
                print(f"judge failed: {exc}", file=sys.stderr)

    compact = [
        {k: v for k, v in r.items() if k not in ("report", "context")}
        for r in scored
    ]
    write_json(args.results / "scored.json", compact)

    summary = summarize(scored, strategies, baseline)
    write_json(args.results / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=1))

    print()
    print("| strategy | runs | errors | accuracy | precision | vs baseline | ctx tokens | filter s | write $ | total $ |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for row in summary:
        pw = row.get(f"pairwise_vs_{baseline}") or {}
        print(
            f"| {row['strategy']} | {row['runs']} | {row['errors']} | "
            f"{row['accuracy']} | {row['precision']} | "
            f"{pw.get('win', 0)}/{pw.get('tie', 0)}/{pw.get('loss', 0)} | "
            f"{row['median_context_tokens']} | {row['median_filter_seconds']} | "
            f"{row['mean_write_cost']} | {row['mean_total_cost']} |"
        )


if __name__ == "__main__":
    main()