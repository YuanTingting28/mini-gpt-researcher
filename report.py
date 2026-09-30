"""成稿：上下文 -> 带引用的 Markdown 报告。

对照源码：
    gpt_researcher/skills/writer.py:20   ReportGenerator
        write_report(49)              主流程
        write_introduction(175)       引言
        write_report_conclusion(136)  结论
    gpt_researcher/actions/report_generation.py
        generate_report(209)          正文生成
        summarize_url(115)            长文先摘要再进上下文
    gpt_researcher/prompts.py:262       generate_report_prompt（核心提示词，重点读）
        看它怎么约束：标题层级、引用格式 [1]、参考文献表、字数
    gpt_researcher/agent.py:700         add_references（角标 -> URL 列表）
                     :712  extract_headers / :734 table_of_contents
    gpt_researcher/actions/markdown_processing.py（108 行，Markdown 后处理）
"""

from __future__ import annotations

from typing import Any

from config import Config, load_config


def build_report_prompt(query: str, context: str, *, cfg: Config) -> str:
    """拼报告提示词。

    对照源码：gpt_researcher/prompts.py:262

    TODO(阶段 6)：
      - 明确要求 Markdown、小标题、引用角标 [n]、末尾参考文献表
      - 明确字数目标 cfg.total_words
      - 明确"只允许使用给定上下文，不许编造"——这条能显著减少幻觉引用
    """
    raise NotImplementedError("阶段 6：实现 build_report_prompt")


def add_references(report: str, sources: list[dict[str, Any]]) -> str:
    """给报告末尾补参考文献表，并校验角标编号。

    对照源码：gpt_researcher/agent.py:700

    TODO(阶段 6)：
      1. 扫出正文里所有 [n]（正则 \\[(\\d+)\\]）
      2. 编号超出 sources 范围的：记为"模型编造的引用"，打印警告
      3. 末尾追加：
           ## 参考文献
           [1] 标题 — https://...
      4. 只列出正文真正引用到的来源
    """
    raise NotImplementedError("阶段 6：实现 add_references")


def write_report(
    query: str,
    context: str,
    sources: list[dict[str, Any]],
    *,
    cfg: Config | None = None,
) -> str:
    """生成完整报告。

    TODO(阶段 6)：
      1. build_report_prompt
      2. llm.chat(..., max_tokens <= 8192)
      3. add_references
      4. 加分项：再各调一次生成引言和结论，插到开头和结尾
    """
    raise NotImplementedError("阶段 6：实现 write_report")