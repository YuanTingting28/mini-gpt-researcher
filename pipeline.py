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
import re
from typing import Any
from llm import chat
from prompts import quick_summary_prompt,search_queries_prompt
from config import Config, load_config
from search import get_retriever
import json
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
def parse_json(text: str) -> Any:
    """从模型输出里尽力抠出 JSON 结构，抠不出来返回 None。

    四层，从便宜到贵。实测前三种就能覆盖绝大多数情况，
    最后那层 json_repair 是可选的（处理单引号、缺引号这类硬伤）。
    """
    text = (text or "").strip()
    if not text:
        return None

    candidates = [text]

    # 第 2 层：剥掉 ```json ... ``` 外壳
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())

    # 第 3 层：截取第一个 [ 或 { 到最后一个 ] 或 }
    # 对付"好的，结果如下：[...] 希望对你有帮助"这种前后带废话的
    starts = [i for i in (text.find("["), text.find("{")) if i != -1]
    if starts:
        start = min(starts)
        end = max(text.rfind("]"), text.rfind("}"))
        if end > start:
            candidates.append(text[start:end + 1])

    for raw in candidates:
        # 每个候选都试两次：原样、以及去掉尾逗号之后
        for attempt in (raw, re.sub(r",\s*([\]}])", r"\1", raw)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue

    # 第 4 层：json_repair（可选依赖，处理单引号等硬伤）
    try:
        import json_repair

        return json_repair.loads(text)
    except ImportError:
        logger.warning("json_repair 未安装，最后一层修复不可用（pip install json_repair）")
    except Exception as exc:
        logger.warning("json_repair 也解析失败：%s", exc)

    return None

def normalize_sub_queries(parsed:Any,fallback_query:str)->list[str]:
    """把模型返回的各种畸形结构统一成list[str] 空则兜底成[原始查询]
    对照源码:
    必须能处理:list / {"queries":[...]} / {"query":"..."} /"裸字符串" /None
    1. 显式跳过 None 和嵌套结构。原写法用 str(item) 会把 None 变成
         字符串 "None"，然后作为一个无意义的子查询被送去搜索。
    2. 去重且保序。模型偶尔会生成重复查询，重复检索纯属浪费。
    """
    #-----dict 找到装列表的键
    if isinstance(parsed,dict):
        for key in ("queries","sub_queries","subQueries","items"):
            value = parsed.get(key)
            if isinstance(value,list):
                parsed = value
                break
        else:
            # for-else：只有循环没被 break 时才执行，即四个键都不是列表
            single = parsed.get("query")
            parsed = [single] if isinstance(single, str) else []
    if isinstance(parsed,str):
        parsed = [parsed] if parsed.strip() else []

    #---其他类型(None /int/..)
    if not isinstance(parsed,list):
        parsed = []
    #---元素级清洗
    queries:list[str] = []
    for item in parsed:
        if item is None or isinstance(item,(dict,list)):
            continue
        text = str(item).strip()
        if text:
            queries.append(text)
    queries = list(dict.fromkeys(queries))  # 把列表的元素作为字典的键
    # --- 兜底：绝不能返回空列表
    if not queries and fallback_query.strip():
        return [fallback_query.strip()]
    return queries

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
    prompt = quick_summary_prompt(query,context)
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
    cfg = cfg or load_config()
    prompt = search_queries_prompt(query,cfg.max_sub_queries)
    response = chat([{"role":"user","content":prompt}],cfg=cfg)
    parsed = parse_json(response)
    if parsed is None:
        logger.warning("子问题json解析失败，退化为原始查询，模型输出前200字:%r",response[:200])
    sub_queries = normalize_sub_queries(parsed,query)
    #数量上限由代码保证
    sub_queries = sub_queries[:cfg.max_sub_queries]
    if sub_queries == [query]:
        if sub_queries == [query]:
            logger.warning("模型未产出有效子问题，将直接检索原始查询")
        else:
            logger.info("拆出 %d 个子问题：%s", len(sub_queries), sub_queries)

    return sub_queries


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