"""编排层：把前面几层串成一条链。

对照源码：
    gpt_researcher/agent.py:343            conduct_research（顶层入口）
    gpt_researcher/skills/researcher.py:97 conduct_research（主循环，全文最重要的函数）
        _get_initial_search_results(50)
        plan_research(68)
        _process_sub_query(520)
        _get_context_by_web_search(298)
        _get_new_urls(813)          URL 去重
        _extract_content(1065)
        _summarize_content(1102)
    gpt_researcher/skills/context_manager.py（134 行）
    gpt_researcher/actions/query_processing.py
        generate_sub_queries(83)      三级降级，值得抄
        _normalize_sub_queries(11)    畸形 JSON 归一化，必读
        plan_research_outline(158)
    gpt_researcher/utils/workers.py(42) / rate_limiter.py(82)  并发与限流
"""

from __future__ import annotations

import logging
from typing import Any
from llm import chat
import prompts
from config import Config, load_config
from search import get_retriever

logger = logging.getLogger(__name__)

def format_context(results:list[dict[str,Any]])->str:
    """把检索结果拼成带编号的上下文。
    **编号在这里就固定下来** ——阶段6的引用角标和参考文献依靠这个，绝对不能让模型自己发明编号
    对照源码: context += f"[{i}]{title}:{body}({url})\n\n"
    字段用 or 链兜底 因为不同检索器给的键名不完全一致

    """
    blocks = []
    for i,item in enumerate(results,1):
        title = item.get("title") or "(无标题)"
        body = item.get("body") or item.get("content") or ""
        url = item.get("href") or item.get("url") or ""
        blocks.append(f"[{i}]{title}\n{body}\n来源:{url}\n")
    return "\n".join(blocks)

def quick_summary(query: str, *, cfg: Config | None = None) -> str:
    """阶段 2：搜索一次，让模型写一段带来源的短答案。

    对照源码：gpt_researcher/agent.py:531 quick_search
               gpt_researcher/prompts.py:532 generate_quick_summary_prompt

    TODO(阶段 2)：
      1. search.get_retriever(cfg.retriever, query).search(cfg.max_search_results)
      2. 拼 context：每条 "标题 / 链接 / 摘要"
      3. llm.chat，提示词要求：200 字以内 + 引用来源链接
    """
    cfg = cfg or load_config()
    #1.检索
    retriever = get_retriever(cfg.retriever,query)
    results = retriever.search(cfg.max_search_results)
    if not results:
        # 阶段8会一次跑很多子查询，这里不能抛异常
        return "检索没有返回任何结果，无法生成摘要。"
    #品上下文
    context = format_context(results)
    logger.info("检索到 %d 条来源，上下文共 %d 字符", len(results), len(context))
    prompt = prompts.quick_summary_prompt(query,context)
    return chat([{"role":"user","content":prompt}],cfg=cfg)


def plan_research(query: str, *, cfg: Config | None = None) -> list[str]:
    """阶段 3：复杂问题 -> 子问题列表。

    对照源码：gpt_researcher/actions/query_processing.py:83 generate_sub_queries
               gpt_researcher/prompts.py:213 generate_search_queries_prompt
               gpt_researcher/skills/researcher.py:68 plan_research

    TODO(阶段 3)：
      1. 提示词要求模型返回 JSON 数组，个数 = cfg.max_sub_queries
      2. 写一个 normalize_sub_queries(parsed, fallback) 函数，参考
         query_processing.py:11，必须能处理：
           list / {"queries": [...]} / {"query": "..."} / "裸字符串" / None
      3. 解析用 json_repair.loads（模型经常返回 ```json 包裹或带尾逗号）
      4. 全都解析不出来 -> 退化成 [query]，绝不能返回空列表
    """
    raise NotImplementedError("阶段 3：实现 plan_research")


def build_context(query: str, *, cfg: Config | None = None) -> dict[str, Any]:
    """阶段 5：搜索 -> 抓取 -> 压缩，返回 {context, sources, raw_chars}。

    TODO(阶段 5)：串起 search / scrape / context 三层。
    并发抓取放到阶段 8 再做，这里先串行。
    """
    raise NotImplementedError("阶段 5：实现 build_context")


def research_report(query: str, *, cfg: Config | None = None, with_costs: bool = False) -> dict[str, Any]:
    """阶段 6：build_context + write_report。

    TODO(阶段 6)：返回 {report, sources, cost_report}
    """
    raise NotImplementedError("阶段 6：实现 research_report")


def full_research(query: str, *, cfg: Config | None = None) -> dict[str, Any]:
    """阶段 8：完整链路，落盘到 outputs/。

    TODO(阶段 8)：
      1. plan_research 拆子问题
      2. 子问题并发跑 search + scrape，加并发上限
      3. URL 全局去重（参考 researcher.py:813 _get_new_urls）
      4. 单个子问题失败只跳过并记录，不中断整轮
      5. 汇总上下文 -> 成稿 -> 写 outputs/<时间戳>.md
      6. 返回 {"path": ..., "cost_report": ...}
    """
    raise NotImplementedError("阶段 8：实现 full_research")