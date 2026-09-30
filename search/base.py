"""检索器契约。

对照源码：gpt_researcher/retrievers/base.py（47 行，很短，但顶部注释很值钱）

    它讲清了一件事：检索结果分两种——
      requires_scraping = True   搜索接口只给链接 + 摘要，正文要另外抓
      requires_scraping = False  检索器自己就把正文带回来了（如 PubMed Central）

    原项目早期没有这个声明，靠 "raw_content 长度 > 100" 猜，
    结果摘要一长就被误判成"已抓取"，正文永远不抓，报告里一条可验证引用都没有
    （issue #1846 / #1892）。所以：能声明就别猜。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRetriever(ABC):
    """所有检索器的基类。

    requires_scraping 的语义：
      True  -> 每条结果要有 url/href，body 只是预览，正文另外抓
      False -> 每条结果要有 url 和 raw_content（已抓好的正文），不再抓
    """

    requires_scraping: bool = True

    def __init__(self, query: str, query_domains: list[str] | None = None):
        self.query = query
        self.query_domains = query_domains or None

    @abstractmethod
    def search(self, max_results: int = 5) -> list[dict[str, Any]]:
        """返回统一格式的结果列表。

        约定字段（阶段 1 的硬性要求，下游全靠它）：
            href  必填，结果链接
            body  选填，摘要/预览
            title 选填，标题

        TODO(阶段 1)：
          - 失败时返回空列表并打日志，不要抛异常打断主流程
          - 返回条数不超过 max_results
        """
        raise NotImplementedError