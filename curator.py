"""
阶段13 来源管家：给抓回来的页面打分排序，决定 哪些来源配进上下文
curator 在整条链上的位置，抓取之后、压缩之前。它管的是 哪些页面
1.不让模型把正文抄回来，只让它交编号
上游提示词要求模型 按原样的JSON 列表格式返回 也就是把完整的source_data 原封不动回声一边，模型只负责删掉几条
很贵
脏：只要让模型抄，就会顺手改写 顺手压缩 顺手补充
改成只回编号 输出只剩下几十个token
2. 失败一律放行（fail-open）。
   管家调用挂了、JSON 坏了、给了不存在的编号 —— 都把原始页面**原样**返回。
   管家是"筛"，不是"证"：筛错方向的代价是上下文里多几篇无关材料，
   而一旦筛成空集，整轮研究白跑。后者贵得多，所以降级方向是"退回未筛选"。
为什么单独一个文件：和 fact_checker.py 同理 —— 它有自己的提示词、自己的解析、
自己的失败策略；塞进 pipeline.py 只会让编排层继续膨胀。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from agents import NO_THINKING
from config import Config, load_config
from llm import chat
from prompts import curate_sources_prompt

logger = logging.getLogger(__name__)
#: 管家只回编号，几十个 token 就够。给 1000 是防它絮叨；
#: 配合 NO_THINKING（不要让一个分类任务去花推理 token）。
CURATE_MAX_TOKEN = 1000

#: 目录里每页给模型看的开头长度。给全量正文就等于把钱交给最贵的那个环节，
#: 而"这页跑不跑题"看开头几百字基本就定了。
DEFAULT_SNIPPET_CHARS = 600

#: 模型写 dropped 理由的格式："3: 与任务无关"
_DROP_RE = re.compile(r"^\s*(\d+)\s*[:：]\s*(.*)$")

def _catalog(pages:list[dict[str, Any]],snippet_chars:int) -> str:
    """把页面列表 编成 给模型 看的 目录：编号 + 标题 + URL + 长度 +开头
    长度一定要写进去：一页80字的“来源”和一页“8000字的来源
    光看开头几百字是一样长的，得让模型知道它是不是个空壳
    """
    blocks = []
    for i,page in enumerate(pages,1):
        text = page.get("raw_content") or ""
        #用正则把 text 里所有连续空白字符（空格、换行 \n、制表符 \t、回车 \r 等）替换成单个空格。
        snippet = re.sub(r"\s+"," ",text).strip()[:snippet_chars]
        blocks.append(
            f"[{i}] {page.get('title') or '(无标题)'}\n"
            f"URL: {page.get('url') or ''}\n"
            f"长度: {len(text)} 字符\n"
            f"开头: {snippet}"
        )
    return "\n\n".join(blocks)

def _normalize_keep(raw:Any,n:int) -> list[int]:
    """把模型给的keep列表 西城 合法的、去重的、保序的编号

    挡掉的东西：不是列表（上层已挡）、元素不是 int、bool（bool 是 int 的子类，
    True 会被当 1）、越界、重复。返回空列表表示"一条能用的都没有"。
    """
    if not isinstance(raw,list):
        return []
    order:list[int] = []
    for item in raw:
        if isinstance(item,bool) or not isinstance(item,int):
            continue
        if 1<=item<=n and item not in order:
            order.append(item)
    return order

def _parse_drop_reasons(raw: Any) -> dict[int, str]:
    """模型给的 dropped 是给人看的（"3: 与任务无关"），只用于展示，不影响取舍。"""
    out: dict[int, str] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            continue
        m = _DROP_RE.match(str(item))
        if m:
            out[int(m.group(1))] = m.group(2).strip()
        elif isinstance(item, int):
            out[item] = ""
    return out

def _brief(n: int, page: dict[str, Any], why: str = "") -> dict[str, Any]:
    return {
        "n": n,
        "title": page.get("title") or "(无标题)",
        "url": page.get("url") or "",
        "why": why,
    }

def curate_pages(
        query:str,
        pages:list[dict[str, Any]],
        *,
        cfg:Config | None = None,
        max_results:int | None = None,
        snippet_chars:int | None = None,
        chat_fn:Callable[...,str] | None = None,
) -> dict[str, Any]:
    """让来源管家筛一遍页面 返回最终该用的那批
    返回 {"pages","kept","dropped","ok","reason"}:
    pages   —— 最终要用的页面（失败时就是传进来的原样）
      kept    —— [{n, title, url, why}]，n 是**传入顺序**里的编号，方便和日志对账
      dropped —— [{n, title, url, why}]
      ok      —— 这一步是否真的筛成了（False = 退回未筛选，原因在 reason）
      reason  —— 失败原因 / 跳过理由 / 成功时为空
    chat_fn 只是为了离线自建能打桩 正常调用
    """
    cfg = cfg or load_config()
    chat_fn =  chat_fn or chat
    if max_results is None:
        max_results = cfg.curate_max_results
    if snippet_chars is None:
        snippet_chars = cfg.curate_snippet_chars
    original = list(pages)
    out: dict[str, Any] = {
        "pages": original,
        "kept": [_brief(i, p) for i, p in enumerate(original, 1)],
        "dropped": [],
        "ok": True,
        "reason": "",
    }
    # 一篇来源没有"筛选"可言。上游也没这个短路，但这是白花一次调用的钱。
    if len(original) <= 1:
        out["reason"] = f"只有{len(original)} 篇来源，跳过筛选"
        return out

    prompt = curate_sources_prompt(query,_catalog(original,snippet_chars),max_results)
    try:
        raw = chat_fn(
            [{"role":"user","content":prompt}],
            cfg=cfg,
            temperature=0.0,
            max_tokens=CURATE_MAX_TOKEN,
            json_mode=True,
            step = "来源管家",
            extra_body=NO_THINKING,
        )
    except Exception as exc:
        #局部失败降级 管家 挂了 不该让已经抓好的页面作废
        logger.warning("来源管家调用失败，退回未筛选的%d篇来源：%s",len(original),exc)
        out["ok"] = False
        out["reason"] = f"来源管家调用失败:{exc}"
        return out

    from pipeline import parse_json
    parsed = parse_json(raw)
    if not isinstance(parsed,dict) or "keep" not in parsed:
        logger.warning("来源管家返回的不是{keep:[...]},退回未筛选（原文前200字：%r)",raw[:200])
        out["ok"] = False
        out["reason"] = "管家返回的JSON里没有keep列表"
        return out
    order = _normalize_keep(parsed.get("keep"),len(original))
    if not order:
        # 注意这里和"模型说全都不要"是同一件事 —— 都不接受。
        # 管家的职责是排序和剔重，不是把上下文清空。
        logger.warning(
            "来源管家的 keep 里没有一条合法编号（%r），退回未筛选", parsed.get("keep")
        )
        out["ok"] = False
        out["reason"] = "管家给的编号全部越界或非法"
        return out
    if len(order) > max_results:
        logger.info("来源管家保留 %d 篇，超过上限 %d，截断", len(order), max_results)
        order = order[:max_results]
    kept_pages = [original[i-1] for i in order]

    reasons = _parse_drop_reasons(parsed.get("dropped"))
    out["pages"] = kept_pages
    out["kept"] = [_brief(i,original[i-1],reasons.get(i,"")) for i in order]
    out["dropped"] = [
        _brief(i,original[i-1],reasons.get(i,""))
        for i in range(1,len(original)+1)
        if i not in order]

    logger.info(
        "来源管家：保留 %d/%d 篇（丢弃 %s）",
        len(kept_pages),
        len(original),
        [d["n"] for d in out["dropped"]] or "无",
    )
    return out