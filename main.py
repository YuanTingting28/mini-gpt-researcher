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

    # 上限 8192，超了会 400；给太小又会被推理过程吃光，正文变空
    if provider == "deepseek" and cfg.smart_token_limit > 8192:
        out.append(("配置 max_tokens", "FAIL", f"{cfg.smart_token_limit} 超过 deepseek 上限 8192"))
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
    print(f"标题: {page.get('title')}")
    print(f"长度: {len(page.get('raw_content') or '')} 字符")
    print("-" * 60)
    print((page.get("raw_content") or "")[:300])
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
    if args.costs:
        print("\n" + "-" * 60)
        print(result["cost_report"])
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    """阶段 8：一键跑完核心链路并落盘。"""
    from pipeline import full_research

    result = full_research(args.query, cfg=load_config())
    print(f"报告已写入: {result['path']}")
    print(result["cost_report"])
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
    p.set_defaults(func=cmd_research)

    return parser


STAGE_OF = {
    "search": 1, "summary": 2, "plan": 3, "fetch": 4,
    "context": 5, "report": 6, "research": 8,
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