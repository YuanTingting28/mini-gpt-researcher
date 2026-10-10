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

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import llm
from config import Config, load_config
from llm import chat
from prompts import (report_conclusion_prompt, report_introduction_prompt,
                     report_prompt)

logger = logging.getLogger(__name__)

#正文里的角标 [1] / [1,2] / [1,2]
_CITATION_RE = re.compile(r"\[(\d+(?:\s*[，,]\s*\d+)*)\]")
#: [1-3] 这种区间写法我们不展开，只报警
_CITATION_RANGE_RE = re.compile(r"\[\d+\s*[-~–]\s*\d+\]")
#: 上下文里一块来源的开头："[3] 标题"
_BLOCK_HEAD_RE = re.compile(r"\[(\d+)\]\s*(.*)$")
#: 模型自己写的、需要由程序接管的章节标题
_REF_HEADING = re.compile(r"^#{1,3}\s*(参考文献|参考资料|引用来源|References?)\s*[:：]?\s*$", re.I | re.M)
_CONC_HEADING = re.compile(r"^#{1,3}\s*(结论|总结|小结|Conclusion|Summary)\s*[:：]?\s*$", re.I | re.M)
_TOC_HEADING = re.compile(r"^#{1,3}\s*(目录|Table of Contents)\s*[:：]?\s*$", re.I | re.M)

BODY_TEMPERATURE = 0.35      # 对照上游 report_generation.py:298
SIDE_TEMPERATURE = 0.25      # 引言/结论，对照上游 :48 和 :100
MAX_TOKENS_CEILING = 8192    # deepseek 的硬上限
# ------------------------------------------------------------------ 编号解析
def parse_context_sources(context: str) -> list[dict[str, Any]]:
    """
    把上下文里的 [n] 编号块抽成 [{n, title, url}]。

    为什么不直接用 build_context 返回的 sources：那份 sources 是"抓到的页面"，
    而上下文里的编号是"筛选后保留的片段"。keyword 模式下一个页面可能贡献好几块，
    两份列表的下标根本对不上（实测 9 块来自 3 个页面）。
    编号的定义就写在上下文里，直接读它，永远不会错位。
    :param context:
    :return:
    """
    out: list[dict[str, Any]] = []
    if not context:
        return out
    lines =  context.splitlines()
    expect = 1
    for i,line in enumerate(lines):
        m = _BLOCK_HEAD_RE.match(line)
        if not m or int(m.group(1)) != expect:
            continue
        url = ""
        if i+1 <len(lines) and lines[i+1].startswith("URL:"):
            url = lines[i + 1][4:].strip()
        out.append({"n":expect,"title":m.group(2).strip(),"url":url})
        expect += 1
    if context.strip() and not out:
        logger.warning("上下文解析不到任何[n]来源块，报告没有引用")
    return out

def _citation_numbers(text:str)->list[int]:
    nums: list[int] = []
    for m in _CITATION_RE.finditer(text):
        nums.extend(int(x) for x in re.split(r"[，,\s]+",m.group(1)) if x.strip())
    return nums
def validate_citations(report:str,known:set[int])->tuple[str,list[int]]:
    """剔除越界角标，返回（处理后的正文，被剔除的编号）
    对照源码：上游没有这一步 —— 它让模型直接写 ([citation](url))，
    URL 本身就是引用，写错了会 404 但不会被"发明"。我们改成编号，
    就必须自己把关：模型确实会写出上下文里不存在的编号。
    """
    report = report.replace("【","[").replace("】","]") #中文模型爱写全角括号
    dropped:list[int] = []
    def repl(m:re.Match)->str:
        nums = [int(x) for x in re.split(r"[，,\s]+",m.group(1)) if x.strip()]
        keep = [n for n in nums if n in known]
        dropped.extend(n for n in nums if n not in known)
        if not keep:
            return ""
        return "[" + ",".join(str(n) for n in keep) + "]"
    cleaned = _CITATION_RE.sub(repl, report)
    if _CITATION_RANGE_RE.search(report):
        logger.warning(
            "报告里有 [1-3] 这种区间引用，本阶段不展开，请人工确认：%s",
            _CITATION_RANGE_RE.findall(report)[:5],)
    cleaned = re.sub(r"[ \t]+(?=\n|$)","",cleaned)
    cleaned = re.sub(r"[ \t]+([。，、；：！？）】])", r"\1", cleaned)   # "个 。" -> "个。"
    return cleaned, dropped

