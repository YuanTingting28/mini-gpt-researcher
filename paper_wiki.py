"""arXiv 论文 -> 中文结构化卡片。

位置：curator 之后、select_context 之前。
卡片不是最终报告，只是把英文论文整理成中文可检索、可引用的证据。
英文原文必须保留，事实核查以原文为准。
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from agents import NO_THINKING
from config import Config, load_config
from llm import chat

logger = logging.getLogger(__name__)

NO_THINKING = {"reasoning_effort":"none"}
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)

def is_arxiv(url:str) -> bool:
    return "arxiv.org" in (url or "").lower()

def _clean_json(text:str)->str:
    return _JSON_FENCE.sub("",(text or "").strip()).strip()

#这里是提取论文的信息界面
def analyze_paper(page:dict[str,Any],*,cfg:Config) -> dict[str,Any] | None:
    text = (page.get("raw_content_original") or page.get("raw_content") or "")
    text = text[:cfg.paper_wiki_chars]
    if not text.strip():
        return None

    prompt = f"""你是论文分析员。只根据下面的论文原文生成中文结构化卡片。
    不要补充原文没有的内容；找不到的字段填 null 或空列表。

    论文标题：{page.get("title", "")}
    论文原文：
    {text}

    输出一个 JSON 对象，字段固定为：
    {{
      "title_zh": "中文标题",
      "problem": "研究问题",
      "method": "核心方法",
      "contributions": ["贡献1", "贡献2"],
      "experiments": [{{"setup": "数据集/设置", "metric": "指标", "result": "结果"}}],
      "limitations": ["局限1"],
      "key_quotes": [{{"en": "原文英文原句", "zh": "中文解释", "section": "章节或位置"}}],
      "relevance": "这篇论文对当前研究任务的价值"
    }}

    要求：
    1. 所有数字、数据集、指标必须来自原文；没有就写 null，不要猜。
    2. key_quotes 至少 2 条，必须是原文英文原句，不得改写。
    3. 中文术语首次出现写成「中文（English）」。
    4. 只输出 JSON，不要解释。
    """
    try:
        raw = chat(
            [{"role":"user","content":prompt}],
            cfg=cfg,
            model = cfg.fast_llm,
            temperature=0.0,
            max_tokens=cfg.paper_wiki_max_tokens,
            json_mode=True,
            extra_body=NO_THINKING,
            step="论文卡片",
        )
        data = json.loads(_clean_json(raw))
    except Exception as exc:
        logger.warning("论文卡片失败，保留原文 %s：%s", page.get("url"), exc)
        return None

    if not isinstance(data,dict):
        return None

    data["arxiv_url"] = page.get("url","")
    data["title_en"] = page.get("title","")
    return data

def render_card(card:dict[str,Any]) -> str:
    lines = [f"## 论文卡片：{card.get('title_zh') or card.get('title_en') or ''}"]
    if card.get("title_en"):
        lines.append(f"- 英文标题：{card['title_en']}")
    if card.get("arxiv_url"):
        lines.append(f"- arXiv：{card['arxiv_url']}")

    for label,key in (
        ("研究问题","problem"),
        ("核心方法","method"),
        ("与研究任务的关系","relevance"),
    ):
        if card.get(key):
            lines.append(f"- {label}：{card[key]}")
    for label, key in (("主要贡献", "contributions"), ("局限", "limitations")):
        values = card.get(key) or []
        if values:
            lines.append(f"- {label}：")
            lines.extend(f"  - {value}" for value in values)

    experiments = card.get("experiments") or []
    if experiments:
        lines.append("- 实验：")
        for item in experiments:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"  - 设置：{item.get('setup') or '未说明'}；"
                f"指标：{item.get('metric') or '未说明'}；"
                f"结果：{item.get('result') or '未说明'}"
            )

    quotes = card.get("key_quotes") or []
    if quotes:
        lines.append("- 可引用原文：")
        for quote in quotes:
            if not isinstance(quote, dict) or not quote.get("en"):
                continue
            lines.append(f"  - “{quote['en']}”（{quote.get('section') or '原文'}）")
            if quote.get("zh"):
                lines.append(f"    中文：{quote['zh']}")

    return "\n".join(lines)

def enrich_papers(
    pages: list[dict[str, Any]],
    *,
    cfg: Config | None = None,
) -> list[dict[str, Any]]:
    """只增强 arXiv 页面；失败原样返回，绝不丢页。"""
    cfg = cfg or load_config()
    if not cfg.paper_wiki:
        return pages

    targets = [
        page for page in pages
        if is_arxiv(page.get("url", "")) and not page.get("paper_card")
    ][: cfg.paper_wiki_max]

    if not targets:
        return pages

    def run(page: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return page, analyze_paper(page, cfg=cfg)

    with ThreadPoolExecutor(
        max_workers=min(cfg.paper_wiki_concurrency, len(targets))
    ) as pool:
        futures = [pool.submit(run, page) for page in targets]
        for future in as_completed(futures):
            try:
                page, card = future.result()
            except Exception as exc:
                logger.warning("论文卡片异常，保留原文：%s", exc)
                continue

            if not card:
                continue

            original = page.get("raw_content") or ""
            page["raw_content_original"] = original
            page["paper_card"] = card
            page["raw_content"] = (
                render_card(card)
                + "\n\n## 原文节选\n"
                + original[: cfg.paper_wiki_excerpt_chars]
            )

    return pages