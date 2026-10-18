"""arXiv 检索器：免 key、免第三方包，走官方 Atom 接口。

为什么值得加：Tavily 返回的多是二手博客，arXiv 返回的是一手论文。
它给出的 href 是 pdf_url，正好接进阶段12 已经做好的 PyMuPDF 抽取链路。

一个必须说清的限制：arXiv 只收英文论文。纯中文查询（本项目规划器产出的
子问题大多是中文）在 arXiv 上基本必然 0 条。所以这里做了短路：查询里没有
任何 ASCII 单词就不发请求 —— 省一次注定为空的网络往返，日志里也能看出
"不是检索失败，是这个源不适用"。
这里支持检索英文
对照源码：gpt_researcher/retrievers/arxiv/arxiv.py（用 arxiv 库，本质是同一个 API）
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import requests

from config import Config, load_config
from .base import BaseRetriever

logger = logging.getLogger(__name__)

API = "https://export.arxiv.org/api/query"
NS = {"a":"http://www.w3.org/2005/Atom"}
_MAX_QUERY_LEN = 200
_ASCII_WORD = re.compile(r"[A-Za-z]{2,}")

class ArxivRetriever(BaseRetriever):
    requires_scraping = True

    def __init__(self,
                 query:str,
                 query_domains:list[str] | None = None,
                 *,
                 timeout:int | None = None,
                 cfg: Config | None=None):
        super().__init__(query,query_domains)
        self.cfg = cfg or load_config()
        self.timeout = timeout if timeout is not None else self.cfg.request_timeout

    def search(self,max_results:int=5) -> list[dict[str,Any]]:
        if not _ASCII_WORD.search(self.query or ""):
            logger.info(
                "arXiv 跳过纯中文查询（该源只收英文论文）：%r", (self.query or "")[:60]
            )
            return []
        try:
            resp = requests.get(API,params=self._params(max_results),timeout=self.timeout)
            resp.raise_for_status()
            return self._normalize(resp.text,max_results)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            logger.warning("arXiv 返回 HTTP %s", status)
            return []
        except Exception as exc:
            logger.warning("arXiv 检索失败（%s: %s）", type(exc).__name__, exc)
            return []
    def _params(self,max_results:int)->dict[str, Any]:
        q = " ".join((self.query or "").split())[:_MAX_QUERY_LEN]
        return {
            "search_query": f'all:"{q}"',
            "start": 0,
            "max_results": max(1, max_results),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

    @staticmethod
    def _normalize(xml_text: str, max_results: int) -> list[dict[str, Any]]:
        """Atom -> 统一格式。逐条防御：缺字段的条目丢掉，不能让一条脏数据带走整批。"""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("arXiv 返回的不是合法 XML：%s", exc)
            return []

        out: list[dict[str, Any]] = []
        for entry in root.findall("a:entry", NS):
            title = " ".join((entry.findtext("a:title", "", NS) or "").split())
            summary = " ".join((entry.findtext("a:summary", "", NS) or "").split())

            href = ""
            for link in entry.findall("a:link", NS):
                if link.get("title") == "pdf" and link.get("href"):
                    href = link.get("href").strip()
                    break
            if not href:
                alt = entry.find("a:link[@rel='alternate']", NS)
                href = (alt.get("href") if alt is not None else "") or ""
                href = href.strip() or (entry.findtext("a:id", "", NS) or "").strip()
            if not href:
                continue

            out.append({"title": title, "href": href, "body": summary})
            if len(out) >= max_results:
                break
        return out