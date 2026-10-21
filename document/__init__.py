"""本地文档研究：加载、切块、索引、检索。"""

from .loader import iter_files, load_file
from .index import index_paths

__all__ = [
    "iter_files",
    "load_file",
    "index_paths",
    "LocalDocumentRetriever",
]


def __getattr__(name: str):
    """惰性导出 retriever，避免包初始化期间形成循环。"""
    if name == "LocalDocumentRetriever":
        from .retriever import LocalDocumentRetriever

        return LocalDocumentRetriever
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
