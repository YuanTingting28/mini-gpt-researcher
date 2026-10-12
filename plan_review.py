"""阶段 10：提纲人审（human-in-the-loop）。

对照源码
  multi_agents/agents/human.py            HumanAgent.review_plan
                                          把提纲交给用户，用户回 "no" 表示接受，其余当反馈
  multi_agents/agents/plan_review.py:1    DEFAULT_MAX_PLAN_REVISIONS = 3
  multi_agents/agents/plan_review.py:8    route_human_feedback(review, max_plan_revisions)
                                          超出上限抛 MaxPlanRevisionsExceededError
  multi_agents/agents/orchestrator.py     add_conditional_edges('human', ...,
                                          {"accept": "researcher", "revise": "planner"})
                                          并且专门把超限异常接住当成 accept —— 放行，不是崩掉

为什么单独一个文件：人审是"把控制流交给外部"，和编排器（agents.py）不是一回事，
上游也是分开的（human.py / plan_review.py）。
"""
from __future__ import annotations

import logging
from typing import Callable

from config import Config

logger = logging.getLogger(__name__)

#提纲最多打回几次，上有 multi_agents plan_review 是3
#超限不是报错，是强制放行
MAX_PLAN_REVISIONS = 3
#用户输入里能被识别的关键词
_ACCEPT_WORDS = ("", "y", "yes", "ok", "好", "是", "接受", "通过")
_REVISE_WORDS = ("n", "no", "否", "不是", "打回")
_EDIT_WORDS = ("e", "edit", "改", "编辑")

def _strip_bullet(line: str) -> str:
    """剥掉用户粘进来的序号/项目符号:"1. xxx" / "1、xxx" / "- xxx" / "* xxx"。

    上游没有这一步（它的人审只收 yes/no），"允许手改提纲"是我们加的能力，
    所以这层解析也得自己写。
    """
    text =  (line or "").strip()
    for prefix in ("- ","* ","· "):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    i = 0
    while i < len(text) and text[i].isdigit():
        i += 1
    if 0 < i < len(text) and text[i] in ".、)）":
        return text[i + 1 :].strip()
    return text

def _ask(ask:Callable[[str],str],prompt:str) -> str:
    """读一行用户输入
    stdin 已经关了 会抛出错误
    不能让"读不到人话"把整条研究链路炸掉，也不能静默地当成没开启人审。
    这里按“接受”继续，并留下一条warning
    """
    try:
        return ask(prompt)
    except EOFError:
        logger.warning("stdin 已结束，读不到输入，这一问按「接受」处理")
        return ""

def _echo_plan(sub_queries:list[str], out:Callable[...,None]) -> None:
    out("")
    out(f"研究提纲（{len(sub_queries)}条）：")
    for n,item in enumerate(sub_queries,1):
        out(f"  {n}. {item}")
    out("")

def human_review(
        sub_queries:list[str],
        ask:Callable[[str],str],
        *,
        out:Callable[...,None]=print,
) -> tuple[list[str],str]:
    """把提纲交给用户看一眼，返回 (子问题列表, 动作)。

    动作三选一：
      accept —— 直接用这份提纲（回车 / y / 是）
      revise —— 打回，让 planner 重拆（n / no / 否）—— 要再花一次 LLM 调用
      edit   —— 用户自己写一份 —— 一次 LLM 调用都不花

    为什么把 revise 和 edit 分开：两者代价完全不同（一个要重拆、一个不要）。
    混成一个"有反馈"信号，编排器就没法记账，也没法给人审设上限。

    ask 是可注入的输入函数（命令行传 input）—— 抽出来是为了离线自检能打桩，
    否则自检会真的卡在键盘上等输入。
    """
    _echo_plan(sub_queries, out)
    raw = _ask(ask,"回车=接受，n=打回重拆,e=自己改>")
    text = (raw or "").strip()
    low = text.lower()

    if low in _ACCEPT_WORDS:
        return list(sub_queries), "accept"
    if low in _REVISE_WORDS:
        return list(sub_queries), "revise"
    # 进入手改模式。两种情况：
    #   1. 用户敲了 e —— 接下来逐行输入，空行结束
    #   2. 用户直接把一条子问题粘了进来（很常见）—— 把这一行当第一条，接着读
    if low in _EDIT_WORDS:
        edited: list[str] = []
        out("逐行输入子问题，空行结束：")
    else:
        edited = [_strip_bullet(text)]
        out("收到 1 条，继续逐行输入，空行结束：")

    while True:
        line = _strip_bullet(_ask(ask, "  > "))
        if not line:
            break
        edited.append(line)

    if not edited:
        # 说了要改却什么都没输入：不能猜用户想"删光"，按接受处理并留痕
        logger.warning("编辑模式没有输入任何内容，按接受原提纲处理")
        return list(sub_queries), "accept"
    return edited, "edit"

def review_plan(
        question:str,
        cfg:Config,
        ask:Callable[[str],str] | None=None,
        *,
        planner:Callable[...,list[str]] | None=None,
        max_revisions:int=MAX_PLAN_REVISIONS,
        out:Callable[...,None]=print,
)->list[str]:
    """
    拆子问题 -> （可选）人审 -> 打回就重拆，最多重拆 max_revisions 次。
    ask=None 表示没接人审（命令行走 --human），直接采用自动拆解的结果。
       抽 planner 参数是为了离线自检能打桩，不然自检要真调一次 LLM。

       为什么超限是"强制放行"而不是抛异常：
       上游 orchestrator.py 专门把 MaxPlanRevisionsExceededError 接住当成 accept，
       理由是"让研究继续跑下去，而不是死在图的递归上限上"。
       人审是加分项，不该成为整条链路的单点故障。

    """
    if planner is None:
        from pipeline import plan_research as planner
    sub_queries = planner(question,cfg=cfg)
    if ask is None:
        logger.info("没有接入人审（ask=None），直接采用自动拆解的子问题")
        return sub_queries
    revisions = 0
    while True:
        sub_queries,action = human_review(sub_queries, ask, out=out)
        if action == "accept":
            logger.info("提纲已确认：%d 条", len(sub_queries))
            return sub_queries
        if action == "edit":
            logger.info("提纲由人工改写：%d 条", len(sub_queries))
            return sub_queries
        # action == "revise"：打回重拆
        revisions += 1
        if revisions > max_revisions:
            logger.warning("打回次数已达上限 %d，按当前版本强制放行", max_revisions)
            return sub_queries
        logger.info("提纲被打回，第 %d/%d 次重拆", revisions, max_revisions)
        sub_queries = planner(question, cfg=cfg)