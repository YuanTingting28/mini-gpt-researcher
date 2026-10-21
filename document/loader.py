"""本地文档加载：PDF按页 纯文本按整篇。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any,Iterable

SUPPORTED = {".pdf",".txt",".md",".markdown"}

_SKIP_DIRS = {
    ".git",".hg",".svn","__pycache__",".venv",
    "venv","env","node_modules","document_index",
}

def file_sha1(path:str | Path) ->str:
    """文件内容哈希，用于增量索引"""
    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for block in iter(lambda:handle.read(1024 * 1024),b""):
            digest.update(block)
    return digest.hexdigest()

def _load_pdf(path:Path,*,max_bytes: int | None=None)->list[dict[str,Any]]:
    if max_bytes is not None and path.stat().st_size > max_bytes: #这里大于最大的size了
        raise RuntimeError(
            f"PDF 过大 （{path.stat().st_size} 字节>上限 {max_bytes} 字节）"
        )
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("需要 pymupdf：pip install pymupdf") from exc

    doc = fitz.open(path)
    if getattr(doc,"needs_pass",False):
        doc.close()
        raise RuntimeError(f"PDF加密，无法读取：{path}")

    metadata = getattr(doc,"metadata",None) or {}
    title = str(metadata.get("title") or path.stem).strip() or path.stem

    pages:list[dict[str,Any]] = []
    try:
        for number,page in enumerate(doc,1):
            text = page.get_text("text",sort=True) or ""
            if text.strip():
                pages.append({
                    "text":text,
                    "source":str(path),
                    "title":title,
                    "page":number,
                })
    finally:
        doc.close()
    return pages

def _load_text(path:Path,*,max_bytes: int | None=None) -> list[dict[str,Any]]:
    raw_bytes = path.read_bytes()
    if max_bytes is not None and len(raw_bytes) > max_bytes:
        raise RuntimeError(
            f"文本文件过大（{len(raw_bytes)} 字节 > 上限 {max_bytes} 字节）"
        )
    text = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"无法解码文本文件（试过 UTF-8 / GB18030 / GBK）：{path}")
    return [
        {
            "text": text.replace("\x00", ""),
            "source": str(path),
            "title": path.stem,
            "page": 1,
        }
    ]

def load_file(path:str | Path,*,max_bytes: int | None=None) -> list[dict[str,Any]]:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(path,max_bytes=max_bytes)
    if suffix in (".txt",".md",".markdown"):
        return _load_text(path,max_bytes=max_bytes)
    raise ValueError(f"不支持的文档类型：{suffix}（支持 {sorted(SUPPORTED)}）")

def _is_hidden(path:Path,root:Path) ->bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts)

def iter_files(paths:Iterable[str | Path])->list[Path]:
    """展开文件/目录，递归找支持的文档"""
    out:list[Path] = []
    seen:set[str] = set()

    def add(item:Path) -> None:
        key = os.path.normcase(str(item.resolve()))
        if key not in seen:
            seen.add(key)
            out.append(item)
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if path.is_file():
            if path.suffix.lower() in SUPPORTED:
                add(path)
        elif path.is_dir():
            for root,dirs,names in os.walk(path):
                root_path = Path(root)
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in _SKIP_DIRS and not d.startswith(".")
                ]
                for name in names:
                    item = root_path / name
                    if (
                        item.suffix.lower() in SUPPORTED
                        and not name.startswith(".")
                        and not _is_hidden(item, path)
                    ):
                        add(item)
    return sorted(out, key=lambda p: os.path.normcase(str(p)))
