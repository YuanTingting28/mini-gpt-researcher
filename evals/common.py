"""阶段18 评测公共层 固定输入、策略构建、成本统计"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

from config import Config, load_config

NONE_CAP_CHARS = 48_000
try:
    import tiktoken
    _ENC = tiktoken.get_encoding("o200k_base")
except Exception:
    _ENC = None

def count_tokens(text:str)->int:
    if _ENC is None:
        return max(1,len(text or "") // 2)
    return len(_ENC.encode(text or "",disallowed_special=()))

def load_json(path:str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

def eval_cfg(cfg:Config | None=None,*,strategy:str | None=None) -> Config:
    """评测时固定 paper wiki 避免把上下文筛选的差异搅浑"""
    cfg = cfg or load_config()
    changes:dict[str, Any] = {"paper_wiki":False}
    if strategy and strategy.startswith("keyword"):
        changes.update(
            {
                "context_filter":"keyword",
                "compression_threshold":0,
                "keyword_max_results":10 if strategy == "keyword_top10" else 25,
            }
        )
    elif strategy == "none_capped":
        changes.update(
            {
                "context_filter": "none",
                "compression_threshold": 0,
            }
        )
    return dataclasses.replace(cfg, **changes)

def split_context_blocks(context: str) -> list[str]:
    """按 pretty_print_docs 的 [n] 标记切块。"""
    text = (context or "").strip()
    if not text:
        return []

    matches = list(re.finditer(r"(?m)^\[(\d+)\]\s", text))
    blocks: list[str] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append(text[match.start():end].strip())
    return blocks


def build_none_capped(
    pages: list[dict[str, Any]],
    cap: int = NONE_CAP_CHARS,
) -> str:
    """不排序，按检索顺序拼全文，总字符数封顶。"""
    from context import pretty_print_docs

    kept: list[dict[str, Any]] = []
    used = 0

    for page in pages:
        text = page.get("raw_content") or ""
        if not text:
            continue
        take = text[: max(0, cap - used)]
        if not take:
            break
        kept.append({**page, "raw_content": take})
        used += len(take)

    return pretty_print_docs(kept)


def build_context(
    strategy: str,
    query: str,
    pages: list[dict[str, Any]],
    *,
    cfg: Config | None = None,
) -> str:
    cfg = eval_cfg(cfg, strategy=strategy)

    if strategy == "none_capped":
        return build_none_capped(pages)

    if strategy in ("keyword", "keyword_top10", "keyword_wide"):
        from context import select_context

        return select_context(query, pages, cfg=cfg)

    raise ValueError(f"未知评测策略 {strategy!r}")


def usage_cost(usage: list[dict[str, Any]]) -> float:
    from costs import CostTracker

    tracker = CostTracker()
    for item in usage:
        tracker.add(
            item["model"],
            item["prompt_tokens"],
            item["completion_tokens"],
            step=item.get("step", "default"),
            cache_hit_tokens=item.get("cache_hit_tokens", 0),
            reasoning_tokens=item.get("reasoning_tokens", 0),
        )
    return tracker.total