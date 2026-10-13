"""阶段 10 离线自检：提纲人审。全部靠打桩，不联网、不占键盘。

用法：python check_stage10.py
"""
import pathlib

import agents
import main as cli
import pipeline
import report
from agents import Verdict
from plan_review import MAX_PLAN_REVISIONS, _strip_bullet, human_review, review_plan

FAILED = []

def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<34} {extra}")
    if not ok:
        FAILED.append(name)

CFG = type("CFG", (), {"max_concurrency": 2, "max_pages": 5})()

def QUIET(*args, **kwargs):
    pass

def fake_ask(answers):
    """把一串预设回答当键盘输入。用完了返回空串（等价于一直按回车）。"""
    queue = list(answers)

    def ask(prompt=""):
        return queue.pop(0) if queue else ""

    return ask

def fake_planner(plans):
    """第 n 次调用返回第 n 份提纲，用来验证"打回之后真的重拆了"。"""
    calls = []

    def planner(question, *, cfg=None):
        calls.append(question)
        return list(plans[min(len(calls), len(plans)) - 1])

    planner.calls = calls
    return planner

# ---------------- 1. 接受
p = fake_planner([["甲", "乙"]])
r = review_plan("Q", CFG, fake_ask([""]), planner=p, out=QUIET)
check("回车 = 接受原提纲", r == ["甲", "乙"], str(r))
check("接受时只拆一次", len(p.calls) == 1, str(len(p.calls)))

# ---------------- 2. 打回一次后重拆
p = fake_planner([["甲"], ["丙", "丁"]])
r = review_plan("Q", CFG, fake_ask(["n", ""]), planner=p, out=QUIET)
check("打回后用的是重拆结果", r == ["丙", "丁"], str(r))
check("重拆真的调了第二次", len(p.calls) == 2, str(len(p.calls)))

# ---------------- 3. 一直打回 -> 到上限强制放行
p = fake_planner([["甲"], ["乙"], ["丙"], ["丁"], ["戊"]])
r = review_plan("Q", CFG, fake_ask(["n"] * 10), planner=p, out=QUIET)
check("打回超上限不抛异常", isinstance(r, list), str(r))
check("最多重拆 MAX_PLAN_REVISIONS 次",
      len(p.calls) == 1 + MAX_PLAN_REVISIONS, str(len(p.calls)))
check("强制放行用的是最后一版", r == ["丁"], str(r))

# ---------------- 4. e 进入手改模式
p = fake_planner([["甲"]])
r = review_plan("Q", CFG, fake_ask(["e", "1. 新问题一", "- 新问题二", ""]), planner=p, out=QUIET)
check("手改模式收下两条", r == ["新问题一", "新问题二"], str(r))
check("手改不再调 planner", len(p.calls) == 1, str(len(p.calls)))

# ---------------- 5. 直接把一条子问题粘进来
p = fake_planner([["甲"]])
r = review_plan("Q", CFG, fake_ask(["我自己的问题", ""]), planner=p, out=QUIET)
check("非关键词输入按手改处理", r == ["我自己的问题"], str(r))

# ---------------- 6. 说了要改却什么都没输入
p = fake_planner([["甲"]])
r = review_plan("Q", CFG, fake_ask(["e", ""]), planner=p, out=QUIET)
check("空手改 = 接受原提纲", r == ["甲"], str(r))

# ---------------- 7. 没接人审
p = fake_planner([["甲", "乙"]])
r = review_plan("Q", CFG, None, planner=p, out=QUIET)
check("ask=None 直接用自动拆解", r == ["甲", "乙"], str(r))

# ---------------- 8. 关键词与序号解析
check("y 是接受", human_review(["甲"], fake_ask(["y"]), out=QUIET)[1] == "accept")
check("NO 大小写无关", human_review(["甲"], fake_ask(["NO"]), out=QUIET)[1] == "revise")
check("非关键词不误判成接受",
      human_review(["甲"], fake_ask(["随便说点什么"]), out=QUIET)[1] == "edit")
check("esc 掉的空输入=接受", human_review(["甲"], fake_ask([None]), out=QUIET)[1] == "accept")
for raw, want in (("1. abc", "abc"), ("2、中文项", "中文项"), ("- x", "x"),
                  ("10) y", "y"), ("3）全角括号", "全角括号"), ("没有序号", "没有序号")):
    check(f"剥序号 {raw!r}", _strip_bullet(raw) == want, _strip_bullet(raw))

# ---------------- 8b. stdin 已关闭
def eof_ask(prompt=""):
    raise EOFError

p = fake_planner([["甲"]])
r = review_plan("Q", CFG, eof_ask, planner=p, out=QUIET)
check("stdin 关闭时不崩", r == ["甲"], str(r))

# ---------------- 9. 和编排器接线
COLLECTED = []

def fake_collect(targets, cfg=None):
    COLLECTED.append(list(targets))
    return [{"title": "T", "url": "https://a.com/1", "raw_content": "正文" * 20}]

def reset():
    COLLECTED.clear()
    pipeline.plan_research = lambda q, *, cfg=None: ["自动甲", "自动乙"]
    pipeline._collect_pages = fake_collect
    agents.render_evidence = lambda state, cfg: "[1] T\nURL: https://a.com/1\n正文: 内容"
    agents.write_draft = lambda state, cfg, feedback: "# 草稿\n"
    agents.finalize_report = lambda state, cfg: state.draft + "\n## 结论\n\n假结论\n"
    agents.review_draft = lambda state, cfg: Verdict(True, 9, [], [])
    report.save_report = lambda q, rep, **kw: pathlib.Path("outputs/fake.md")

reset()
r = agents.run_editorial("Q", cfg=CFG, max_rounds=2, ask=fake_ask(["e", "人改问题", ""]))
check("人改的提纲进了研究者", COLLECTED[0] == ["人改问题"], str(COLLECTED))
check("人审后仍然正常出报告", bool(r["path"]), str(r["path"]))

reset()
r = agents.run_editorial("Q", cfg=CFG, max_rounds=2)
check("不开人审：走自动拆解", COLLECTED[0] == ["自动甲", "自动乙"], str(COLLECTED))

# ---------------- 10. 和命令行接线
args = cli.build_parser().parse_args(["agents", "Q", "--human"])
check("--human 已接线", args.human is True, str(getattr(args, "human", None)))
args = cli.build_parser().parse_args(["agents", "Q"])
check("默认不接人审", args.human is False, str(getattr(args, "human", None)))

print()
print("全部通过" if not FAILED else f"{len(FAILED)} 项失败：{FAILED}")
raise SystemExit(1 if FAILED else 0)