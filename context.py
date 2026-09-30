"""上下文筛选与压缩：几万字 -> 塞得进模型的相关片段。

对照源码：
    gpt_researcher/context/select.py（79 行，先读这个）
        resolve_context_filter(42)  auto/jev/keyword/embeddings/none 五种模式
        select_context(57)          主入口
        —— 关键设计：任何模式失败都回落到 keyword，保证永远有上下文可用。
           你先只实现 none 和 keyword 两种，其余是扩展位。
    gpt_researcher/context/lexical.py（112 行，BM25 手写实现，重点读）
        tokenize(44) / bm25_scores(48) / select(95) / async_get_context(106)
        —— 阈值是"至少达到最高分的 50%"，上限 25 块。
           这个 50% 是原项目用 evals/context_filter 的评测调出来的，不是拍脑袋。
    gpt_researcher/context/compression.py（222 行，向量版，阶段 5 之后再回来看）
    gpt_researcher/prompts.py:555  pretty_print_docs（文档怎么拼成给模型的字符串）
"""

from __future__ import annotations

from typing import Any

from config import Config, load_config


def pretty_print_docs(pages: list[dict[str, Any]], top_n: int | None = None) -> str:
    """把页面列表拼成给模型的上下文。

    对照源码：gpt_researcher/prompts.py:555

    TODO(阶段 5)：每篇固定加上来源标记，格式自定但必须稳定，例如：

        [1] 标题
        URL: https://...
        正文: ...

    编号要在这里就固定下来——后面写报告时的引用角标全靠它，
    不能让模型自己发明编号。
    """
    raise NotImplementedError("阶段 5：实现 pretty_print_docs")


def bm25_scores(query: str, passages: list[str]) -> list[float]:
    """BM25 打分。

    对照源码：gpt_researcher/context/lexical.py:48

    TODO(阶段 5)：
      - 先写分词：小写 + 按非字母数字切分（中文要额外处理，可先用空格/标点切）
      - 再写 BM25：k1=1.5, b=0.75，注意 idf 和文档长度归一化
      - 这是本项目里唯一需要手写算法的地方，别跳过去
    """
    raise NotImplementedError("阶段 5：实现 bm25_scores")


def select_context(query: str, pages: list[dict[str, Any]], *, cfg: Config | None = None) -> str:
    """挑出与 query 最相关的片段并拼成上下文。

    TODO(阶段 5)：
      1. mode = cfg.context_filter
      2. mode == "none"  -> 直接 pretty_print_docs(pages)
      3. 短路：总字符数 < cfg.compression_threshold 且篇数不多 -> 不筛选，全用
         （对应原项目 select.py 里那段 "Little content needs no filtering"）
      4. mode == "keyword"：
           - 按空行把每页切成块
           - bm25_scores 打分
           - 只保留分数 >= 最高分 * cfg.keyword_relative_threshold 的块
           - 最多取 cfg.keyword_max_results 块
      5. 未知 mode -> 警告并回落 keyword（和原项目一样，别让它抛异常）
    """
    raise NotImplementedError("阶段 5：实现 select_context")