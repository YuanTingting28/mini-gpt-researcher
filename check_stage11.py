"""阶段 11 离线自检：事实核查员，以及它和评审员的分工。全程打桩，不联网、不占键盘。

用法：python check_stage11.py
"""
import pathlib

import agents
import fact_checker
import main as cli
import pipeline
import prompts
import report
from agents import ResearchState, Verdict

FAILED = []
REAL_CHECK_FACTS = fact_checker.check_facts
REAL_CHAT = fact_checker.chat

def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<40} {extra}")
    if not ok:
        FAILED.append(name)

CFG = type("CFG", (), {"max_concurrency": 2, "max_pages": 5})()

COLLECTED = []
WRITE_FEEDBACK = []

def fake_plan(query, *, cfg=None):
    return ["子问题甲"]

def fake_collect(targets, cfg):
    COLLECTED.append(list(targets))
    return [{"title": "T", "url": "https://a.com/1", "raw_content": "正文" * 20}]

def fake_render(state, cfg):
    return "[1] T\nURL: https://a.com/1\n正文: 内容"

def fake_write(state, cfg, feedback):
    WRITE_FEEDBACK.append(feedback)
    return f"# 草稿第 {len(WRITE_FEEDBACK)} 版\n\n引用 [1]。\n"

pipeline.plan_research = fake_plan
pipeline._collect_pages = fake_collect
report.save_report = lambda q, rep, **kw: pathlib.Path("outputs/fake.md")
agents.render_evidence = fake_render
agents.finalize_report = lambda state, cfg: state.draft + "\n## 结论\n\n假结论\n"

def reset(review=None, facts=None):
    COLLECTED.clear()
    WRITE_FEEDBACK.clear()
    agents.write_draft = fake_write
    agents.review_draft = review or (lambda state, cfg: Verdict(True, 9, [], []))
    fact_checker.check_facts = facts or (lambda state, cfg: Verdict(passed=True))

# ---------------- 1. 核查不过 -> 重写 -> 第二轮过
seen = []
def fact_twice(state, cfg):
    seen.append(state.rounds)
    if len(seen) == 1:
        return Verdict(False, 0, [], ["第 2 段引的 [1] 里没有这个数字"])
    return Verdict(passed=True)

reset(facts=fact_twice)
r = agents.run_editorial("Q", cfg=CFG, max_rounds=4, with_fact_check=True)
check("核查不过 -> 再重写一轮", r["rounds"] == 2, f"rounds={r['rounds']}")
check("收工时仍有报告可交付", bool(r["report"]) and r["path"], str(r["path"]))
check("重写用的是核查给的无据", WRITE_FEEDBACK[1] is not None
      and WRITE_FEEDBACK[1].unsupported == ["第 2 段引的 [1] 里没有这个数字"],
      str(WRITE_FEEDBACK[1]))
check("最终稿是重写后的那版", "第 2 版" in r["report"], r["report"][:16])

# ---------------- 2. 核查一直不过 -> 打满轮数，但草稿要保住
reset(facts=lambda state, cfg: Verdict(False, 0, [], ["永远无据"]))
r = agents.run_editorial("Q", cfg=CFG, max_rounds=3, with_fact_check=True)
check("核查一直不过 -> 打满轮数收工", r["rounds"] == 3, f"rounds={r['rounds']}")
check("打满轮数也不丢报告", bool(r["report"]) and r["path"], str(r["path"]))

# ---------------- 3. 说了不过却没给意见 -> 就地放行，不许原样重写
reset(facts=lambda state, cfg: Verdict(False, 0, [], []))
r = agents.run_editorial("Q", cfg=CFG, max_rounds=4, with_fact_check=True)
check("空裁决 -> 放行，不烧轮数", r["rounds"] == 1, f"rounds={r['rounds']}")

# ---------------- 4. check_facts 自己的失败路径（打桩 chat）
state = ResearchState(question="Q", draft="# 草稿\n引用 [1]。", evidence="[1] T\n正文: 内容")
fact_checker.check_facts = REAL_CHECK_FACTS   # 上面几段把 check_facts 打桩了，这里换回真实现

def boom(*a, **k):
    raise RuntimeError("模拟网络炸了")

fact_checker.chat = boom
v = fact_checker.check_facts(state, CFG)
check("核查调用失败 -> 放行且不抛", v.passed is True, f"passed={v.passed}")

fact_checker.chat = lambda *a, **k: "这不是 JSON"
v = fact_checker.check_facts(state, CFG)
check("核查输出坏 JSON -> 未通过且无意见", v.passed is False and v.unsupported == [], str(v))

fact_checker.chat = lambda *a, **k: '{"pass": true, "unsupported": ["第 1 段无据"]}'
v = fact_checker.check_facts(state, CFG)
check("说通过却列了无据 -> 按未通过", v.passed is False
      and v.unsupported == ["第 1 段无据"], str(v))

