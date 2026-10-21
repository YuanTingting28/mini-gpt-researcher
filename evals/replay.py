"""阶段18 Step 2：同一批 pages，换策略重建上下文并写报告。"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config
from evals.common import (
    build_context,
    count_tokens,
    eval_cfg,
    load_json,
    usage_cost,
    write_json,
)
from llm import USAGE_LOG
from report import write_report

STRATEGIES = ["none_capped", "keyword"]


def replay_one(run, strategy: str, out: Path, cfg, force: bool = False):
    path = out / strategy / f"{run['id']}.json"
    if path.exists() and not force:
        return run["id"], strategy, "skip"

    pages = run.get("pages") or []
    query = run["query"]
    filter_query = run.get("filter_query") or query

    t_filter = time.perf_counter()
    context = build_context(strategy, filter_query, pages, cfg=cfg)
    filter_seconds = time.perf_counter() - t_filter

    usage_start = len(USAGE_LOG)
    t_write = time.perf_counter()
    error = None
    report = ""

    if not context.strip():
        error = "empty context"
    else:
        try:
            report = write_report(query, context, None, cfg=cfg)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

    write_seconds = time.perf_counter() - t_write
    usage = USAGE_LOG[usage_start:]
    write_cost = usage_cost(usage)

    record = {
        "id": run["id"],
        "kind": run.get("kind"),
        "query": query,
        "answer": run.get("answer"),
        "strategy": strategy,
        "filter_seconds": round(filter_seconds, 2),
        "filter_cost": 0.0,
        "context_chars": len(context),
        "context_tokens": count_tokens(context),
        "write_seconds": round(write_seconds, 2),
        "write_cost": write_cost,
        "total_cost": write_cost,
        "report": report,
        "error": error,
        "context": context,
    }

    write_json(path, record)
    return (
        run["id"],
        strategy,
        error or f"ctx {len(context)} chars / ${write_cost:.5f}",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runs",
        type=Path,
        default=ROOT / "evals" / "runs",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "evals" / "results",
    )
    parser.add_argument("--strategies", nargs="+", default=STRATEGIES)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    cfg = eval_cfg(load_config())
    args.out.mkdir(parents=True, exist_ok=True)

    runs = [load_json(p) for p in sorted(args.runs.glob("*.json"))]
    runs = [r for r in runs if not r.get("error") and r.get("pages")]
    if args.limit:
        runs = runs[: args.limit]

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [
            pool.submit(replay_one, run, strategy, args.out, cfg, args.force)
            for run in runs
            for strategy in args.strategies
        ]
        for future in as_completed(futures):
            rid, strategy, note = future.result()
            print(f"{strategy:12} {rid}: {note}", flush=True)


if __name__ == "__main__":
    main()