"""阶段18 Step 1：每个问题只跑一次，固定 (query, pages)。

后面的 replay 只换上下文筛选策略，搜索和抓取完全不变。
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config
from evals.common import eval_cfg, load_json, usage_cost, write_json
from llm import USAGE_LOG
from pipeline import _collect_pages, plan_research, reset_dead_urls


def collect_one(item, out: Path, cfg, force: bool = False):
    path = out / f"{item['id']}.json"
    if path.exists() and not force:
        return item["id"], "skip"

    reset_dead_urls()
    start = time.perf_counter()
    usage_start = len(USAGE_LOG)

    try:
        sub_queries = plan_research(item["query"], cfg=cfg)
        pages = _collect_pages(sub_queries, cfg=cfg)
        error = None
    except Exception as exc:
        sub_queries, pages = [], []
        error = f"{type(exc).__name__}: {exc}"

    usage = USAGE_LOG[usage_start:]

    record = {
        **item,
        "sub_queries": sub_queries,
        "filter_query": " ".join([item["query"], *sub_queries]),
        "pages": pages,
        "collect_seconds": round(time.perf_counter() - start, 1),
        "collect_cost": usage_cost(usage),
        "error": error,
    }

    write_json(path, record)
    return item["id"], error or f"{len(sub_queries)} subs / {len(pages)} pages"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--queries",
        type=Path,
        default=ROOT / "evals" / "queries.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "evals" / "runs",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    cfg = dataclasses.replace(eval_cfg(load_config()), max_pages=args.max_pages)
    args.out.mkdir(parents=True, exist_ok=True)

    items = load_json(args.queries)
    if args.limit:
        items = items[: args.limit]

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [
            pool.submit(collect_one, item, args.out, cfg, args.force)
            for item in items
        ]
        for future in as_completed(futures):
            rid, note = future.result()
            print(f"{rid}: {note}", flush=True)


if __name__ == "__main__":
    main()