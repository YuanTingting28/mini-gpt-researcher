"""检索器注册表：名字 -> 类。"""

from __future__ import annotations

from .base import BaseRetriever

RETRIEVERS: dict[str, type[BaseRetriever]] = {}


def register(name: str, cls: type[BaseRetriever]) -> None:
    RETRIEVERS[name.lower()] = cls


def get_retriever(
    name: str,
    query: str,
    query_domains: list[str] | None = None,
) -> BaseRetriever:
    """按名字造一个检索器实例。

    local 必须在这里导入，不能放到模块顶层：
        document.retriever -> search.base
        search.__init__ 顶层再 -> document.retriever
    会在包初始化期间形成循环导入。
    """
    from .arxiv import ArxivRetriever
    from .duckduckgo import DuckDuckGoRetriever
    from .tavily import TavilyRetriever
    from document.retriever import LocalDocumentRetriever

    register("arxiv", ArxivRetriever)
    register("duckduckgo", DuckDuckGoRetriever)
    register("tavily", TavilyRetriever)
    register("local", LocalDocumentRetriever)

    cls = RETRIEVERS.get(name.lower())
    if cls is None:
        raise ValueError(f"未知检索器 {name!r}，可选：{sorted(RETRIEVERS)}")
    return cls(query, query_domains=query_domains)


__all__ = ["BaseRetriever", "RETRIEVERS", "get_retriever", "register"]
