"""Tavily 检索器：需要 key，但结果质量比 DuckDuckGo 好。

对照源码：gpt_researcher/retrievers/tavily/tavily_search.py（137 行）

两个值得抄的点：
  1. LLM 生成的查询常带 Google 风格 site: 操作符，Tavily 不支持会返回 0 条结果，
     原项目把 site:x.com 抽出来转成 include_domains 参数。
  2. Tavily 拒绝超过 400 字符的查询，代码里做了 query[:400]。
"""

from __future__ import annotations

import json
import logging
import re
from config import Config, load_config

_SITE_OPERATOR_PATTERN = re.compile(r"site:(\S+)", re.IGNORECASE)
import requests

import os
from typing import Literal, Any
from .base import BaseRetriever

TAVILY_ENDPOINT = "https://api.tavily.com/search"
logger = logging.getLogger(__name__)
_MAX_QUERY_LEN = 400

class TavilyRetriever(BaseRetriever):
    requires_scraping = True
    def __init__(self,
                 query:str,
                 query_domains:list[str] | None = None,
                 *,
                 topic="general",
                 timeout:int | None = None,
                 cfg:Config | None = None,
                 ):
        super().__init__(query,query_domains)
        self.topic = topic
        self.cfg = cfg or load_config()
        self.timeout = timeout if timeout is not None else self.cfg.request_timeout
        self.api_key = os.environ.get("TAVILY_API_KEY","").strip()

    def search(self,max_results: int = 5) -> list[dict[str, Any]]:
        """
        返回 [{href,body,title?}],履行BaseRetriever契约
        任何异常都将降级成空列表，阶段8会并发跑多个子查询
        多个检索源，单个源不断终端整轮研究

        TODO(阶段 1)：

        1. 从 os.environ 取 TAVILY_API_KEY，取不到就提示并返回 []
        2. POST TAVILY_ENDPOINT，body 带 query / max_results / api_key
        3. 响应里 results 是 list，每条取 url 和 content
        4. 归一化成 href / body，异常返回 []
        5. 加分项：实现 site: -> include_domains 的转换
        """
        if not self.api_key:
            logger.warning("未配置")
            return []
        try:
            payload = self._build_payload(max_results)
            data = self._post(payload)
            return self._normalize(data,max_results)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            logger.warning("Tavily 返回 HTTP %s，查询=%r", status, self.query[:80])
            return []
        except Exception as exc:
            logger.warning("Tavily 检索失败（%s: %s）", type(exc).__name__, exc)
            return []
    def _build_payload(self,max_results:int) -> dict[str, Any]:
        """出方向：内部统一格式 -> Tavily 请求格式。"""
        query = self.query
        include_domains:list[str] = list(self.query_domains or [])
        site_domains = _SITE_OPERATOR_PATTERN.findall(query)
        if site_domains:
    # "site:example.com/path," -> "example.com"
            query = _SITE_OPERATOR_PATTERN.sub(" ", query).strip() #sub是替换，把所有的匹配替换成repl 用空格可以隔离边界
            cleaned = [d.strip(",.;").split("/")[0] for d in site_domains]
            include_domains = list(dict.fromkeys([d for d in cleaned if d]+include_domains))
        return {
            "query": query[:_MAX_QUERY_LEN],
            "search_depth":"basic",
            "topic":self.topic,
            "max_results":max_results,
            "include_domains":include_domains or None,
            "include_raw_content":False,
            "api_key":self.api_key,
        }
    def _post(self,payload: dict[str, Any]) -> Any:
        """纯HTTP层 只负责发送请求"""
        response = requests.post(TAVILY_ENDPOINT,data= json.dumps(payload),headers={"Content-Type": "application/json"}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _normalize(data: Any, max_results: int) -> list[dict[str, Any]]:
        """入方向：Tavily 响应格式 -> 内部统一格式。

        逐条防御：走代理时网关可能返回 list 或字符串，个别条目也可能缺字段，
        不能让一条畸形数据带走整批结果。
        """

        if not isinstance(data, dict):
            logger.warning("Tavily 响应顶层不是 JSON 对象，实际是 %s", type(data).__name__)
            return []

        sources = data.get("results")
        if not isinstance(sources, list):
            logger.warning("Tavily 响应里没有 results 列表")
            return []

        out: list[dict[str, Any]] = []
        for obj in sources:
            if not isinstance(obj, dict):
                continue
            href = str(obj.get("url") or "").strip()
            if not href:
                continue
            body = obj.get("content") or obj.get("snippet") or ""
            item:dict[str, Any] = {"href":href, "body":str(body)}
            title = obj.get("title")
            if title:
                item["title"] = str(title)
            out.append(item)
            if len(out) >= max_results:
                break
        return out