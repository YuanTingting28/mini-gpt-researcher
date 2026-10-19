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
import dataclasses
import logging
import re
from concurrent import futures
from typing import Any
from llm import chat
from prompts import quick_summary_prompt,search_queries_prompt
from concurrent.futures import ThreadPoolExecutor,as_completed
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
    results = search_many(query,cfg)
    if not results:
        # 阶段8会一次跑很多子查询，这里不能抛异常
        return "检索没有返回任何结果，无法生成摘要。"
    #品上下文
    context = format_context(results)
    logger.info("检索到 %d 条来源，上下文共 %d 字符", len(results), len(context))
    prompt = quick_summary_prompt(query,context)
    return chat([{"role":"user","content":prompt}],cfg=cfg,step="摘要")


def plan_research(query: str, *, cfg: Config | None = None,expert:Any=None) -> list[str]:
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
    from agent_creator import planning_persona

    prompt = search_queries_prompt(query,cfg.max_sub_queries,persona=planning_persona(expert))
    response = chat([{"role":"user","content":prompt}],cfg=cfg,step="子问题")
    parsed = parse_json(response)
    if parsed is None:
        logger.warning("子问题json解析失败，退化为原始查询，模型输出前200字:%r",response[:200])
    sub_queries = normalize_sub_queries(parsed,query)
    #数量上限由代码保证
    sub_queries = sub_queries[:cfg.max_sub_queries]
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
    from context import select_context
    cfg = cfg or load_config()
    pages = _collect_pages([query],cfg=cfg)
    if not pages:
        logger.warning("全部抓取失败，没有可用上下文")
        return {"context": "", "sources": [], "raw_chars": 0}
    raw_chars = sum(len(p["raw_content"]) for p in pages)
    context = select_context(query,pages,cfg=cfg)
    logger.info(
        "抓取 %d 页 / %d 字符 -> 上下文 %d 字符",
        len(pages),
        raw_chars,
        len(context),
    )
    return {"context": context, "sources": pages, "raw_chars": raw_chars}

#----并发阶段
def _normalize_url(url:str)->str:
    """URL归一化 只用于去重比较，不用于抓取
    去掉片段（#...) 同一个页面的不同锚点不该抓取两次
    去掉末尾斜杠
    保留query 因为很多站点的正文靠query区分
    """
    base = (url or "").strip().split("#",1)[0]
    while base.endswith("/") and base.count("/")>2:
        base = base[:-1]
    return base
#这个确实，检索不出的下次也别检索了
#: 抓不出正文的 URL（用 _normalize_url 归一化后作键）-> 上次的原因。
#: 只记确定性失败：HTTP 4xx/5xx、PDF 没抽文字、非网页、拦截页、正文过短。
#: 超时/网络错误是瞬时失败，不记 —— 下一轮值得再试。
#: 实测（2026-10-11 三轮跑）：同一批 403 / 正文过短的链接每轮都被重新排队重抓，
#: 既占用 MAX_PAGES 名额，又要白等一遍超时。
_DEAD_URLS: dict[str,str] = {}

def dead_urls()->dict[str,str]:
    """已确认抓不出正文的 URL 快照（自检/调试用）。"""
    return dict(_DEAD_URLS)

def reset_dead_urls()->None:
    """清空负缓存。长驻进程里如果想「过一段时间再试一次」，调这个。"""
    _DEAD_URLS.clear()

def _retriever_names(cfg:Config) ->tuple[str,...]:
    """这次用哪些检索源。兼容没有retrievers字段的假cfg（自检里到处是）"""
    names = getattr(cfg,"retrievers",None) or (cfg.retriever,)
    return tuple(n for n in names if n) or (cfg.retriever,)

def _search_with(name:str,sub_query:str,cfg:Config) -> list[dict[str,Any]]:
    """单个检索源头。单独抽出来是为了多源时能被线程池调度、也方便打桩。"""
    return get_retriever(name,sub_query).search(cfg.max_search_results)

