"""
阶段14的工具层：把已经建好的研究链路 包成 给别的AI应用调用的三个动作

为什么和 mcp_server.py 分成两个文件：
    MCP 只是**一层壳**。真正干活的是这三个函数，它们完全不 import mcp，
    所以能在没装 SDK 的环境里单独自检（见 check_stage14.py 第 1 组）。
    壳一旦和逻辑长在一起，"协议对不对"和"研究跑不跑得通"就绑死了 ——
    任一个坏掉，你都看不出是哪个坏的。

对照源码：上游的 MCP server 已经拆到独立仓库 assafelovic/gptr-mcp，
    本地仓库只剩文档（docs/docs/gpt-researcher/mcp-server/*.md）和
    "把别人的 MCP 工具当检索源"的客户端（gpt_researcher/mcp/）。
    所以这里按文档里公开的工具契约对齐：
        quick_search       上游同名                                  快，只给检索结果
        deep_research      上游同名                                  慢，端到端出报告
        research_sources   上游 research + get_research_sources + get_research_context 三合一

和上游的一处分歧：上游把"研究"和"写报告"拆成两个工具，靠服务端保存的
    researcher.context 串起来 —— 那是有状态的（调用顺序错了就拿到空上下文，
    而且并发两个请求会互相踩）。我们的 full_research 本来就是一条链，
    拆开只会多一份要维护的会话状态，收益为零。所以 deep_research 一步到位，
    另外单独给一个 research_sources 把"证据"交出去（宿主模型想自己写就用它）。
"""
from __future__ import annotations

import logging
from typing import Any

from config import Config, load_config

logger = logging.getLogger(__name__)

#: 每条检索结果给模型看的摘要长度。工具返回值最终会进宿主的上下文窗口，
#: 10 条 × 全文摘要是纯浪费 —— 想深挖的应该去调 research_sources。
MAX_SNIPPET_CHARS = 200

def quick_search(query:str,*,max_results:int=6,
                 cfg:Config | None= None,) -> str:
    """快速检索：只返回搜索结果的标题、链接、摘要
    不抓正文、不调模型，所以是秒级的，适合先看看有哪些来源
    """
    cfg = cfg or load_config()
    from search import get_retriever
    results = get_retriever(cfg.retriever,query).search(max_results) or []
    if not results:
        #明确说没找到 否则和调用成功但结果为空 没办法却分 模型会以为真没有
        return f"没有为{query}检索到任何结果（检索器{cfg.retriever}）。"
    blocks = []
    for i,item in enumerate(results[:max_results],1):
        title = item.get("title") or "(无标题)"
        url =  item.get("url") or item.get("href") or ""
        body = (item.get("body") or item.get("content") or "").strip()
        body = " ".join(body.split())[:MAX_SNIPPET_CHARS]
        blocks.append(f"[{i}] {title}\n    {url}\n    {body}")
    logger.info("quick_search「%s」-> %d 条", query, len(blocks))
    return "\n".join(blocks)

def research_sources(query:str,*,max_pages: int | None = None ,cfg:Config | None= None) -> str:
    """
    检索+抓取+压缩 把“能直接进提示词的证据交出去，但是不写报告”
    宿主模型 想用自己的上下文窗口组织答案、或者拿证据做二次推理时用这个
    返回的正文里每块都带[n]编号
    :param query:
    :param max_pages:
    :param cfg:
    :return:
    """
    from context import select_context
    from pipeline import _collect_pages,plan_research

    cfg = cfg or load_config()
    sub_queries = plan_research(query, cfg=cfg)
    pages = _collect_pages(sub_queries,cfg=cfg)
    if max_pages is not None and len(pages) > max_pages:
        logger.info("抓到 %d 页，按 max_pages=%d 截断", len(pages), max_pages)
        pages = pages[:max_pages]
    if not pages:
        return f'没有为"{query}"抓到任何可用页面，这次拿不到证据。'

    context = select_context(" ".join([query,*sub_queries]),pages,cfg=cfg)
    if not context.strip():
        return f'抓到了 {len(pages)} 个页面，但压缩后没有留下可用证据。'
    logger.info(
        "research_sources「%s」-> %d 页 / 证据 %d 字符", query, len(pages), len(context)
    )
    return (
        f"研究任务：{query}\n"
        f"子问题：{'；'.join(sub_queries)}\n"
        f"抓取 {len(pages)} 页，下面是压缩后的证据（[n] 是引用编号）：\n\n" + context
    )

def deep_research(query:str,*,cfg:Config | None= None) -> str:
    """
    端到端：拆子问题-》检索 -》抓正文 -》压缩 -》出一篇带引用的报告，并落盘
    这是耗时的一步 只想要证据，并且自己写答案，用 reasearch_sources
    :param query:
    :param cfg:
    :return:
    """
    from pipeline import full_research
    cfg = cfg or load_config()
    result = full_research(query,cfg=cfg)
    report = (result.get("report") or "").strip()
    path = result.get("path")
    if not path:
        # 没落盘 = 这条链上某个"全局失败"发生了（一个页面都没抓到 / 压缩后为空）。
        # full_research 已经把原因塞进 report 了，原样交出去 —— 不要编一份"看起来像报告"
        # 的东西糊弄宿主，那是这个项目最忌讳的静默失败。
        return f"这次没有产出报告：{report}"
    lines: list[str] = [report, "", "---", f"（完整报告已落盘：{path}）"]
    sources:list[dict[str,Any]] = result.get("sources") or []
    if sources:
        lines += ["","## 来源"]
        for s in sources:
            lines.append(
                f"- [{s.get('n')}] {s.get('title') or '(无标题)'} — {s.get('url') or ''}"
            )
    logger.info(
        "deep_research「%s」-> %d 字 / %d 个来源", query, len(report), len(sources)
    )
    return "\n".join(lines)
