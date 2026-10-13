"""
阶段11 事实核查员——只判”这句话在资料里有没有依据”，不判结构
multi_agents/agents/fact_checker.py          FactCheckerAgent.check_facts
      把 Introduction + Sections Data + Conclusion 拼成整篇交给模型逐句核对；
      提示词要求"如果事实无误，只返回字符串 None"
multi_agents/agents/fact_review.py           route_fact_check(state, max_revisions)
      notes 为空/None -> "accept"（放行）；超过上限抛 MaxFactCheckRevisionsExceededError
multi_agents/agents/orchestrator.py          把上面那个异常接住当成 accept —— 放行，不是崩掉
multi_agents/agents/utils/none_sentinels.py  is_none_accept_response
      严格匹配 "None" 这个哨兵，明确写着不能拿子串去判断
      （"None of the criteria are met" 不算通过）
和上游的两处分歧，都是有意的
1.不用"返回字符串 None"当通过信号。
   我们全线是 json_mode=True（response_format={"type": "json_object"}）。
   在 JSON 协议里再塞一个非 JSON 的哨兵，解析层就得先猜"这是哨兵还是坏 JSON"，
   同一种信息两种编码是 bug 温床。这里改成 JSON 里的 pass 字段，
   和评审员完全同构 —— 两边都归一到 agents.Verdict。
2. 不设独立的重写预算（上游是 DEFAULT_MAX_FACT_CHECK_REVISIONS）。
   上游是 LangGraph 条件边，必须给环设上限，否则图递归不收敛。
   我们的编排器本身就是个带上限的 while 循环，再叠一个预算只是多一个要维护的常量。
   真正需要单独兜住的是"说了未通过、却一条无据论断都没给"这种没法执行的裁决 ——
   见 agents._merge_verdicts，那里就地把它降级成放行。
为什么单独一个文件：上游也是独立的（fact_checker.py / fact_review.py）。
核查员有自己的提示词、自己的解析、自己的失败策略，塞进 agents.py 只会让它继续膨胀。
"""
from __future__ import annotations

import logging

from agents import NO_THINKING, Verdict, _normalize_verdict
from config import Config
from llm import chat
from prompts import fact_checker_prompt

logger = logging.getLogger(__name__)

#核查额度 和评审员同理
#截断的代价是 白花一次调用 + 这一轮核查意见对调
#max_tokens 只是上限，模型不写那么多就不按
FACT_MAX_TOKENS = 4000

def check_facts(state,cfg:Config)->Verdict:
    """事实核查员:只回答一个问题——草稿里的话，资料支持吗？

    产出只有一类：unsupported（哪句话在资料里找不到依据）。
    结构和缺口不归它管，那是评审员的活 —— 分工写死在两边的提示词里，
    否则两个角色会重复提同一批意见，等于同一件事付两次钱。

    state 只要有 question / draft / evidence 三个字段就够（ResearchState 满足）。
    """
    from pipeline import parse_json
    prompt = fact_checker_prompt(state.question,state.draft,state.evidence)
    try:
        raw = chat(
            [{"role":"user","content":prompt}],
            cfg=cfg,
            temperature=0.0,
            max_tokens=FACT_MAX_TOKENS,
            json_mode=True,
            step="事实核查",
            extra_body=NO_THINKING,
        )
    except Exception as exc:
        # 核查失败不该让已经写好的报告作废（局部失败降级）。
        # 注意降级方向：这里按「没发现问题」放行，而不是按「有问题」。
        # 判「有问题」会触发一轮重写，可重写的依据（到底哪句无据）已经跟着异常丢了 ——
        # 那才是最坏的失败：花了钱、拿不到信息、还把正文改乱了。
        # 放行不是静默失败：日志里留了痕，编排器也会把原因带进 history。
        logger.warning("事实核查调用失败，本轮按「未发现问题」放行：%s", exc)
        return Verdict(passed=True)
    verdict = _normalize_verdict(parse_json(raw),raw)
    # 和 review_draft 同一条代码级不变量：说通过、却又列出一堆无据论断的，不采信。
    # 提示词是软约束，模型偶尔会自相矛盾，这行兜住它。
    if verdict.passed and verdict.unsupported:
        logger.warning(
            "事实核查判通过却给出了无据论断（%s），按未通过处理", verdict.unsupported
        )
        verdict.passed = False
    return verdict