def _search_many(sub_query:str,names:tuple[str,...],cfg:Config)->list[dict[str,Any]]:
    """
    多源并发检索 -》 按配置顺序合并去重 （保证确定性）
    顺序为什么必须是names的顺序而不是完成顺序；
    检索先完成的是“快的源”，不是“该排前面的源”。而[1]..[n]的编号是引用
    体系的根，一旦顺序随机，同一批查询两次跑出来引用编号就对不上。
    报告也就不可复现。并发只发生在取数据这一步，合并必须确定性。
    """
    hits:dict[str,list[dict[str,Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1,len(names))) as  pool:
        futures = {pool.submit(_search_with,name,sub_query,cfg):name for name in names}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                hits[name] = fut.result() or []
            except Exception as exc:
                logger.warning("检索源 %s 失败，跳过（其他源继续）：%s", name, exc)
    merged:list[dict[str,Any]] = []
    seen:set[str] = set()
    for name in names:
        for item in hits.get(name,[]):
            url = str(item.get("href") or item.get("url") or "").strip()
            key = _normalize_url(url)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    logger.info(
        "子问题「%s」：%s -> 合并 %d 条",
        (sub_query or "")[:40],
        "/".join(names),
        len(merged),
    )
    return merged

def _search_one(sub_query:str,cfg:Config)->list[dict[str, Any]]:
    """单个子问题的检索。抛异常由调用方决定怎么处理。

    阶段17：配了多个源就并发跑再合并；只有一个源时走老路径，
    不额外起线程池，行为和阶段8 完全一致。
    """
    names = _retriever_names(cfg)
    if len(names) == 1:
        return _search_with(names[0], sub_query, cfg)
    return _search_many(sub_query, names, cfg)

"""
用 ThreadPoolExecutor 并发跑每个源；
某个源抛异常只跳过它自己；
合并时按 cfg.retrievers 的顺序，不按完成顺序，保证 [1]..[n] 引用编号可复现；
用 _normalize_url 跨源去重。
"""

def search_many(sub_query:str,cfg:Config,*,max_result:int | None = None) -> list[dict[str,Any]]:
    """公开入口：按cfg.retrievers 多源并发检索 按配置顺序合并去重
    max_results 只影响这一次调用 不修改传进来的 cfg
    """
    if max_result is not None:
        cfg = dataclasses.replace(cfg,max_search_results=max_result)
    names = _retriever_names(cfg)
    if len(names) == 1:
        return _search_with(names[0],sub_query, cfg)
    return _search_many(sub_query,names,cfg)

def _fetch_one(url:str,search_title:str,cfg:Config)->dict[str,Any] | None:
    from scrape import fetch
    #缓存检索不出的url
    key = _normalize_url(url)
    if key in _DEAD_URLS:
        logger.info("跳过已知抓不出正文的 URL %s（%s）", url, _DEAD_URLS[key])
        return None
    page = fetch(url,cfg=cfg)
    if not page["ok"]:
        logger.info("抓取失败，跳过 %s（%s）", url, page["reason"])
        return None
    return {
        "title": page["title"] or search_title or "",
        "url": page["url"],
        "raw_content": page["raw_content"],
    }

def _collect_pages(sub_queries:list[str],cfg:Config)->list[dict[str, Any]]:
    """并发检索+全局去重+并发抓取，返回[按首次出现顺序]排好的页面列表
    单独抽出来是为了能离线测试：_search_one/_fetch_one 可以被打桩替换
    """
    #  --- 1.并发检索，每个子问题一次search
    hits_by_query:dict[str,list[dict[str, Any]]] = {}
    if not sub_queries:
        return []
    workers = max(1,min(cfg.max_concurrency,len(sub_queries)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_search_one,sub,cfg):sub for sub in sub_queries}
        for fut in as_completed(futures):
            sub = futures[fut]
            try:
                hits_by_query[sub] = fut.result() or []
            except Exception as exc:
                logger.warning("子问题检索失败，跳过「%s」：%s", sub, exc)
    # ----2.全局去重 跨子问题 同一个URL只抓一次
    urls:list[str] = []
    titles:dict[str, str] = {}
    seen:set[str] = set()
    for sub in sub_queries:
        for item in hits_by_query.get(sub,[]):
            url = (item.get("url") or item.get("href") or "").strip()
            key = _normalize_url(url)
            if not key or key in seen:
                continue
            if key in _DEAD_URLS:
                continue  # 已知抓不出正文，别占 MAX_PAGES 的名额
            seen.add(key)
            urls.append(url)
            titles[url] = item.get("title") or ""
    if len(urls)>cfg.max_pages:
        logger.info("去重后 %d 个 URL 超过 MAX_PAGES=%d，截断", len(urls), cfg.max_pages)
        urls = urls[:cfg.max_pages]
    logger.info("%d 个子问题 -> 去重后 %d 个 URL 待抓取", len(hits_by_query), len(urls))
    if not urls:
        return []
    # ------3 并发抓取
    fetched:dict[str,dict[str, Any] | None] = {}
    workers = max(1,min(cfg.max_concurrency,len(urls)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_one,url,titles.get(url,""),cfg):url for url in urls
        }
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                fetched[url] = fut.result()
            except Exception as exc:
                logger.warning("抓取异常，跳过 %s：%s", url, exc)
                fetched[url] = None

    # ------4.按检索顺序回填
    #并发完成顺序是乱的，而上下文[n]的编号是引用体系的根，必须可复现，所以这里不按完成顺序，而是urls的顺序
    pages:list[dict[str, Any]] = []
    seen_final:set[str] = set()
    for url in urls:
        page = fetched.get(url) #通过url取，也按url的顺序来
        if page is None:
            continue
        key = _normalize_url(page["url"])
        if key in seen_final:
            # 不同短链重定向到同一页，抓完才发现撞车
            logger.info("重定向撞车，跳过 %s", page["url"])
            continue
        seen_final.add(key)
        pages.append(page)
    return pages

def research_report(query: str, *, cfg: Config | None = None, with_costs: bool = False) -> dict[str, Any]:
    """阶段 6：build_context + write_report。

    TODO(阶段 6)：返回 {report, sources, cost_report}
    """
    from report import parse_context_sources,save_report,write_report
    cfg = cfg or load_config()
    built = build_context(query,cfg=cfg)
    context = built["context"]
    if not context.strip():
        # 对照源码：gpt_researcher/skills/writer.py:82 —— 一条资料都没有时
        # 宁可不写，也不能凭空生成一篇"看起来有来源"的报告。
        logger.warning("没有抓到任何可用资料，不出报告")
        return {"report": f'没有为"{query}"抓到任何可用资料', "sources": [], "cost_report": "","path":None}
    sources = parse_context_sources(context)
    report = write_report(query,context,sources, cfg=cfg)
    path = save_report(query,report)
    logger.info("报告已写入 %s（%d 字，%d 个来源）", path, len(report), len(sources))

    cost_report = ""
    if with_costs:
        cost_report = _cost_report_hook(cfg)
    return {
        "report": report,
        "sources": sources,
        "cost_report": cost_report,
        "path": str(path),
    }

def _cost_report_hook(cfg: Config) -> str:
    """阶段 7 的接入点：CostTracker 还没实现，返回提示而不是抛异常。"""
    from costs import CostTracker
    from llm import USAGE_LOG
    try:
        tracker = CostTracker()
        for item in USAGE_LOG:
            tracker.add(item["model"], item["prompt_tokens"], item["completion_tokens"],step=item.get("step", "default"),
                cache_hit_tokens=item.get("cache_hit_tokens", 0),
                reasoning_tokens=item.get("reasoning_tokens", 0))
        return tracker.report()
    except NotImplementedError:
        return "（成本统计属于阶段 7，尚未实现）"

def full_research(query: str, *, cfg: Config | None = None,
                  curate:bool | None=None) -> dict[str, Any]:
    """阶段 8：完整链路，落盘到 outputs/。
    阶段 8：拆子问题 -> 并发检索 -> 全局去重 -> 并发抓取 -> 一次压缩 -> 成稿落盘。

    对照源码：gpt_researcher/skills/researcher.py:97 conduct_research（主循环）
    与上游的差别：上游把子问题塞进 asyncio 事件循环 + rate_limiter；我们全是同步
    代码，用线程池是最小改动。代价是没有细粒度限流，MAX_CONCURRENCY 是唯一的阀门。

    TODO(阶段 8)：
      1. plan_research 拆子问题
      2. 子问题并发跑 search + scrape，加并发上限
      3. URL 全局去重（参考 researcher.py:813 _get_new_urls）
      4. 单个子问题失败只跳过并记录，不中断整轮
      5. 汇总上下文 -> 成稿 -> 写 outputs/<时间戳>.md
      6. 返回 {"path": ..., "cost_report": ...}
    """
    from context import select_context
    from report import  parse_context_sources,save_report,write_report
    cfg = cfg or load_config()
    sub_queries = plan_research(query,cfg=cfg)
    pages = _collect_pages(sub_queries,cfg=cfg)
    #阶段13 抓完先让来源管家筛选一边，再进入压缩
    curation:dict[str,Any] | None = None
    if curate is None:
        curate = cfg.curate_sources
    if curate and pages:
        from curator import curate_pages
        curation = curate_pages(query,pages,cfg=cfg)
        if curation["ok"]:
            logger.info("来源管家：保留 %d/%d 篇", len(curation["pages"]), len(pages))
        else:
            # 局部失败降级：筛不成不是错误，退回未筛选接着跑（见 curator.py 抬头的理由）
            logger.warning("来源管家没筛成，按未筛选继续：%s", curation["reason"])
        pages = curation["pages"]

    if not pages:
        # 全局失败停下，但要把原因说清楚（局部失败才降级）
        logger.warning("一个页面都没抓到，不出报告")
        return {
            "report": f'没有为"{query}"抓到任何可用资料',
            "sources": [],
            "cost_report": _cost_report_hook(cfg),
            "path": None,
            "sub_queries": sub_queries,
            "curation": curation,
        }
    raw_chars = sum(len(p["raw_content"]) for p in pages)
    #用[原问题+子问题]当检索词，子问题是模型按检索需要生成的
    filter_query = " ".join([query,*sub_queries])
    #分析论文z
    from paper_wiki import enrich_papers
    pages = enrich_papers(pages, cfg=cfg)
    context = select_context(filter_query,pages,cfg=cfg)
    logger.info("抓取 %d 页 / %d 字符 -> 上下文 %d 字符", len(pages), raw_chars, len(context))
    if not context.strip():
        logger.warning("压缩后上下文为空，不出报告")
        return {
            "report": f'没有为"{query}"找到可用上下文',
            "sources": pages,
            "cost_report": _cost_report_hook(cfg),
            "path": None,
            "sub_queries": sub_queries,
            "curation": curation,
        }
    sources = parse_context_sources(context)
    report = write_report(query,context,sources, cfg=cfg)
    path = save_report(query,report)
    logger.info("报告已写入 %s（%d 字，%d 个来源）", path, len(report), len(sources))
    return {
        "report": report,
        "sources": sources,
        "cost_report": _cost_report_hook(cfg),
        "path": str(path),
        "sub_queries": sub_queries,
        "curation": curation,
    }