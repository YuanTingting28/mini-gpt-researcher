"""本地文档索引：增量解析 -> 切块 -> 落盘 JSON。"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config import Config, load_config
from context import chunk_text
from .loader import file_sha1, iter_files, load_file

logger = logging.getLogger(__name__)

INDEX_VERSION = 1


def empty_index() -> dict[str, Any]:
    return {
        "version": INDEX_VERSION,
        "built_at": None,
        "files": {},
        "pages": [],
        "chunks": [],
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def _backup_corrupt(path: Path) -> Path | None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}.bak")
    try:
        path.replace(backup)
        return backup
    except OSError as exc:
        logger.warning("索引损坏，但备份失败：%s", exc)
        return None


def _validate_index(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("索引根节点不是对象")
    if data.get("version") != INDEX_VERSION:
        raise ValueError(f"索引版本不匹配：{data.get('version')!r} != {INDEX_VERSION}")
    if not isinstance(data.get("files"), dict):
        raise ValueError("索引缺少 files 对象")
    if not isinstance(data.get("pages"), list):
        raise ValueError("索引缺少 pages 列表")
    if not isinstance(data.get("chunks"), list):
        raise ValueError("索引缺少 chunks 列表")

    chunks: list[dict[str, Any]] = []
    for item in data["chunks"]:
        if not isinstance(item, dict):
            continue

        text = item.get("text")
        source = item.get("source")
        if not isinstance(text, str) or not text.strip() or not isinstance(source, str):
            continue

        try:
            page = int(item.get("page") or 1)
            chunk = int(item.get("chunk") or 0)
        except (TypeError, ValueError):
            continue

        chunks.append(
            {
                "sha1": str(item.get("sha1") or ""),
                "source": source,
                "title": str(item.get("title") or Path(source).stem),
                "page": page,
                "chunk": chunk,
                "text": text,
            }
        )

    pages: list[dict[str, Any]] = []
    for item in data["pages"]:
        if not isinstance(item, dict):
            continue

        source = item.get("source")
        text = item.get("text")
        if not isinstance(source, str) or not isinstance(text, str):
            continue

        try:
            page = int(item.get("page") or 1)
        except (TypeError, ValueError):
            continue

        pages.append(
            {
                "sha1": str(item.get("sha1") or ""),
                "source": source,
                "title": str(item.get("title") or Path(source).stem),
                "page": page,
                "text": text,
            }
        )

    files = {
        str(sha): rec
        for sha, rec in data["files"].items()
        if isinstance(rec, dict) and rec.get("path")
    }

    return {
        "version": INDEX_VERSION,
        "built_at": data.get("built_at"),
        "files": files,
        "pages": pages,
        "chunks": chunks,
    }


def load_index(path: str | Path) -> dict[str, Any]:
    """文件不存在返回空索引；损坏则备份后重建。"""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return empty_index()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _validate_index(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        backup = _backup_corrupt(path)
        logger.warning(
            "本地文档索引不可用（%s），已重建%s",
            exc,
            f"，旧文件备份到 {backup}" if backup else "",
        )
        return empty_index()


def save_index(path: str | Path, index: dict[str, Any]) -> Path:
    """原子写：先写临时文件再 replace。"""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    index = dict(index)
    index["version"] = INDEX_VERSION
    index["built_at"] = _now()

    payload = json.dumps(index, ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _remove_records(index: dict[str, Any], shas: set[str]) -> None:
    if not shas:
        return

    for sha in shas:
        index["files"].pop(sha, None)

    index["pages"] = [p for p in index["pages"] if p.get("sha1") not in shas]
    index["chunks"] = [c for c in index["chunks"] if c.get("sha1") not in shas]


def _stale_shas(index: dict[str, Any], path: Path, sha1: str | None = None) -> set[str]:
    stale: set[str] = set()

    if sha1 and sha1 in index["files"]:
        stale.add(sha1)

    key = _path_key(path)
    for old_sha, record in index["files"].items():
        if _path_key(record.get("path", "")) == key:
            stale.add(old_sha)

    return stale


def _normalize_input_paths(
    paths: str | Path | Iterable[str | Path] | None,
) -> list[str | Path]:
    if paths is None:
        return []
    if isinstance(paths, (str, Path)):
        return [paths]
    return list(paths)


def index_paths(
    paths: str | Path | Iterable[str | Path] | None = None,
    *,
    cfg: Config | None = None,
    force: bool = False,
    prune: bool = False,
    index_path: str | Path | None = None,
) -> dict[str, Any]:
    """建立/更新本地文档索引。"""
    cfg = cfg or load_config()
    raw_paths = _normalize_input_paths(paths)

    if not raw_paths:
        raw_paths = list(getattr(cfg, "document_paths", ()) or ())

    target = Path(
        index_path or getattr(cfg, "document_index", "document_index/index.json")
    ).expanduser().resolve()

    index = load_index(target)

    stats: dict[str, Any] = {
        "ok": False,
        "index_path": str(target),
        "scanned": 0,
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "files": len(index["files"]),
        "pages": len(index["pages"]),
        "chunks": len(index["chunks"]),
        "errors": [],
    }

    if not raw_paths:
        stats["errors"].append(
            "没有文档路径：在 .env 设置 DOCUMENT_PATHS，或运行 local-index <路径>"
        )
        return stats

    path_errors = 0
    for raw in raw_paths:
        p = Path(raw).expanduser()
        if not p.exists():
            path_errors += 1
            stats["errors"].append(f"路径不存在：{p}")

    files = iter_files(raw_paths)
    stats["scanned"] = len(files)

    if not files:
        stats["errors"].append("没有找到 .pdf / .txt / .md 文件")
        return stats

    max_bytes = max(0, int(getattr(cfg, "document_max_file_mb", 100))) * 1024 * 1024
    chunk_size = max(50, int(getattr(cfg, "chunk_size", 1000)))
    overlap = max(0, min(int(getattr(cfg, "chunk_overlap", 150)), chunk_size - 1))

    for path in files:
        try:
            stat = path.stat()

            if max_bytes and stat.st_size > max_bytes:
                raise RuntimeError(
                    f"文件过大（{stat.st_size} 字节 > 上限 {max_bytes} 字节）"
                )

            sha1 = file_sha1(path)
            old = index["files"].get(sha1)
            same_path = bool(old) and _path_key(old.get("path", "")) == _path_key(path)

            if old and same_path and not force:
                stats["skipped"] += 1
                logger.info("跳过未变化文档：%s", path)
                continue

            pages = load_file(path, max_bytes=max_bytes or None)

            new_pages: list[dict[str, Any]] = []
            new_chunks: list[dict[str, Any]] = []

            for page in pages:
                text = str(page.get("text") or "")
                if not text.strip():
                    continue

                page_no = int(page.get("page") or 1)
                title = str(page.get("title") or path.stem)

                new_pages.append(
                    {
                        "sha1": sha1,
                        "source": str(path),
                        "title": title,
                        "page": page_no,
                        "text": text,
                    }
                )

                for number, part in enumerate(chunk_text(text, chunk_size, overlap)):
                    if part.strip():
                        new_chunks.append(
                            {
                                "sha1": sha1,
                                "source": str(path),
                                "title": title,
                                "page": page_no,
                                "chunk": number,
                                "text": part,
                            }
                        )

            if not new_pages:
                raise RuntimeError("没有可抽取的文字（可能是扫描件，或文件为空）")

            # 解析成功后才动旧记录，失败时保留上一版可用索引。
            was_update = bool(_stale_shas(index, path, sha1))
            _remove_records(index, _stale_shas(index, path, sha1))

            index["pages"].extend(new_pages)
            index["chunks"].extend(new_chunks)
            index["files"][sha1] = {
                "path": str(path),
                "title": new_pages[0]["title"],
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "indexed_at": _now(),
            }

            if was_update:
                stats["updated"] += 1
            else:
                stats["added"] += 1

            logger.info(
                "索引 %s：%d 页 / %d 块",
                path,
                len(new_pages),
                len(new_chunks),
            )

        except Exception as exc:
            stats["failed"] += 1
            stats["errors"].append(f"{path}：{exc}")
            logger.warning("索引失败，跳过 %s：%s", path, exc)

    if prune:
        missing = {
            sha
            for sha, rec in index["files"].items()
            if not Path(rec.get("path", "")).exists()
        }
        if missing:
            _remove_records(index, missing)
            logger.info("清理失效文件记录 %d 个", len(missing))

    save_index(target, index)

    stats.update(
        {
            "ok": stats["failed"] == 0
            and stats["scanned"] > 0
            and path_errors == 0,
            "files": len(index["files"]),
            "pages": len(index["pages"]),
            "chunks": len(index["chunks"]),
        }
    )
    return stats


def index_summary(path: str | Path) -> dict[str, Any]:
    index = load_index(path)
    return {
        "path": str(Path(path).expanduser().resolve()),
        "files": len(index["files"]),
        "pages": len(index["pages"]),
        "chunks": len(index["chunks"]),
        "built_at": index.get("built_at"),
        "documents": [
            {"path": rec.get("path"), "title": rec.get("title")}
            for rec in index["files"].values()
        ],
    }


__all__ = [
    "INDEX_VERSION",
    "empty_index",
    "index_paths",
    "index_summary",
    "load_index",
    "save_index",
]