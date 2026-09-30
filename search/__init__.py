"""检索器注册表：名字 -> 类。"""

from __future__ import annotations

from .base import BaseRetriever

RETRIEVERS: dict[str, type[BaseRetriever]] = {}


def register(name: str, cls: type[BaseRetriever]) -> None:
    RETRIEVERS[name.lower()] = cls


def get_retriever(name: str, query: str, query_domains: list[str] | None = None) -> BaseRetriever:
    """按名字造一个检索器实例。

    对照源码：gpt_researcher/retrievers/utils.py

    TODO(阶段 1)：两个检索器都实现后，这里的注册就生效了。
    """
    from .duckduckgo import DuckDuckGoRetriever
    from .tavily import TavilyRetriever

    register("duckduckgo", DuckDuckGoRetriever)
    register("tavily", TavilyRetriever)

    cls = RETRIEVERS.get(name.lower())
    if cls is None:
        raise ValueError(f"未知检索器 {name!r}，可选：{sorted(RETRIEVERS)}")
    return cls(query, query_domains=query_domains)


__all__ = ["BaseRetriever", "RETRIEVERS", "get_retriever", "register"]