"""本地文档检索器：复用 context.py 的中文 2-gram + BM25。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config import Config, load_config
from context import bm25_scores
from search.base import BaseRetriever
from .index import load_index

logger = logging.getLogger(__name__)


def _file_url(path: str, page: int) -> str:
    try:
        base = Path(path).expanduser().resolve().as_uri()
    except ValueError:
        base = f"file:///{Path(path).as_posix().lstrip('/')}"
    return f"{base}#page={max(1, int(page))}"


class LocalDocumentRetriever(BaseRetriever):
    """本地索引检索器。"""

    requires_scraping = False

    def __init__(
        self,
        query: str,
        query_domains: list[str] | None = None,
        *,
        cfg: Config | None = None,
        index: dict[str, Any] | None = None,
        index_path: str | Path | None = None,
    ):
        super().__init__(query, query_domains)
        self.cfg = cfg or load_config()
        self.index_path = Path(
            index_path
            or getattr(self.cfg, "document_index", "document_index/index.json")
        ).expanduser().resolve()
        self._index = index

    def _chunks(self) -> list[dict[str, Any]]:
        if self._index is None:
            self._index = load_index(self.index_path)

        return [
            c
            for c in (self._index.get("chunks") or [])
            if isinstance(c, dict) and str(c.get("text") or "").strip()
        ]

    def search(self, max_results: int = 5) -> list[dict[str, Any]]:
        limit = max(0, int(max_results))
        if limit == 0 or not (self.query or "").strip():
            return []

        chunks = self._chunks()
        if not chunks:
            logger.warning(
                "本地文档索引为空（%s）。先运行：python main.py local-index <PDF或目录>",
                self.index_path,
            )
            return []

        passages = [
            f"{c.get('title') or ''}\n{c.get('text') or ''}"
            for c in chunks
        ]

        try:
            scores = bm25_scores(self.query, passages)
        except Exception as exc:
            logger.warning("本地文档 BM25 打分失败：%s", exc)
            return []

        best = max(scores, default=0.0)
        if best <= 0:
            logger.info(
                "本地文档：查询与索引没有词法重合，不返回低相关页。"
                "若文档是英文而查询是中文，请改用英文关键词；"
                "跨语言检索需要 embedding/FAISS。"
            )
            return []

        threshold = float(
            getattr(self.cfg, "document_relative_threshold", 0.5)
        )
        threshold = min(1.0, max(0.0, threshold))

        ranked = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))

        kept: list[tuple[int, float]] = []
        seen_pages: set[tuple[str, int]] = set()

        for i in ranked:
            score = float(scores[i])
            if score < best * threshold:
                break

            item = chunks[i]
            key = (
                str(item.get("source") or ""),
                int(item.get("page") or 1),
            )
            if key in seen_pages:
                continue

            seen_pages.add(key)
            kept.append((i, score))

            if len(kept) >= limit:
                break

        results: list[dict[str, Any]] = []

        for i, score in kept:
            item = chunks[i]
            source = str(item.get("source") or "")
            page = int(item.get("page") or 1)
            title = str(item.get("title") or Path(source).stem)
            text = str(item.get("text") or "")

            results.append(
                {
                    "title": f"{title} · 第 {page} 页",
                    "href": _file_url(source, page),
                    "body": text,
                    "raw_content": text,
                    "source": source,
                    "page": page,
                    "chunk": int(item.get("chunk") or 0),
                    "score": round(score, 4),
                    "requires_scraping": False,
                }
            )

        logger.info(
            "本地文档：%d 个块中保留 %d 页（最高分 %.3f，阈值 %.2f×）",
            len(chunks),
            len(results),
            best,
            threshold,
        )
        return results


__all__ = ["LocalDocumentRetriever"]