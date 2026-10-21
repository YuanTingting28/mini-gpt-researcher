"""配置层：从 .env 读配置，给出默认值，缺关键项时报清楚的错。

对照源码：gpt_researcher/config/config.py
    值得抄的三点：
      1. merge_config      —— 默认值 / 环境变量 / 外部配置层合并
      2. parse_llm         —— "deepseek:deepseek-chat" 拆成 provider 和模型名
      3. convert_env_value —— env 全是字符串，要按目标类型转回 int/float/bool/list

阶段 0 的任务就是把这个文件读懂 + 跑通，不要上来就抄原项目的 288 行。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

#: provider -> 从哪里取 key，以及默认 base_url
PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
    },
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "base_url": os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
    },
}


def _env_str(key: str, default: str) -> str:
    return os.getenv(key) or default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"环境变量 {key} 应该是整数，实际是 {raw!r}") from None


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"环境变量 {key} 应该是小数，实际是 {raw!r}") from None
def _env_bool(key: str, default: bool) -> bool:
    """env 里布尔值只能写成字符串，这里统一认 1/true/yes/on（不分大小写）。

    只把空串当成"没设"：设了 CURATE_SOURCES=0 就该是 False，
    不能因为 "0" 是真值字符串就变成 True —— 那是典型的环境变量坑。
    """
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")

def _env_list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """逗号（或分号）分隔的列表。空值一律回退到默认值，不留空元组。"""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    items = [part.strip().lower() for part in raw.replace(";", ",").split(",")]
    out = [part for part in items if part]
    return tuple(dict.fromkeys(out)) or default

def _env_paths(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """读路径列表；不能复用 _env_list，因为它会把路径 lower()。"""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default

    parts = [
        p.strip().strip('"').strip("'")
        for p in raw.replace(";", ",").split(",")
    ]

    out: list[str] = []
    seen: set[str] = set()

    for part in parts:
        if not part:
            continue

        expanded = os.path.expandvars(os.path.expanduser(part))
        key_norm = os.path.normcase(os.path.abspath(expanded))

        if key_norm not in seen:
            seen.add(key_norm)
            out.append(expanded)

    return tuple(out) or default

def _default_retrievers() -> tuple[str, ...]:
    """阶段17：RETRIEVERS 没配就退回单源 RETRIEVER —— 老 .env 一个字都不用改。"""
    return _env_list("RETRIEVERS", (_env_str("RETRIEVER", "tavily"),))

def parse_llm(model: str) -> tuple[str, str]:
    """``"deepseek:deepseek-chat"`` -> ``("deepseek", "deepseek-chat")``。

    没写 provider 前缀时按 openai 处理。
    """
    if ":" in model:
        provider, name = model.split(":", 1)
        return provider.strip().lower(), name.strip()
    return "openai", model.strip()


def resolve_llm(model: str) -> tuple[str, str, str]:
    """解析出调用需要三元组：``(api_key, base_url, 模型名)``。

    key 缺失时直接报错，并指明应该设哪个环境变量——配置类错误里
    「说不清该改哪里」是最浪费时间的。
    """
    provider, name = parse_llm(model)
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(f"未知 provider {provider!r}，目前只支持 {sorted(PROVIDERS)}")
    api_key = os.getenv(spec["env_key"], "")
    if not api_key:
        raise ValueError(f"{provider} 缺少 API key，请在 .env 里设置 {spec['env_key']}")
    return api_key, spec["base_url"], name


@dataclass
class Config:
    """运行期配置。字段名尽量和原项目保持一致。"""

    fast_llm: str = _env_str("FAST_LLM", "deepseek:deepseek-chat")
    smart_llm: str = _env_str("SMART_LLM", "deepseek:deepseek-chat")
    strategic_llm: str = _env_str("STRATEGIC_LLM", "deepseek:deepseek-chat")

    temperature: float = _env_float("TEMPERATURE", 0.4)
    smart_token_limit: int = _env_int("SMART_TOKEN_LIMIT", 4000)

    retriever: str = _env_str("RETRIEVER", "tavily")
    #阶段17，按顺序参与检索的源，合并时按这个顺序排，保证引用编号可复现
    retrievers:tuple[str,...] = _default_retrievers()
    max_search_results: int = _env_int("MAX_SEARCH_RESULTS", 6)
    max_sub_queries: int = _env_int("MAX_SUB_QUERIES", 4)

   #-----并发（阶段8）
    # 并发抓取/检索的最大线程数，网络IO为主，4-8是甜点去
    # 调太大容易被目标站限流，我们还没有限流器兜底
    max_concurrency:int = _env_int("MAX_CONCURRENCY", 4)
    #一轮研究最多抓取多少页面？ 4个子问题 * 6 条结果 = 24个URL
    max_pages:int = _env_int("MAX_PAGES", 12)
    # ---- 来源管家（阶段13）----
    #: 是否在压缩之前先让来源管家筛一遍。默认关：它要多一次模型调用。
    #: 对照上游 default.py:18 "CURATE_SOURCES": False（默认也是关）。
    curate_sources: bool = _env_bool("CURATE_SOURCES", False)
    #: 最多保留几篇来源。抓 12 篇、筛掉一半，是常见的量级。
    curate_max_results: int = _env_int("CURATE_MAX_RESULTS", 10)
    #: 目录里每篇给模型看的开头长度（字符）。
    curate_snippet_chars: int = _env_int("CURATE_SNIPPET_CHARS", 600)
    context_filter: str = _env_str("CONTEXT_FILTER", "keyword")
    compression_threshold: int = _env_int("COMPRESSION_THRESHOLD", 8000)
    keyword_relative_threshold: float = _env_float("KEYWORD_RELATIVE_THRESHOLD", 0.5)
    keyword_max_results: int = _env_int("KEYWORD_MAX_RESULTS", 25)
    chunk_size: int = _env_int("CHUNK_SIZE", 1000)
    chunk_overlap: int = _env_int("CHUNK_OVERLAP", 150)
    report_type: str = _env_str("REPORT_TYPE", "research_report")
    #: 报告输出语言，和检索语言解耦。auto 表示跟随研究任务语言；
    #: 英文资料 + 中文报告可以设成 zh。
    report_language: str = _env_str("REPORT_LANGUAGE", "auto")
    total_words: int = _env_int("TOTAL_WORDS", 1000)
    llm_timeout: int = _env_int("LLM_TIMEOUT", 120)
    #这里有爬取正文的事-所以和爬虫有关
    scrape_timeout: int = _env_int("SCRAPE_TIMEOUT", 15)
    scrape_max_chars: int = _env_int("SCRAPE_MAX_CHARS", 12000)
    #: 单个响应体的字节上限（阶段 12）。PDF 十几 MB 很常见，而 Content-Length
    #: 是服务器说了算的 —— 读取时按这个上限叫停（见 scrape._read_body）。
    scrape_max_bytes: int = _env_int("SCRAPE_MAX_BYTES", 30 * 1024 * 1024)
    #相关英文文献的分析
    paper_wiki: bool = _env_bool("PAPER_WIKI", True)
    paper_wiki_max: int = _env_int("PAPER_WIKI_MAX", 3)
    paper_wiki_chars: int = _env_int("PAPER_WIKI_CHARS", 12000)
    paper_wiki_excerpt_chars: int = _env_int("PAPER_WIKI_EXCERPT_CHARS", 4000)
    paper_wiki_max_tokens: int = _env_int("PAPER_WIKI_MAX_TOKENS", 1800)
    paper_wiki_concurrency: int = _env_int("PAPER_WIKI_CONCURRENCY", 2)
    # 检索器的 HTTP 超时
    request_timeout: int = _env_int("REQUEST_TIMEOUT", 15)
    # ---- 本地文档研究（阶段19）----
    document_paths: tuple[str, ...] = _env_paths("DOCUMENT_PATHS", ())
    document_index: str = _env_str("DOCUMENT_INDEX", "document_index/index.json")
    document_max_results: int = _env_int("DOCUMENT_MAX_RESULTS", 8)
    document_relative_threshold: float = _env_float(
        "DOCUMENT_RELATIVE_THRESHOLD", 0.5
    )
    document_max_file_mb: int = _env_int("DOCUMENT_MAX_FILE_MB", 100)
    report_language: str = _env_str("REPORT_LANGUAGE", "zh")
    def show(self) -> str:
        """给 ``main.py check`` 用：把当前配置列出来，key 一律打码。"""
        lines = []
        for f in fields(self):
            lines.append(f"  {f.name:<26} = {getattr(self, f.name)}")
        for provider, spec in PROVIDERS.items():
            key = os.getenv(spec["env_key"], "")
            masked = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else ("<缺失>" if not key else "<过短>")
            lines.append(f"  {spec['env_key']:<26} = {masked}")
        return "\n".join(lines)


def load_config() -> Config:
    return Config()


if __name__ == "__main__":
    print(load_config().show())
