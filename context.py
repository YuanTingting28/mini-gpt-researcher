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

from langchain_text_splitters import RecursiveCharacterTextSplitter

import logging
import math
import re
from collections import Counter
from typing import Any

from config import Config, load_config

logger = logging.getLogger(__name__)

#英文/数字词
_WORD = re.compile(r"[a-z0-9]+")

#连续汉字串
_CJK = re.compile(r"[\u4e00-\u9fff]+")
#中文虚词，只用来丢掉"全是虚词“的2-gram，不用完整的停用词表
#常见词被压下去是IDF的职责，手工维护词表 及维护不起也会有无上
_CJK_STOP = frozenset("的了是在和与及对于也都就还而为之以于其这那我们你他她它")

#: 英文停用词，照抄原项目 lexical.py:20
STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers herself him himself his how i if in into is it its itself just me more most
my myself no nor not now of off on once only or other our ours ourselves out over own same she should
so some such than that the their theirs them themselves then there these they this those through to
too under until up very was we were what when where which while who whom why will with would you your
yours yourself yourselves
""".split())

#1.分块参数，原项目把1000/100 硬编码再LexicalContextCompressor里
#2.我们改成从cfg.chunk_size读，因为中文的信息密度很高
#1000个中文字符差不多是四个段落，块内会被塞进无关内容
#筛选精读就没了 建议300-500
CHUNK_SIZE = 500
CHUNK_OVERLAP = 150
#: 切分优先级：段落 -> 行 -> 中文句号 -> 英文句号 -> 中文逗号 -> 空格 -> 字符。
#: langchain 的默认值是 ["\n\n", "\n", " ", ""]，中文正文没有空格，
#: 会一路降级到最后的 ""（按字符硬切），把句子拦腰切断。
#: 所以中文标点必须显式列进来。
_SEPARATORS = [
    "\n\n", "\n",
    "。", "！", "？", "；",          # 中文句末
    ". ", "! ", "? ", "; ",          # 英文句末（带空格，避免切断小数点）
    "，", ", ",
    " ",
    "",
]
def _stem(token: str) -> str:
    """轻量复数还原，让 batteries 能匹配 battery、costs 能匹配 cost。

    只对 ASCII 有效，中文 token 会原样穿过。
    """
    if len(token) <= 4 or token.endswith("ss"):
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s"):
        return token[:-1]
    return token

def tokenize(text:str)->list[str]:
    """
    中英混排分词
     **中文必须单独处理**：原项目 tokenize 用的 `\\w+` 会把一整串汉字
    当成一个 token。实测 "检索增强生成是一种把外部知识引入大模型的技术"
    只切出 1 个 token，查询 "检索增强生成是什么" 更是切出完全不同的 1 个，
    两者交集为空 —— 中文查询下 BM25 永远全 0。
     这里对汉字串切 2-gram："检索增强生成" -> 检索/索增/增强/强生/生成。
    代价是会产出跨词边界的伪词（如"步编"），但它对查询和文档是同一套
    切法，等价于一个二字特征，不影响相关性判断。
    :param text:
    :return:
    """
    text = (text or "").lower()
    tokens:list[str] = []

    for run in _CJK.findall(text):
        if len(run)==1:#这里要排除长度为1的词
            if run not in _CJK_STOP:
                tokens.append(run)
            continue
        tokens.extend(
            run[i:i+2] for i in range(len(run)-1)
            if not (run[i] in _CJK_STOP and run[i+1] in _CJK_STOP)
        )
    for word in _WORD.findall(text):
        if len(word) > 1 and word not in STOPWORDS:
            tokens.append(_stem(word))
    return tokens

# ------------------------------------------------------------------ BM25

def bm25_scores(query: str, passages: list[str],k1:float = 1.5,b:float=0.75) -> list[float]:
    """计算每个passage对query的BM25分数
    对照源码：gpt_researcher/context/lexical.py:48，公式逐项照抄。
        IDF 从 passages 自身统计（没有外部语料库）。

            score = Σ_t IDF(t) * tf*(k1+1) / (tf + k1*(1 - b + b*L/avgL))

        三个机制各管一件事，缺一个就会退化：
          IDF(t)      常见词降权。"编程" 在 5/6 篇里都有，idf 只有 0.24；
                      "异步" 只在 2/6 篇里，idf 高达 1.03。
          L/avgL      长度归一化。不除以长度，长文档永远赢。
          tf 的饱和    出现 1 次到 2 次很关键，20 次到 21 次几乎没用，
                      所以别用线性 tf，用 tf/(tf+k1*...) 让边际收益递减。

    """
    docs = [Counter(tokenize(p)) for p in passages]
    if not docs:
        return []
    avg_len = sum(sum(d.values()) for d in docs) / len(docs) or 1.0
    query_terms = set(tokenize(query)) #将问题分词
    df = {t:sum(1 for d in docs if t in d) for t in query_terms}
    # 末尾那个 +1 是 BM25+ 的保险：某个词在所有文档里都出现时，
    # 原始 IDF 会变成负数，把总分往下拉，这不是我们想要的效果。
    idf = {t:math.log((len(docs)-n+0.5)/(n+0.5)+1)for t,n in df.items()}
    scores:list[float] = []
    for d in docs:
        length = sum(d.values())
        score = 0.0
        for t in query_terms:
            tf = d.get(t)
            if tf:
                score += idf[t] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg_len))
        scores.append(score)
    return scores
# ------------------------------------------------------------------ 分块
#: 句子边界：中文/全角标点，外加英文的「句号 + 空白」。
#: 英文句号必须带空白才算边界，否则会把 "3.14" 从中间腰斩。
_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])|(?<=\.\s)")

#: 不得不用硬切时（整段没有可切标点），向前回退找断点的最大距离，
#: 以及允许落刀的位置。目的是别把英文单词劈成两半。
_HARD_CUT_WINDOW = 24
_HARD_CUT_AT = frozenset(" \t,，、;；!！?？)）]】>")


def _soft_wrap_limit(overlap: int, chunk_size: int) -> int:
    """算出 _soft_wrap 的行长上限，让 langchain 的重叠不可能归零。

    依据是 langchain_text_splitters/base.py:153-161 的弹出循环：

        total > chunk_overlap                              # (1)
        total + len(下一单元) > chunk_size and total > 0     # (2)

    条件 (1)：弹到只剩一个单元时，只有「这个单元 <= overlap」才停手。
    而 keep_separator=True 会让每个单元自带一个前导 "\\n"，所以

        行内容 + 1 <= overlap  ->  行内容 <= overlap - 1

    条件 (2)：最后一个单元 + 下一个单元都要 <= chunk_size，
    两边各按上限 limit + 1 估，得 limit <= chunk_size // 2 - 1。

    实测（3 个真实网页 / 28386 字符）：
        limit = overlap      -> 零重叠 7/76、2/31，min=0
        limit = overlap - 1  -> 零重叠 0/81，min=48
        本公式（6 组参数）    -> 零重叠 0/81、0/64、0/110、0/31、0/35、0/158

    旧代码取 overlap // 2，安全但把 149 字的行硬切成 75 字，
    实测被硬切的字符数多出约 5 倍，所以按精确边界取。
    """
    return max(min(overlap - 1, chunk_size // 2 - 1), 1)


def _soft_wrap(text: str, limit: int) -> str:
    """把超长行按句子边界打断，保证每一行不超过 limit 字符。

    为什么必须打断：langchain 的 chunk_overlap 是按「完整单元」整数丢弃的
    （base.py:153-161），只要上一块结尾的那个单元自身比 overlap 长，
    它就会被整个弹掉，剩下的继续弹 —— 极端情况弹空，重叠直接归零，
    而且不报错、不打日志。上限该取多少见 _soft_wrap_limit。

    另一个理由：langchain 只在「单元 >= chunk_size」时才递归降级切分，
    而递归出来的切点不受控。在这里先把行打断，单元稳定落在句子级别，
    重叠才可控。
    """
    out: list[str] = []
    for line in (text or "").splitlines():
        if len(line) <= limit:
            out.append(line)
            continue
        buf = ""
        for seg in _SENTENCE_END.split(line):
            while len(seg) > limit:        # 整句仍超长（无标点的长串）才硬切
                cut = limit
                for i in range(limit, max(limit - _HARD_CUT_WINDOW, 0), -1):
                    if seg[i - 1] in _HARD_CUT_AT:
                        cut = i
                        break
                out.append(seg[:cut])
                seg = seg[cut:]
            if buf and len(buf) + len(seg) > limit:
                out.append(buf)
                buf = seg
            else:
                buf += seg
        if buf:
            out.append(buf)
    return "\n".join(out)


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """把正文切成互相重叠的小块。

    对照源码：gpt_researcher/context/lexical.py:92

    和上游的一处不同：送进分块器之前先 _soft_wrap，
    否则 chunk_overlap 会静默失效（见 _soft_wrap 的说明）。
    """
    # langchain 在 chunk_overlap > chunk_size 时直接抛异常，先夹住；
    # 也为 _soft_wrap_limit 保证 chunk_size // 2 - 1 >= 1。
    overlap = max(0, min(overlap, chunk_size - 1))
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=_SEPARATORS,
        keep_separator=True,
    )
    prepared = _soft_wrap(text or "", _soft_wrap_limit(overlap, chunk_size))
    chunks = [c for c in splitter.split_text(prepared) if c.strip()]
    _warn_if_zero_overlap(chunks, overlap)
    return chunks


def _common_overlap(a: str, b: str, cap: int) -> int:
    """两个相邻块的真实重叠长度（a 的后缀 == b 的前缀）。"""
    cap = min(cap, len(a), len(b))
    for n in range(cap, 0, -1):
        if a[-n:] == b[:n]:
            return n
    return 0


def _warn_if_zero_overlap(chunks: list[str], overlap: int) -> None:
    """兜底哨兵：重叠归零是静默失败，必须留下痕迹。

    _soft_wrap_limit 正常情况已经保证了不会发生；这里防的是
    langchain 升级改了 _merge_splits、或者参数被改成极端值。
    """
    if overlap <= 0 or len(chunks) < 2:
        return
    zeros = sum(
        1 for i in range(1, len(chunks))
        if not _common_overlap(chunks[i - 1], chunks[i], overlap)
    )
    if zeros:
        logger.warning(
            "%d/%d 对相邻块没有重叠（chunk_overlap=%d 未生效），"
            "检查 _soft_wrap_limit 与 langchain 版本",
            zeros, len(chunks) - 1, overlap,
        )
# ------------------------------------------------------------------ 输出格式
def _block(index: int, title: str, url: str, text: str) -> str:
    return f"[{index}] {title or '(无标题)'}\nURL: {url}\n正文: {text}\n"


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
    blocks  = []
    for i,page in enumerate(pages,1):
        if top_n is not None and i>top_n:
            break
        url = page.get("url") or page.get("href") or ""
        text = page.get("raw_content") or page.get("content") or ""
        blocks.append(_block(i, page.get("title") or "", url, text))

    return "\n".join(blocks)

# ------------------------------------------------------------------ 主入口

def _keyword_select(query: str, pages: list[dict[str, Any]], cfg: Config) -> str:
    chunk_size = cfg.chunk_size
    overlap = min(cfg.chunk_overlap, chunk_size - 1)

    units: list[tuple[int, str]] = []          # (页码, 块文本)
    for pi, page in enumerate(pages):
        for chunk in chunk_text(page.get("raw_content") or "", chunk_size, overlap):
            units.append((pi, chunk))

    if not units:
        return ""

    scores = bm25_scores(query, [text for _, text in units])
    best = max(scores, default=0.0)

    if best <= 0:
        # 查询和正文一个字都对不上（换语言、拼写差异、纯符号查询）。
        # 退回"每页取开头一块"，至少保证有上下文可用 —— 对应原项目
        # lexical.py:99 的 `return chunks[:max_results]`。
        logger.warning("BM25 全为 0（查询与正文无词汇重合），退化为每页取开头")
        heads = []
        for page in pages:
            first = chunk_text(page.get("raw_content") or "", chunk_size, overlap)
            if first:
                heads.append({"title": page.get("title"), "url": page.get("url"),
                              "raw_content": first[0]})
        return pretty_print_docs(heads, top_n=cfg.keyword_max_results)

    ranked = sorted(range(len(units)), key=lambda i: (-scores[i], i))
    kept = [i for i in ranked
            if scores[i] >= cfg.keyword_relative_threshold * best][:cfg.keyword_max_results]

    logger.info(
        "BM25 保留 %d/%d 块（阈值 %.2f×最高分 %.3f）",
        len(kept), len(units), cfg.keyword_relative_threshold, best,
    )

    out = []
    for i in kept:
        pi, text = units[i]
        page = pages[pi]
        out.append({"title": page.get("title"), "url": page.get("url"),
                    "raw_content": text})
    return pretty_print_docs(out)

def select_context(query: str, pages: list[dict[str, Any]], *, cfg: Config | None = None) -> str:
    """挑出与 query 最相关的片段，拼成上下文。

    对照源码：gpt_researcher/context/select.py:53

    模式由 cfg.context_filter 决定，我们只实现 none / keyword 两种
    （auto 且无 TYPESAFE_API_KEY 时原项目也是走 keyword）。
    """
    cfg = cfg or load_config()
    mode = (cfg.context_filter or "auto").strip().lower()
    if mode == "auto":
        mode = "keyword"
    if mode not in ("keyword", "auto"):
        logger.warning("CONTEXT_FILTER=%r 本阶段未实现，回落 keyword", cfg.context_filter)
        mode = "keyword"
    usable = [p for p in pages if (p.get("raw_content") or "").strip()]
    if not usable:
        return ""
    if mode == "none":
        return pretty_print_docs(usable)
    total = sum(len(p.get("raw_content") or "") for p in usable)
    # 短路：内容本来就不多，筛选只会白忙一场
    # 对照源码：select.py:74 "Little content needs no filtering, whatever the mode."
    if total < cfg.compression_threshold and len(usable) <= cfg.keyword_max_results:
        logger.info("总字符 %d < 阈值 %d，跳过筛选", total, cfg.compression_threshold)
        return pretty_print_docs(usable)
    return _keyword_select(query, usable, cfg)