fact_checker.chat = lambda *a, **k: '{"pass": false, "unsupported": ["[3] 里没有这个数字"]}'
v = fact_checker.check_facts(state, CFG)
check("正常解析 -> 未通过 + 一条无据", v.passed is False
      and v.unsupported == ["[3] 里没有这个数字"], str(v))
fact_checker.chat = REAL_CHAT

# ---------------- 5. 坏 JSON 走完整链路：不许死循环
reset()
fact_checker.check_facts = REAL_CHECK_FACTS
fact_checker.chat = lambda *a, **k: "抱歉，我无法按格式输出"
r = agents.run_editorial("Q", cfg=CFG, max_rounds=4, with_fact_check=True)
check("坏 JSON 的核查不触发死循环", r["rounds"] == 1, f"rounds={r['rounds']}")
fact_checker.chat = REAL_CHAT

# ---------------- 6. history 里两份裁决分开留痕
reset(facts=lambda state, cfg: Verdict(False, 0, [], ["无据 X"]))
r = agents.run_editorial("Q", cfg=CFG, max_rounds=1, with_fact_check=True)
h = r["history"][0]
check("history 单独记了核查结果", h.get("fact") is not None
      and h["fact"]["unsupported"] == ["无据 X"], str(h.get("fact")))
check("合成后的裁决字段齐全",
      {"passed", "score", "missing", "unsupported", "unfixable"} <= set(h["verdict"]),
      str(set(h["verdict"])))

# ---------------- 7. 不开核查：一切照旧
reset()
r = agents.run_editorial("Q", cfg=CFG, max_rounds=2)
check("不开核查：评审过了就收工", r["rounds"] == 1, f"rounds={r['rounds']}")
check("不开核查：history 里 fact 为 None", r["history"][0].get("fact") is None)

flags = []
def record_write(state, cfg, feedback):
    flags.append(state.fact_check_on)
    return "# 草稿\n"

reset()
agents.write_draft = record_write
agents.run_editorial("Q", cfg=CFG, max_rounds=1, with_fact_check=True)
check("开关写进了共享状态", flags == [True], str(flags))
check("state 默认不开核查", ResearchState(question="Q").fact_check_on is False)

# ---------------- 8. 分工：两边提示词不许重叠
fp = prompts.fact_checker_prompt("Q", "# 草稿", "[1] 资料")
rp_on = prompts.reviewer_prompt("Q", "# 草稿", "[1] 资料",
                                round_no=1, max_rounds=3, fact_check_on=True)
rp_off = prompts.reviewer_prompt("Q", "# 草稿", "[1] 资料", round_no=1, max_rounds=3)
check("核查员只输出 pass / unsupported", '"pass"' in fp and '"unsupported"' in fp)
check("核查员不碰缺口与边界字段", '"missing"' not in fp and '"unfixable"' not in fp)
check("评审员仍然管缺口与边界", '"missing"' in rp_on and '"unfixable"' in rp_on)
check("评审员 JSON 结构没变", '"pass"' in rp_on and '"score"' in rp_on)
check("开核查后评审员让出无据", "资料支撑不了" not in rp_on)
check("关核查时评审员自己管无据", "资料支撑不了" in rp_off)

# ---------------- 9. 两个角色的合并规则
m = agents._merge_verdicts(Verdict(True, 8, [], []), Verdict(False, 0, [], ["无据A"]))
check("合一：两个都过才算过", m.passed is False and m.unsupported == ["无据A"], str(m))
m = agents._merge_verdicts(Verdict(False, 3, ["缺"], ["无据A"]),
                           Verdict(False, 0, [], ["无据B"]))
check("无据取并集（保序去重）", m.unsupported == ["无据A", "无据B"], str(m.unsupported))
check("缺口与边界只认评审员", m.missing == ["缺"] and m.unfixable == [], str(m))
check("分数沿用评审员", m.score == 3, str(m.score))
m = agents._merge_verdicts(Verdict(False, 2, [], ["同一句"]), Verdict(False, 0, [], ["同一句"]))
check("两边重复的无据只留一条", m.unsupported == ["同一句"], str(m.unsupported))
m = agents._merge_verdicts(Verdict(True, 8, [], []), Verdict(False, 0, [], []))
check("没意见的「不过」降级为放行", m.passed is True, str(m))
m = agents._merge_verdicts(Verdict(True, 8, [], []), None)
check("没开核查时原样返回", m.passed is True and m.score == 8, str(m))

# ---------------- 10. 命令行接线
args = cli.build_parser().parse_args(["agents", "Q", "--fact-check"])
check("--fact-check 已接线", args.fact_check is True, str(getattr(args, "fact_check", None)))
args = cli.build_parser().parse_args(["agents", "Q"])
check("默认不开事实核查", args.fact_check is False, str(getattr(args, "fact_check", None)))
args = cli.build_parser().parse_args(["agents", "Q", "--human", "--fact-check"])
check("--human 与 --fact-check 可同给", args.human is True and args.fact_check is True)

print()
print("全部通过" if not FAILED else f"{len(FAILED)} 项失败：{FAILED}")
raise SystemExit(1 if FAILED else 0)