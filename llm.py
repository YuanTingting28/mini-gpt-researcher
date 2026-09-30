"""LLM 调用封装。

对照源码：gpt_researcher/utils/llm.py（192 行）
    核心是 create_chat_completion：
      - 按 provider 分派到不同 SDK
      - 失败重试
      - 把响应里的 usage 交给 cost_callback

    gpt_researcher/llm_provider/generic/base.py:146 from_provider
        看 base_url 是怎么透传给 SDK 的。

阶段 0 只要能发一次请求。
阶段 2 回来补：重试 / 超时 / JSON 模式 / 流式。

【重要】DeepSeek 的推理模型（如 deepseek-flash）会先输出一段思维链，
    max_tokens 是「思考 + 正文」共享的额度，不是只算正文。
    实测同一个提示词，推理长度会在 14 ~ 115 token 之间大幅波动，所以：
      1. max_tokens 给小了（比如 16），额度全被思考吃光，content 直接是空字符串，
         finish_reason 是 "length"。这不是网络问题，也不是 key 问题。
      2. 所以 max_tokens 要给足（本项目默认 4000），并且必须检查正文是否为空。
    这个坑对后面所有阶段都成立。
"""

from __future__ import annotations

from openai import OpenAI

from config import Config, load_config, resolve_llm


class LLMError(Exception):
    """LLM 调用相关错误。"""


class LLMTruncated(LLMError):
    """输出被 max_tokens 截断，拿不到正文。"""


#: 每次调用的用量记录，阶段 7 的 CostTracker 从这里取数
USAGE_LOG: list[dict] = []

#: 最近一次调用的元信息，方便自检和调试
LAST_FINISH_REASON: str = ""
LAST_REASONING_TOKENS: int = 0


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
    cfg: Config | None = None,
) -> str:
    """发一次对话请求，返回纯文本。

    TODO(阶段 2)：
      - 加 try/except + 指数退避重试（网络抖动很常见）
      - 加 timeout
    TODO(阶段 7)：
      - USAGE_LOG 已经记好了 token 数，接上 CostTracker 即可
    """
    global LAST_FINISH_REASON, LAST_REASONING_TOKENS

    cfg = cfg or load_config()
    model = model or cfg.smart_llm
    api_key, base_url, model_name = resolve_llm(model)

    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    # max_tokens 不传时回落配置值；不设上限的话推理模型可能长时间不返回
    if max_tokens is None:
        max_tokens = cfg.smart_token_limit
    if model_name.startswith("deepseek") and max_tokens > 8192:
        raise LLMError(f"max_tokens={max_tokens} 超过 deepseek 上限 8192，会被 API 直接拒绝")

    response = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=cfg.temperature if temperature is None else temperature,
        max_tokens=max_tokens,
        **kwargs,
    )

    choice = response.choices[0]
    content = choice.message.content or ""
    LAST_FINISH_REASON = choice.finish_reason or ""

    reasoning_tokens = 0
    if response.usage and response.usage.completion_tokens_details is not None:
        reasoning_tokens = response.usage.completion_tokens_details.reasoning_tokens or 0
    LAST_REASONING_TOKENS = reasoning_tokens

    USAGE_LOG.append(
        {
            "model": model_name,
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "reasoning_tokens": reasoning_tokens,
        }
    )

    if not content.strip():
        if LAST_FINISH_REASON == "length":
            raise LLMTruncated(
                f"输出被 max_tokens={max_tokens} 截断，正文为空"
                f"（其中 {reasoning_tokens} 个 token 用于推理）。"
                f"把 max_tokens 调大（建议 >= 1024）再试。"
            )
        raise LLMError(f"模型返回了空正文，finish_reason={LAST_FINISH_REASON!r}")

    return content