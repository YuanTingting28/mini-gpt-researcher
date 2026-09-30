"""免 key 检索器，先跑通这个。

对照源码：gpt_researcher/retrievers/duckduckgo/duckduckgo.py（73 行）

必读：文件顶部长注释里的 _MAX_PREFETCHED_LEN = 100。
    ddgs 返回的 snippet 经常超过 100 字符，而下游用
    "raw_content/body 长度 > 100" 判断"正文已抓取"，
    于是正文永远不抓、引用列表为空。
    原项目的修法是在检索器出口就把 snippet 截到 100 字符以内。
"""

from __future__ import annotations

from typing import Any

from .base import BaseRetriever

#: snippet 截断长度，原因见模块顶部注释
_MAX_PREFETCHED_LEN = 100


class DuckDuckGoRetriever(BaseRetriever):
    requires_scraping = True

    def search(self, max_results: int = 5) -> list[dict[str, Any]]:
        """TODO(阶段 1)：

        1. from ddgs import DDGS; DDGS().text(self.query, max_results=max_results)
        2. 逐条归一化：
             href  <- result["href"] or result["link"] or result["url"]
             body  <- result["body"] or result["snippet"] or result["description"]
             title <- result.get("title")
           href 为空的直接跳过。
        3. body 截断到 _MAX_PREFETCHED_LEN
        4. 用切片保证返回条数 <= max_results（有的客户端会无视该参数）
        5. 任何异常都 print 出来并返回 []
        """
        raise NotImplementedError("阶段 1：实现 DuckDuckGoRetriever.search")