# ------------------------------------------------------------------ 后处理
def _cut_from_last_heading(text:str,pattern:re.Pattern)->tuple[str,bool]:
    """砍掉最后一个匹配标题以及后面的全部内容"""
    hits = list(pattern.finditer(text))
    if not hits or hits[-1].start() ==0:
        return text,False
    return text[:hits[-1].start()].rstrip(),True

def _drop_toc(text:str)->tuple[str,bool]:
    """删掉”目录“标题以及紧跟其后的列表行（目录在开头，不能整段砍）"""
    m = _TOC_HEADING.search(text)
    if not m:
        return text,False
    rest = text[m.end():]
    eaten = 0
    for line in rest.splitlines(keepends=True):
        s = line.strip()
        if not s or s.startswith(("-","*","+")) or re.match(r"^\d+[.)]",s):
            eaten += len(line)
        else:
            break
    merged = text[:m.start()].rstrip() + "\n\n" + rest[eaten:].lstrip()
    return merged.strip(),True
def _clean_report(text: str, *, drop_conclusion: bool) -> str:
    """把模型输出的 Markdown 收拾干净。

    对照源码：gpt_researcher/actions/markdown_processing.py（上游只有
    extract_headers / table_of_contents，没有这一层清理）。
    """
    out = (text or "").strip()
    out = out.replace("【", "[").replace("】", "]")     # 中文模型爱写全角括号
    fence = re.match(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", out, re.S)
    if fence:
        out = fence.group(1).strip()
        logger.info("报告被 ``` 包住，已剥掉围栏")
    out, hit = _drop_toc(out)
    if hit:
        logger.info("删掉模型自带的目录")
    out, hit = _cut_from_last_heading(out, _REF_HEADING)
    if hit:
        logger.info("删掉模型自带的参考文献章节（由程序追加）")
    if drop_conclusion:
        out, hit = _cut_from_last_heading(out, _CONC_HEADING)
        if hit:
            logger.info("删掉模型自带的结论章节（由程序追加）")
    return out.strip()

_H1_RE = re.compile(r"^#\s+\S.*$", re.M)

def _drop_h1(text: str) -> str:
    """去掉一级标题。主标题由正文负责，引言/结论不该再出一个 H1。

    提示词里虽然写了"不要写一级标题"，但模型经常不听，所以代码兜一层。
    """
    return _H1_RE.sub("", text, count=1).strip()


def _insert_introduction(body: str, intro: str) -> str:
    """把引言插到 H1 之后。没有 H1 就放最前面。"""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[: i + 1] + ["", intro.strip()] + lines[i + 1:])
    return intro.strip() + "\n\n" + body
# ------------------------------------------------------------------ 生成


def build_report_prompt(query: str, context: str, *, cfg: Config) -> str:
    """拼报告提示词。

    对照源码：gpt_researcher/prompts.py:262

    TODO(阶段 6)：
      - 明确要求 Markdown、小标题、引用角标 [n]、末尾参考文献表
      - 明确字数目标 cfg.total_words
      - 明确"只允许使用给定上下文，不许编造"——这条能显著减少幻觉引用
    """
    """拼报告提示词"""
    sources = parse_context_sources(context)
    return report_prompt(
        query,context,
        total_words=cfg.total_words,
        max_source=max((s["n"] for s in sources),default=0)
    )


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
    """校验角标并追加参考文献表

       对照源码：gpt_researcher/actions/markdown_processing.py:101
       上游是 `"".join(f"- [{url}]({url})" for url in sorted(visited_urls))` ——
       把**所有访问过**的 URL 都列上，跟正文引用无关。
       我们只列正文真正引用到的，读者才能一一对上。
       参数 sources 是 parse_context_sources() 的返回值（带 n 的编号来源表），
       不是 build_context() 的 pages。
       """
    by_n = {s["n"]: s for s in sources}
    body, dropped = validate_citations(report, set(by_n))
    if dropped:
        logger.warning(
            "模型编造了 %d 处不存在的引用编号 %s（上下文里只有 1-%d），已从正文删除",
            len(dropped),
            sorted(set(dropped)),
            max(by_n) if by_n else 0,
        )
    cited = sorted(set(_citation_numbers(body)))
    if not cited:
        logger.warning("正文里没有任何有效角标，参考文献表为空")
        return body
    # 同一个URL可能对应读个编号，一个页面被切成多块，合并成一条
    groups: dict[tuple[str, str], list[int]] = {}
    for n in cited:
        s = by_n[n]
        groups.setdefault((s.get("url") or "", s.get("title") or ""), []).append(n)
    lines = ["", "", "## 参考文献", ""]
    for (url, title), nums in groups.items():
        label = "".join(f"[{n}]" for n in nums)
        tail = f" — {url}" if url else ""
        lines.append(f"- {label} {title or '(无标题)'}{tail}")
    return body.rstrip() + "\n" + "\n".join(lines) + "\n"

