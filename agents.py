"""
阶段9 最小多智能体编辑部 ——编排器 + 研究者 + 写作者 + 评审者
对照源码
D:\\gpt-researcher\\multi_agents\\          编辑、研究者、评审、修订四个角色协作
    gpt_researcher/skills/deep_research.py     多轮递归研究（同一思想的单 Agent 版）
    gpt_researcher/skills/curator.py           来源打分排序
    gpt_researcher/actions/agent_creator.py    按 query 选研究者人设
    和阶段8的分界线：
    阶段8 :顺序写死 —— 拆解题目-》检索 ——》写 -》存 跑几轮由代码决定
    阶段9：评审者的裁决决定下一步 ——通过就手工，不通过就把它提的 缺口 变成 下一轮的检索目标
    再走一遍 ”研究 -》 写 -》 评审“
    跑几轮，每轮抓什么，运行时才知道
对照 gpt_researcher/skills/researcher.py:97 conduct_research 的主循环，
区别在于我们的循环里多了一个能改变控制流的角色。

"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any,Callable

import llm
from config import Config, load_config
from llm import chat
from prompts import reviewer_prompt

logger = logging.getLogger(__name__)

MAX_ROUND_DEFAULT = 3
MAX_MISSING = 3
#: 同一个缺口（逐字比较）最多检索几次。实测（2026-10-11 三轮跑）：第 1 轮评审
#: 提的 3 条缺口在第 2、3 轮被逐字重复提出，第 3 轮重复同一批查询只多抓到 1 页 ——
#: 同一个查询跑第二次收益递减，第三次基本是白花配额和时间。
#: 已知简化：这里是逐字匹配，评审改写措辞就失效（要更稳得上相似度/embedding）。
MAX_SEARCH_PER_GAP = 1
@dataclass
class Verdict:
    """评审者裁决，编排器只看这个结构，不看散文"""
    passed:bool
    score:int=0
    missing:list[str]=field(default_factory=list)  # 再检索一轮有机会补上
    unsupported:list[str]=field(default_factory=list)  # 报告里没资料支撑的论断
    unfixable:list[str] = field(default_factory=list)  # 资料边界：再搜也补不上

    def as_dict(self)->dict[str, Any]:
        return {
            "passed":self.passed,
            "score":self.score,
            "missing":self.missing,
            "unsupported":self.unsupported,
            "unfixable":self.unfixable
        }

@dataclass
class ResearchState:
    """共享状态：所有角色读写同一份，这是多智能体协作的前提
     对照源码：上游 multi_agents 里那份 State —— 有了它，角色之间才是
    "通过状态通信"，而不是"函数 A 调函数 B"。
    """
    question:str
    max_rounds:int=MAX_ROUND_DEFAULT
    sub_queries:list[str]=field(default_factory=list)
    pages:list[dict[str,Any]]=field(default_factory=list)
    evidence:str=""
    draft:str=""
    open_questions:list[str]=field(default_factory=list)
    rounds:int=0
    blind_retries:int=0
    history:list[dict[str,Any]]=field(default_factory=list)
    previous_verdict:Verdict | None=None
    gap_tries:dict[str,int] = field(default_factory=dict) #缺口-》已检索次数

def _fingerprint(text:str)->str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]
def _merge_pages(old:list[dict[str,Any]],new:list[dict[str,Any]]) -> list[dict[str,Any]]:
    """有修改 一定有不同的版本啊
    合并两批页面，按URL归一化去重，保持”先到先得“的顺序
    这里复用pipeline 的 _normalize_url 同一份归一化规则不能在两个地方各写一遍，
    否则一处改了斜杠规则、另一处没改，就会出现"去重去不掉"这种难查的 bug。
    """
    from pipeline import _normalize_url
    seen = {_normalize_url(p.get("url") or "") for p in old} #用old的page造这个集合
    out = list(old)
    for page in new:#遍历新的
        key = _normalize_url(page.get("url") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(page)
    return out
def render_evidence(state:ResearchState,cfg:Config)->str:
    """把当前全部页面重新渲染成带[n]编号的上下文呢
    为什么每轮都全量重渲染，而不是把新证据“续号”追加：
    [1..n]连续编号是引用体系的跟(report.parse_context_source)
    增量续号是自己维护号池、还要处理筛选顺序变化，很容易错位
    全量重渲染只是本地BM25 几毫秒的事，换来的是编号永远自洽
    """
    from context import select_context
    if not state.pages:
        return ""
    query = " ".join([state.question,*state.sub_queries])
    return select_context(query,state.pages,cfg=cfg)
def _format_feedback(verdict:Verdict | None)->str:
    """把上一轮裁决翻成写作者能执行的三组要求。
    三组必须分开说，它的动作是相反的
    资料缺口-》去检索补上
    无据论断-》删掉
    资料边界-》不要再找数据，改为如实声明：无法回答
    上一版只回灌了 missing，导致评审者反复指出的归因错误到不了写作者手里 ——
    它每轮都在重复同样的错误，因为它根本不知道被指出过。
    """
    if verdict is None:
        return ""
    blocks:list[str] = []
    if verdict.missing:
        blocks.append( "资料缺口（去检索补上；补不上就明确写出补不上的原因）：\n"
            + "\n".join(f"- {x}" for x in verdict.missing))
    if verdict.unsupported:
        blocks.append(
            "无据论断（逐条处理：删掉，或降低措辞强度，或补上出处限定 —— "
            "写明是谁说的、是官方文档还是博客、是“初步验证”还是“已验证”）：\n"
            + "\n".join(f"- {x}" for x in verdict.unsupported)
        )
    if verdict.unfixable:
        blocks.append(
            "资料边界（不要去找数据，改为在正文或局限里如实声明"
            "“现有资料无法回答什么、为什么无法判断”）：\n"
            + "\n".join(f"- {x}" for x in verdict.unfixable)
        )
    return "\n\n".join(blocks)
def _pick_targets(state:ResearchState)->list[str]:
    """这一轮是检索什么，第一轮是规划器给的子问题（必查）
    之后是评审提的缺口，但同一个缺口最多查 MAX_SEARCH_PER_GAP次
    """
    if state.rounds <=1:
        return list(state.sub_queries)
    return [
        gap for gap in state.open_questions
        if state.gap_tries.get(gap,0) < MAX_SEARCH_PER_GAP
    ]
def write_draft(state:ResearchState,cfg:Config,feedback:Verdict | None)->str:
    """写作者：基于当前证据写一整篇报告 阶段6的write_report 直接复用"""
    from report import write_body

    return write_body(
        state.question,
        state.evidence,
        cfg=cfg,
        extra_instructions=_format_feedback(feedback),
    )
#: 关掉思维链：评审是"对照检查"，不是开放推理。
#: 实测（2026-10-11，deepseek-flash，854 字的评审提示词）：
#:   思考开 ct=845（其中推理 709）；关思考 ct=106，裁决结论基本一致。
#: 输出 token 单价是输入的 8 倍，关掉省的是最贵的那部分。
#: 备注：DeepSeek 原生等价写法是 extra_body={"thinking": {"type": "disabled"}}；
#:      写 thinking=False 会 422，写 chat_template_kwargs / enable_thinking 会被静默忽略。
NO_THINKING = {"reasoning_effort": "none"}
#: 评审额度。实测（2026-10-11）三轮评审的输出分别是 787 / 2000 / 775 token ——
#: 中间那次正好打满 2000，JSON 从中间断掉，整轮评审意见全丢（降级成 score=0）。
#: 截断的代价是「白花一次调用还不算，后面那轮重写少了评审意见」，所以给足 4000。
#: 注意 max_tokens 只是上限，模型不写那么多就不按上限计费。
REVIEW_MAX_TOKENS = 4000

def review_draft(state:ResearchState,cfg:Config)->Verdict:
    """评审者:给当前草稿打分并提出可执行的缺口"""
    from pipeline import parse_json
    prompt = reviewer_prompt(state.question,state.draft,state.evidence,round_no=state.rounds,max_rounds=state.max_rounds,previous_gaps=state.open_questions or None)
    try:

        raw = chat(
            [{"role":"user","content":prompt}],
            cfg=cfg,
            temperature=0.0,
            max_tokens=REVIEW_MAX_TOKENS,
            json_mode=True,
            step="评审者",
            extra_body=NO_THINKING
        )
    except Exception as exc:
        # 评审失败不该让已经花了钱的研究白跑。降级成"未通过但无具体意见"，
        # 由 blind_retries 保证最多再重写一次。降级不是静默失败，静默才是。
        logger.warning("评审调用失败，降级为「未通过且无具体意见」：%s", exc)
        return Verdict(passed=False)
    verdict = _normalize_verdict(parse_json(raw), raw)
    # 代码级不变量：模型说通过，但缺口/无据论断非空 —— 不采信。
    # 提示词是"软约束"，模型偶尔会说 pass=true 同时列出三条缺口，这条兜住它。
    if verdict.passed and (verdict.missing or verdict.unsupported):
        logger.warning(
            "评审判通过但 missing/unsupported 非空（missing=%s unsupported=%s），按未通过处理",
            verdict.missing,
            verdict.unsupported,
        )
        verdict.passed = False
    return verdict

def _normalize_verdict(parsed:Any,raw:str)->Verdict:
    """把评审者的输出收拾成Verdict 解析不出来时返回【未通过+无具体意见】
    为什么不直接抛异常：评审是锦上添花的一环，它坏了不该让整轮研究白跑。
    但也不能静默 —— 调用方靠"missing 为空"识别这种情况，并用 blind_retries 限制
    它最多重写一次，否则模型输出一坏就会无限循环烧钱。

    对照源码：actions/query_processing.py:11 _normalize_sub_queries 的同一种防御。
    """
    if not isinstance(parsed,dict):
        # 必须把 finish_reason 打出来：JSON 解析失败最常见的原因是输出被
        # max_tokens 截断（finish_reason=length），只看「不是 JSON」会误判成模型胡说。
        logger.warning(
            "评审输出不是 JSON（finish_reason=%s，原文 %d 字），"
            "按「未通过且无具体意见」处理。原始前 200 字：%r",
            llm.LAST_FINISH_REASON,
            len(raw or ""),
            (raw or "")[:200],
        )
        return Verdict(passed=False)
    passed = parsed.get("pass")
    if isinstance(passed,str):
        passed = passed.strip().lower() in ("true", "yes", "1", "是", "通过")

    def _as_list(key:str)->list[str]:
        value = parsed.get(key)
        if isinstance(value,str):
            value = [value]
        if not isinstance(value,list):
            return []
        def _text(v:Any)->str:
            # 模型有时把一条意见写成对象（实测出现过 [{"text": "..."}]）。
            # 若按「不是字符串就丢」，等于把评审意见静默吃掉 ——
            # 这里尽量取出正文，取不出来的才丢，并且一定要留日志。
            if isinstance(v,str):
                return v.strip()
            if isinstance(v,dict):
                for k in ("text","reason","issue","description","detail"):
                    if isinstance(v.get(k),str):
                        return v[k].strip()
            return ""

        items = [t for t in (_text(v) for v in value) if t]
        dropped = len(value) - len(items)
        if dropped:
            logger.warning("评审的 %s 有 %d 条取不出正文，已丢弃", key, dropped)
        return items[:MAX_MISSING]

    score = parsed.get("score")
    if not isinstance(score,int) or isinstance(score,bool):
        score = 0
    return Verdict(bool(passed),score,_as_list("missing"), _as_list("unsupported"),_as_list("unfixable"))

def run_editorial(
        question:str,
        *,
        cfg:Config | None=None,
        max_rounds:int=MAX_ROUND_DEFAULT,
        ask:Callable[[str],str] | None=None,
)->dict[str,Any]:
    """编排器：唯一的循环，每轮调度 研究者-》写作者-》评审者
    对照源码：上游 multi_agents 的编排入口 + skills/researcher.py:97 的主循环。

    阶段 10：ask 是注进来的"问人"函数（命令行传 input）。ask=None 表示没人审，
    拆完提纲直接开跑；给上 ask 就变成 拆提纲 -> 人审 -> （打回就重拆）-> 开跑。
    """
    from pipeline import _collect_pages,_cost_report_hook,plan_research
    from plan_review import review_plan
    from report import  save_report

    cfg = cfg or load_config()
    state = ResearchState(question=question,max_rounds=max_rounds)
    state.sub_queries = review_plan(question,cfg=cfg,ask=ask)

    while state.rounds < max_rounds:
        state.rounds += 1
        targets = _pick_targets(state)              # 原来那行长条件
        logger.info(
            "第 %d/%d 轮开始：研究者目标 %d 条（%s）",
            state.rounds,
            max_rounds,
            len(targets),
            "评审缺口" if state.open_questions else "原始子问题",
        )
        #----研究者---
        before = len(state.pages)
        if targets:
            try:
                pages = _collect_pages(targets,cfg=cfg)
            except Exception as exc:
                logger.warning("研究者这一轮失败，用已有证据继续：%s", exc)
                pages = []
            if pages:
                state.pages = _merge_pages(state.pages, pages)
            for gap in targets:  # 计一次尝试
                state.gap_tries[gap] = state.gap_tries.get(gap, 0) + 1
        else:
            logger.info("评审没有提出新的检索目标，本轮只重写不检索")
        state.evidence = render_evidence(state,cfg=cfg) #重新渲染
        if not state.evidence.strip():
            logger.warning("手上没有任何可用证据，停止（不出报告）")
            return {
                "report": f'没有为"{question}"抓到任何可用资料',
                "path": None,
                "rounds": state.rounds,
                "history": state.history,
                "cost_report": _cost_report_hook(cfg),
            }
        logger.info(
            "证据：本轮新增 %d 页，累计 %d 页 -> 上下文 %d 字符",
            len(state.pages) - before,
            len(state.pages),
            len(state.evidence),
        )
        if len(state.pages) == before:
            logger.warning("本轮没有抓到任何新页面，检索可能已到信息边界")
        #----写作者
        try:
            draft = write_draft(state,cfg,state.previous_verdict)
        except Exception as exc:
            # 写作者失败，不该把已经花掉的检索和上一轮草稿一起扔掉。
            # 局部失败降级：保留上一轮草稿收工，原因打在日志里（不做静默失败）。
            logger.warning("写作者这一轮失败，保留上一轮草稿收工：%s", exc)
            break
        state.draft = draft
        if not state.draft.strip():
            logger.warning("写作者这一轮产出为空，停止")
            break

        #---评审者
        verdict = review_draft(state,cfg=cfg)
        state.history.append({
            "round": state.rounds,
            "targets": list(targets),
            "pages_total": len(state.pages),
            "draft_sha":_fingerprint(state.draft),
            "verdict":verdict.as_dict()
        })
        state.previous_verdict = verdict
        state.open_questions = verdict.missing
        logger.info(
            "第 %d 轮评审：pass=%s score=%d\n  缺口=%s\n  无据=%s\n  边界=%s",
            state.rounds,
            verdict.passed,
            verdict.score,
            verdict.missing,
            verdict.unsupported,
            verdict.unfixable,
        )
        #---终止条件：按“最该停的”排前面，每条都必须显
        if verdict.passed:
            logger.info("评审通过，收工")
            break
        if len(state.history)>=2 and state.history[-2]["draft_sha"] == _fingerprint(state.draft):
            #重写产出一摸一样，说明模型卡住了，再跑只是烧钱
            logger.warning("本轮草稿与上一轮完全相同，继续重写不会变好，提前收工")
            break
        if not verdict.missing:
            state.blind_retries += 1
            if state.blind_retries > 1:
                logger.warning(
                    "评审连续两轮没给出可执行的缺口，停止（报告可能仍有问题）"
                )
                break
        if state.rounds > 1 and not targets and state.open_questions:
            logger.warning(
                "评审的缺口都已在前面轮次检索过（同一缺口最多查 %d 次），"
                "不再重复检索，按当前稿收工",
                MAX_SEARCH_PER_GAP,
            )
            break
        if targets and len(state.pages) == before and verdict.missing:
            logger.warning(
                "本轮没有抓到任何新页面，缺口却仍然非空 —— 再检索也不会有新信息，提前收工"
            )
            break
    if not state.draft.strip():
        return {
            "report": "",
            "path": None,
            "rounds": state.rounds,
            "history": state.history,
            "cost_report": _cost_report_hook(cfg),
        }
    try:
        report_text = finalize_report(state, cfg)
    except Exception as exc:
        # 补引言/结论失败不该让整篇报告作废：正文本身是完整的
        logger.warning("补引言/结论失败，直接交正文：%s", exc)
        report_text = state.draft
    path = save_report(question, report_text)
    logger.info(
        "报告已写入 %s（%d 字，共 %d 轮）", path, len(report_text), state.rounds
    )
    return {
        "report": report_text,
        "path": str(path),
        "rounds": state.rounds,
        "history": state.history,
        "cost_report": _cost_report_hook(cfg),
    }
            
def finalize_report(state:ResearchState,cfg:Config)->str:
    """收工 在最后一轮正文上补上"""
    from report import add_side_sections,parse_context_sources
    sources = parse_context_sources(state.evidence)
    return add_side_sections(state.draft,state.question,state.evidence,sources,cfg=cfg)


