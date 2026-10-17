"""命令行入口。每个子命令对应 ROADMAP 里的一个阶段。

用法：
    python main.py check
    python main.py check --offline      # 不联网，只验配置
    python main.py search "查询词"
    python main.py summary "问题"
    python main.py plan "复杂问题"
    python main.py fetch "https://..."
    python main.py context "问题"
    python main.py report "问题" --costs
    python main.py research "问题"

阶段划分见 ROADMAP.md。未实现的子命令会提示对应阶段。
"""

from __future__ import annotations

import argparse
import sys

from config import load_config
import logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
# ---------------------------------------------------------------- 阶段 0 自检

#: 阶段 0 必需的依赖：导入名 -> 说明
REQUIRED_DEPS = {"openai": "调用大模型", "dotenv": "读取 .env"}

#: 后续阶段才需要的依赖
OPTIONAL_DEPS = {
    "requests": "阶段 4 抓取网页",
    "bs4": "阶段 4 解析 HTML",
    "lxml": "阶段 4 HTML 解析器",
    "ddgs": "阶段 1 免 key 检索",
    "tiktoken": "阶段 7 估算 token",
    "json_repair": "阶段 3 修复畸形 JSON",
}


def _check_python() -> tuple[str, str]:
    import platform
    version = platform.python_version()
    if sys.version_info[:2] < (3, 10):
        return "FAIL", f"{version}（本项目用了 `X | None` 写法，需要 3.10+）"
    return "OK", version


def _check_deps() -> list[tuple[str, str, str]]:
    import importlib

    out = []
    for name, why in REQUIRED_DEPS.items():
        try:
            mod = importlib.import_module(name)
            out.append((f"依赖 {name}", "OK", f"{getattr(mod, '__version__', '?')}（{why}）"))
        except ImportError:
            out.append((f"依赖 {name}", "FAIL", f"未安装（{why}）—— pip install -r requirements.txt"))

    for name, why in OPTIONAL_DEPS.items():
        try:
            mod = importlib.import_module(name)
            out.append((f"依赖 {name}", "OK", f"{getattr(mod, '__version__', '?')}（{why}）"))
        except ImportError:
            out.append((f"依赖 {name}", "SKIP", f"未安装（{why}，后期阶段才用）"))
    return out


def _check_env_file() -> tuple[str, str]:
    from pathlib import Path
    if Path(".env").exists():
        return "OK", "已找到 .env"
    if Path(".env").exists():
        return "FAIL", "没有 .env —— 复制 .env 为 .env 并填入 key"
    return "FAIL", "既没有 .env 也没有 .env"


def _check_config(cfg) -> list[tuple[str, str, str]]:
    from config import PROVIDERS, parse_llm
    out = []

    provider, _ = parse_llm(cfg.smart_llm)
    out.append(("配置 模型", "OK" if provider in PROVIDERS else "FAIL",
                f"{cfg.smart_llm} -> provider={provider}"))

    # 实测（2026-10-11）8192 / 16000 / 65536 / 131072 都被接受，官方表 384K；
    # 旧结论「>8192 会 400」是错的，已按实测改成 131072。
    # 真正要防的是给太小：推理和正文共享额度，太小会让正文变空。
    if provider == "deepseek" and cfg.smart_token_limit > 131072:
        out.append(("配置 max_tokens", "FAIL",
                    f"{cfg.smart_token_limit} 超过实测可用上限 131072（官方声明 384K，未实测）"))
    elif provider == "deepseek" and cfg.smart_token_limit < 1024:
        out.append(("配置 max_tokens", "WARN",
                    f"{cfg.smart_token_limit} 偏小。推理模型的 max_tokens 是「思考+正文」共享额度，"
                    "太小会导致正文为空，建议 SMART_TOKEN_LIMIT >= 1024"))
    else:
        out.append(("配置 max_tokens", "OK", str(cfg.smart_token_limit)))

    # deepseek 没有 embedding 接口
    if provider == "deepseek" and cfg.context_filter in ("embeddings", "auto", "jev"):
        out.append(("配置 context_filter", "FAIL",
                    f"{cfg.context_filter} 在 deepseek 上不可用，请设 CONTEXT_FILTER=keyword"))
    else:
        out.append(("配置 context_filter", "OK", cfg.context_filter))

    if not 0 <= cfg.temperature <= 2:
        out.append(("配置 temperature", "FAIL", f"{cfg.temperature} 超出 0-2"))
    else:
        out.append(("配置 temperature", "OK", str(cfg.temperature)))

    return out


def _check_llm(cfg, offline: bool) -> tuple[str, str]:
    if offline:
        return "SKIP", "指定了 --offline"

    from config import resolve_llm
    try:
        _, base_url, model_name = resolve_llm(cfg.smart_llm)
    except ValueError as exc:
        return "FAIL", str(exc)

    # 注意：这里必须 import 模块本身，不能 from llm import LAST_xxx。
    # 后者拿的是导入那一刻的值的快照，chat() 更新模块全局后局部名字不会变。
    import llm as llm_module

    try:
        reply = llm_module.chat(
            [{"role": "user", "content": "只回复两个字：正常"}],
            max_tokens=512,
            temperature=0,
        )
    except Exception as exc:
        return "FAIL", f"{type(exc).__name__}: {str(exc)[:160]}"
    return "OK", (
        f"{base_url} / {model_name} -> {reply.strip()[:40]!r}"
        f"（finish={llm_module.LAST_FINISH_REASON}, 推理 {llm_module.LAST_REASONING_TOKENS} tokens）"
    )