def _generate(prompt:str,cfg: Config,*,step:str,temperature:float) -> str:
    text = chat(
        [{"role":"user","content":prompt}],
        cfg=cfg,
        temperature=temperature,
        max_tokens=min(cfg.smart_token_limit,MAX_TOKENS_CEILING),
        step=step
    )
    if llm.LAST_FINISH_REASON == "length":
        logger.warning(
            "「%s」被 max_tokens=%d 截断（finish_reason=length），报告可能不完整。"
            "把 .env 里的 SMART_TOKEN_LIMIT 调到 8000 再试",
            step,
            min(cfg.smart_token_limit, MAX_TOKENS_CEILING),
        )
    return text.strip()

def _generate_optional(prompt:str,cfg: Config,*,step:str) -> str:
    """引言/结论属于锦上添花，失败只跳过，不影响正文
    局部失败降级，全局失败停下，但一定要打日志
    """
    try:
        return _generate(prompt,cfg=cfg,step=step,temperature=SIDE_TEMPERATURE)
    except Exception as exc:
        logger.warning("「%s」生成失败，已跳过（正文仍然可用）：%s", step, exc)
        return ""

def write_report(
    query: str,
    context: str,
    sources: list[dict[str, Any]],
    *,
    cfg: Config | None = None,
    with_introduction: bool = True,
    with_conclusion: bool = True,
) -> str:
    """生成完整报告。

    对照源码：gpt_researcher/skills/writer.py:49 ReportGenerator.write_report
    上游是三次独立调用（引言 / 正文 / 结论），我们保持一致 ——
    好处是每段可以单独重试、单独降级，代价是多两次调用。

    TODO(阶段 6)：
      1. build_report_prompt
      2. llm.chat(..., max_tokens <= 8192)
      3. add_references
      4. 加分项：再各调一次生成引言和结论，插到开头和结尾
    """
    cfg = cfg or load_config()
    sources = parse_context_sources(context) if sources is None else sources
    n_sources = max((s["n"] for s in sources),default=0)
    body = _generate(build_report_prompt(query,context, cfg=cfg),cfg,step="正文",temperature=BODY_TEMPERATURE)
    body = _clean_report(body,drop_conclusion=with_conclusion)
    if with_introduction:
        intro = _generate_optional(
            report_introduction_prompt(query, context, max_source=n_sources),
            cfg,
            step="引言",
        )
        if intro:
            body = _insert_introduction(
                body, _drop_h1(_clean_report(intro, drop_conclusion=False))
            )

    if with_conclusion:
        conclusion = _generate_optional(
            report_conclusion_prompt(query, body), cfg, step="结论"
        )
        if conclusion:
            conclusion = _drop_h1(_clean_report(conclusion, drop_conclusion=True))
            conclusion = _CONC_HEADING.sub("", conclusion).strip()
            if conclusion:
                body = body.rstrip() + "\n\n## 结论\n\n" + conclusion

    return add_references(body, sources)

def save_report(query: str, report: str, *, out_dir: str | Path = "outputs") -> Path:
    """落盘到 outputs/<时间戳>-<slug>.md。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[\W_]+", "-", query).strip("-")[:40] or "report"
    path = out / f"{stamp}-{slug}.md"
    path.write_text(report, encoding="utf-8")
    return path