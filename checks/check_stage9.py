"""阶段 9 离线自检：把 LLM 和网络全部打桩，只验编排逻辑。

用法：python check_stage9_offline.py
"""
import pathlib

import agents
import pipeline
import report
from agents import ResearchState, Verdict

FAILED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<40} {extra}")
    if not ok:
        FAILED.append(name)


CFG = type("CFG", (), {"max_concurrency": 2, "max_pages": 5})()

COLLECT_TARGETS = []
WRITE_FEEDBACK = []


# ---------------- 打桩 ----------------
def fake_plan(query, *, cfg=None):
    return ["子问题甲", "子问题乙"]


def fake_collect(targets, cfg):
    COLLECT_TARGETS.append(list(targets))
    return [{"title": "T", "url": "https://a.com/1", "raw_content": "正文" * 20}]


def fake_render(state, cfg):
    return "[1] T\nURL: https://a.com/1\n正文: 内容"


def fake_write(state, cfg, feedback):
    WRITE_FEEDBACK.append(feedback)
    return f"# 草稿第 {len(WRITE_FEEDBACK)} 版\n\n引用 [1]。\n"


def fake_save(question, rep, **kwargs):
    return pathlib.Path("outputs/fake.md")


pipeline.plan_research = fake_plan
pipeline._collect_pages = fake_collect
report.save_report = fake_save
agents.render_evidence = fake_render


def reset():
    COLLECT_TARGETS.clear()
    WRITE_FEEDBACK.clear()
    agents.write_draft = fake_write


# ---------------- 1. 第 2 轮通过
reset()
agents.review_draft = lambda state, cfg: (
    Verdict(True, 9, [], []) if state.rounds >= 2 else Verdict(False, 5, ["缺 A"], [])
)
r = agents.run_editorial("Q", cfg=CFG, max_rounds=3)
check("第 2 轮通过后收工", r["rounds"] == 2, f"rounds={r['rounds']}")
check("第 2 轮的研究目标 = 评审提的缺口",
      COLLECT_TARGETS[1] == ["缺 A"], str(COLLECT_TARGETS))
check("缺口被传给了写作者",
      WRITE_FEEDBACK[1] is not None and WRITE_FEEDBACK[1].missing == ["缺 A"],
      str(WRITE_FEEDBACK[1]))

fb = agents._format_feedback(Verdict(False, 5, ["缺口X"], ["无据Y"], ["边界Z"]))
check("三类意见分组进提示词",
      all(k in fb for k in ("资料缺口", "无据论断", "资料边界")) and "边界Z" in fb)
check("没有裁决时反馈为空", agents._format_feedback(None) == "")
check("最终稿是最后一轮的产物", "第 2 版" in r["report"], r["report"][:16])
check("history 记了两轮", len(r["history"]) == 2, str(len(r["history"])))

# ---------------- 2. 一直不通过：必须在轮数上限收工
reset()
agents.review_draft = lambda state, cfg: Verdict(False, 4, ["还有缺口"], [])
r = agents.run_editorial("Q", cfg=CFG, max_rounds=2)
check("一直不通过 -> 在轮数上限停下", r["rounds"] == 2, f"rounds={r['rounds']}")
check("停下时仍然有报告可交付", bool(r["report"]) and r["path"], str(r["path"]))

# ---------------- 3. 评审输出坏掉：盲重写最多一次
reset()
agents.review_draft = lambda state, cfg: Verdict(False, 0, [], [])
r = agents.run_editorial("Q", cfg=CFG, max_rounds=5)
check("无具体缺口 -> 盲重写一次后收工", r["rounds"] == 2, f"rounds={r['rounds']}")

# ---------------- 4. 草稿没有变化：提前收工，不打满轮数
reset()
agents.write_draft = lambda state, cfg, feedback: "# 一模一样的草稿"
agents.review_draft = lambda state, cfg: Verdict(False, 3, ["缺口X"], [])
r = agents.run_editorial("Q", cfg=CFG, max_rounds=5)
check("草稿无变化 -> 提前收工", r["rounds"] == 2, f"rounds={r['rounds']}")

# ---------------- 5. 没有证据：不出报告
reset()
pipeline._collect_pages = lambda targets, cfg: []
agents.render_evidence = lambda state, cfg: ""
r = agents.run_editorial("Q", cfg=CFG, max_rounds=2)
check("没有证据就不出报告", r["path"] is None and "没有" in r["report"],
      repr(r["report"][:20]))

# ---------------- 6. 裁决的归一化
v = agents._normalize_verdict({"pass": "true", "score": 7, "missing": "只有一条"}, "x")
check("字符串 pass 能识别", v.passed is True and v.score == 7)
check("字符串 missing 变单元素列表", v.missing == ["只有一条"])
v = agents._normalize_verdict(
    {"pass": False, "missing": [f"缺口{i}" for i in range(9)]}, "x")
check("missing 封顶 3 条", len(v.missing) == 3, str(len(v.missing)))
v = agents._normalize_verdict(None, "不是 json")
check("解析失败 -> 未通过且无意见", v.passed is False and v.missing == [])
# ---------------- 7. 契约：main.py 要读的字段必须存在
NEED = {"round", "targets", "pages_total", "verdict"}
V_NEED = {"passed", "score", "missing", "unsupported"}

reset()
pipeline._collect_pages = fake_collect
agents.render_evidence = fake_render
agents.review_draft = lambda state, cfg: Verdict(False, 4, ["缺口"], [])
r = agents.run_editorial("Q", cfg=CFG, max_rounds=1)
check("契约轮至少跑了一轮", len(r["history"]) == 1, str(len(r["history"])))
for h in r["history"]:
    check("history 字段齐全", NEED <= set(h), str(NEED - set(h)))
    check("verdict 字段齐全", V_NEED <= set(h["verdict"]), str(V_NEED - set(h["verdict"])))
print()
print("全部通过" if not FAILED else f"{len(FAILED)} 项失败：{FAILED}")
raise SystemExit(1 if FAILED else 0)