def cmd_check(args: argparse.Namespace) -> int:
    """阶段 0 验收：逐项自检，任何一项 FAIL 都说明还没就绪。"""
    results: list[tuple[str, str, str]] = []

    label, detail = _check_python()
    results.append(("Python 版本", label, detail))

    results.extend(_check_deps())

    label, detail = _check_env_file()
    results.append((".env 文件", label, detail))

    cfg = load_config()
    results.extend(_check_config(cfg))

    label, detail = _check_llm(cfg, args.offline)
    results.append(("模型连通", label, detail))

    width = max(len(name) for name, _, _ in results)
    print("阶段 0 自检")
    print("=" * 68)
    for name, status, detail in results:
        print(f"{name:<{width}}  {status:<4}  {detail}")
    print("=" * 68)

    fails = [name for name, status, _ in results if status == "FAIL"]
    warns = [name for name, status, _ in results if status == "WARN"]
    skips = [name for name, status, _ in results if status == "SKIP"]

    if fails:
        print(f"\n未通过：{len(fails)} 项 -> {', '.join(fails)}")
        if any("模型连通" in f for f in fails):
            print("提示：连通失败通常是 key / base_url 的问题，")
            print("      deepseek 的 base_url 要写 https://api.deepseek.com")
        return 1

    if warns:
        print(f"\n警告：{', '.join(warns)}")
    if skips:
        print(f"阶段 0 通过（{len(skips)} 项跳过，属正常：{', '.join(skips)}）")
    else:
        print("阶段 0 通过，全部检查项正常。")
    return 0


# ---------------------------------------------------------------- 各阶段命令


