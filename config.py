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

from dotenv import load_dotenv

load_dotenv()

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
    max_search_results: int = _env_int("MAX_SEARCH_RESULTS", 6)
    max_sub_queries: int = _env_int("MAX_SUB_QUERIES", 4)

    context_filter: str = _env_str("CONTEXT_FILTER", "keyword")
    compression_threshold: int = _env_int("COMPRESSION_THRESHOLD", 8000)
    keyword_relative_threshold: float = _env_float("KEYWORD_RELATIVE_THRESHOLD", 0.5)
    keyword_max_results: int = _env_int("KEYWORD_MAX_RESULTS", 25)

    report_type: str = _env_str("REPORT_TYPE", "research_report")
    total_words: int = _env_int("TOTAL_WORDS", 1000)
    llm_timeout: int = _env_int("LLM_TIMEOUT", 120)
    scrape_timeout: int = _env_int("SCRAPE_TIMEOUT", 15)

    # 检索器的 HTTP 超时
    request_timeout: int = _env_int("REQUEST_TIMEOUT", 15)
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