def cmd_search(args: argparse.Namespace) -> int:
    """阶段 1：搜索 -> 打印结构化结果。"""
    from search import get_retriever

    retriever = get_retriever(load_config().retriever, args.query)
    results = retriever.search(max_results=args.max)
    if not results:
        print("没有结果（检查网络或检索器配置）")
        return 1
    for i, item in enumerate(results, 1):
        print(f"\n[{i}] {item.get('title') or '(无标题)'}")
        print(f"    {item.get('href') or item.get('url')}")
        body = item.get("body") or ""
        print(f"    {body[:160]}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """阶段 2：搜索 + 一次 LLM 摘要。"""
    from pipeline import quick_summary

    print(quick_summary(args.query, cfg=load_config()))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """阶段 3：把复杂问题拆成子问题。"""
    from pipeline import plan_research

    for i, sub in enumerate(plan_research(args.query, cfg=load_config()), 1):
        print(f"{i}. {sub}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """阶段 4：抓取单个 URL 的正文。"""
    from scrape import fetch

    page = fetch(args.url, cfg=load_config())
    if not page["ok"]:
        print(f"抓取失败：{page['reason']}（HTTP {page['status']}）")
        return 1
    print(f"标题: {page['title']}")
    print(f"长度: {len(page['raw_content'])} 字符")
    print(f"最终地址: {page['url']}")
    print("-" * 60)
    print(page["raw_content"][:300])
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """阶段 5：搜索 -> 抓取 -> 压缩，看压缩比。"""
    from pipeline import build_context

    result = build_context(args.query, cfg=load_config())
    print(f"原始字符数  : {result['raw_chars']}")
    print(f"压缩后字符数: {len(result['context'])}")
    print(f"来源数      : {len(result['sources'])}")
    print("-" * 60)
    print(result["context"][:600])
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """阶段 6：成稿。阶段 7 加 --costs。"""
    from pipeline import research_report

    result = research_report(args.query, cfg=load_config(), with_costs=args.costs)
    print(result["report"])
    if result.get("path"):
        print(f"\n（已写入 {result['path']}）")
    if args.costs:
        print("\n" + "-" * 60)
        print(result["cost_report"])
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    """阶段 8：一键跑完核心链路并落盘。"""
    from pipeline import full_research

    result = full_research(args.query, cfg=load_config(),curate=args.curate)
    for i, sub in enumerate(result.get("sub_queries", []), 1):
        print(f"  {i}. {sub}")
    print(f"报告已写入: {result['path']}")
    print(result["cost_report"])
    return 0

def cmd_agents(args:argparse.Namespace) -> int:
    """阶段9：多Agent编辑部 / 阶段10：可选的人审提纲 / 阶段11：可选的事实核查"""
    from agents import run_editorial
    ask = input if args.human else None
    if args.dynamic_role:
        print("（已开动态角色：先按题目挑专家人设，再拆提纲和写作）")
    if ask is None:
        print("（未开人审：加 --human 可以在拆完提纲后先看一遍再开始跑）")
    if args.fact_check:
        print("（已开事实核查：每轮写完后由核查员逐句核对引用有没有依据）")
    result = run_editorial(
        args.query,
        cfg=load_config(),
        max_rounds=args.rounds,
        ask=ask,
        with_fact_check=args.fact_check,
        dynamic_role=args.dynamic_role,
    )
    expert = result.get("expert")
    if expert is not None:
        print(f"\n专家人设：{expert.name} —— {expert.role_prompt}\n")
    print(result["report"])
    for h in result.get("history", []):
        verdict = h.get("verdict") or {}
        fact = h.get("fact") or {}
        fact_note = ""
        if h.get("fact") is not None:
            fact_note = f"  核查={'过' if fact.get('passed') else '不过'}"
        print(
            f"  第 {h.get('round')} 轮  目标 {len(h.get('targets') or [])} 条"
            f"  证据 {h.get('pages_total')} 页"
            f"  评审={'过' if verdict.get('passed') else '不过'}{fact_note}"
        )
    if result.get("path"):
        print(f"\n（已写入 {result['path']}，共 {result.get('rounds')} 轮）")
    if result.get("cost_report"):
        print("\n" + "-" * 60)
        print(result["cost_report"])
    return 0
def cmd_curate(args: argparse.Namespace) -> int:
    """阶段 13：抓取 -> 来源管家筛选排序（对照上游 skills/curator.py）。"""
    from curator import curate_pages
    from pipeline import _collect_pages, _cost_report_hook, plan_research

    cfg = load_config()
    subs = plan_research(args.query, cfg=cfg)
    for i, sub in enumerate(subs, 1):
        print(f"  {i}. {sub}")
    pages = _collect_pages(subs, cfg=cfg)
    if not pages:
        print("一个页面都没抓到，没有可筛的来源")
        return 1
    result = curate_pages(args.query, pages, cfg=cfg)
    print(f"抓取 {len(pages)} 篇 -> 保留 {len(result['pages'])} 篇")
    if not result["ok"]:
        print(f"（管家没筛成，已原样放行：{result['reason']}）")
    elif result["reason"]:
        print(f"（{result['reason']}）")
    print("-" * 60)
    for item in result["kept"]:
        why = f"  —— {item['why']}" if item["why"] else ""
        print(f"  留 [{item['n']}] {item['title'][:48]}{why}")
    for item in result["dropped"]:
        why = f"  —— {item['why']}" if item["why"] else ""
        print(f"  丢 [{item['n']}] {item['title'][:48]}{why}")
    if args.costs:
        print("\n" + "-" * 60)
        print(_cost_report_hook(cfg))
    return 0
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="mini-gpt-researcher")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="阶段 0：环境自检")
    p.add_argument("--offline", action="store_true", help="跳过模型连通测试")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("search", help="阶段 1：单次搜索")
    p.add_argument("query")
    p.add_argument("--max", type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("summary", help="阶段 2：搜索 + 摘要")
    p.add_argument("query")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("plan", help="阶段 3：子问题拆解")
    p.add_argument("query")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("fetch", help="阶段 4：抓取正文")
    p.add_argument("url")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("context", help="阶段 5：上下文压缩")
    p.add_argument("query")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("report", help="阶段 6：生成报告")
    p.add_argument("query")
    p.add_argument("--costs", action="store_true", help="阶段 7：打印成本")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("research", help="阶段 8：完整链路")
    p.add_argument("query")
    p.add_argument(
        "--curate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="阶段 13：抓完先让来源管家筛一遍（不指定就跟随 .env 的 CURATE_SOURCES）",
    )
    p.set_defaults(func=cmd_research)
    p = sub.add_parser("curate", help="阶段 13：来源管家筛选排序")
    p.add_argument("query")
    p.add_argument("--costs", action="store_true", help="打印这一步的成本")
    p.set_defaults(func=cmd_curate)
    p = sub.add_parser("agents", help="阶段 9：多 Agent 编辑部")
    p.add_argument("query")
    p.add_argument("--rounds", type=int, default=3, help="最多评审几轮")
    p.add_argument(
        "--human",
        action="store_true",
        help="阶段 10：拆完提纲后停下来等人确认（回车接受 / n 打回 / e 自己改）",
    )
    p.add_argument(
        "--fact-check",
        action="store_true",
        help="阶段 11：每轮写完后让事实核查员逐句核对引用有没有依据（每轮多一次模型调用）",
    )
    p.add_argument(
        "--dynamic-role",
        action="store_true",
        help="阶段 15：先让模型按题目选一位专家人设，再拆提纲和写作",
    )
    p.set_defaults(func=cmd_agents)
    return parser


STAGE_OF = {
    "search": 1, "summary": 2, "plan": 3, "fetch": 4,
    "context": 5, "report": 6, "research": 8,"agents":9,
    "curate": 13
}

def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except NotImplementedError as exc:
        stage = STAGE_OF.get(args.command, "?")
        print(f"该功能还没实现（阶段 {stage}）。")
        print(f"提示：{exc}")
        print("对照源码见 ROADMAP.md 对应小节。")
        return 2


if __name__ == "__main__":
    sys.exit(main())