"""阶段 16 离线自检：技能层 + 编排门面。全程打桩，不联网、不调模型。

用法：python check_stage16.py   （放根目录或 checks/ 里都能跑）

五组：
  1. 装配 —— 门面默认就装好 5 个技能，也能被替换；
  2. 事件流 —— 顺序、payload、回调异常隔离；
  3. 失败语义 —— 无页面 / 空上下文 要停下并说清；
  4. 来源管家 —— 开/关/失败降级三条路；
  5. 等价性 —— 新门面与旧的 pipeline.full_research 产出一致。
"""
import logging
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent
while not (_ROOT / "researcher.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))

logging.disable(logging.WARNING)

import agents
import context
import curator
import pipeline
import report
from researcher import Researcher
from skills import (
    CompressorSkill,
    PlannerSkill,
    ReviewerSkill,
    SearcherSkill,
    WriterSkill,
)
from skills.base import RunContext

FAILED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<40} {extra}")
    if not ok:
        FAILED.append(name)


CFG = type("CFG", (), {"curate_sources": False})()
CTX = RunContext(cfg=CFG)

PAGES = [{"title": "T", "url": "https://a.test/1", "raw_content": "正文" * 30}]
CONTEXT = "[1] T\nURL: https://a.test/1\n正文: 内容\n"
SOURCES = [{"n": 1, "title": "T", "url": "https://a.test/1"}]
CALLS = []
FLAGS = {"pages": list(PAGES), "context": CONTEXT, "curate_ok": True}


def fake_plan(query, *, cfg=None, expert=None):
    CALLS.append(("plan", query))
    return ["甲", "乙"]


def fake_collect(subs, cfg):
    CALLS.append(("collect", list(subs)))
    return list(FLAGS["pages"])


def fake_select(q, pages, *, cfg=None):
    CALLS.append(("compress", len(pages)))
    return FLAGS["context"]


def fake_parse(text):
    return list(SOURCES)


def fake_write_report(q, ctx_text, sources, *, cfg=None):
    CALLS.append(("write", len(sources)))
    return "# 报告\n\n引用 [1]。"


def fake_save(q, text, **kw):
    CALLS.append(("save",))
    return pathlib.Path("fake.md")


def fake_cost(cfg):
    return "COST"


def fake_curate(question, pages, *, cfg=None):
    CALLS.append(("curate", len(pages)))
    if not FLAGS["curate_ok"]:
        return {"ok": False, "pages": list(pages), "kept": [], "dropped": [], "reason": "模型挂了"}
    return {"ok": True, "pages": pages[:1], "kept": [], "dropped": [], "reason": ""}


def install():
    CALLS.clear()
    pipeline.plan_research = fake_plan
    pipeline._collect_pages = fake_collect
    pipeline._cost_report_hook = fake_cost
    context.select_context = fake_select
    report.parse_context_sources = fake_parse
    report.write_report = fake_write_report
    report.save_report = fake_save
    curator.curate_pages = fake_curate


install()

# ---------------------------------------------------------------- 1. 装配
print("---- 1. 装配 ----")

r = Researcher(cfg=CFG)
check("默认装配 PlannerSkill", isinstance(r.planner, PlannerSkill))
check("默认装配 SearcherSkill", isinstance(r.searcher, SearcherSkill))
check("默认装配 CompressorSkill", isinstance(r.compressor, CompressorSkill))
check("默认装配 WriterSkill", isinstance(r.writer, WriterSkill))
check("默认装配 ReviewerSkill", isinstance(r.reviewer, ReviewerSkill))
check("技能都有名字", [s.name for s in (r.planner, r.searcher, r.compressor, r.writer, r.reviewer)]
      == ["planner", "searcher", "compressor", "writer", "reviewer"])
check("技能共享同一个 RunContext", r.planner.ctx is r.ctx and r.writer.ctx is r.ctx)

standalone = PlannerSkill(RunContext(cfg=CFG))
check("技能可脱离门面单独用", standalone.plan("问题") == ["甲", "乙"])

class FakeWriter:
    def write(self, q, c, s):
        return "# 注入的"

    def save(self, q, t):
        return pathlib.Path("inj.md")


res_inj = Researcher(cfg=CFG, writer=FakeWriter()).run("Q")
check("writer 可注入替换", res_inj["report"] == "# 注入的", res_inj["report"])
check("注入的 writer 参与了落盘", res_inj["path"] == "inj.md")

# ---------------------------------------------------------------- 2. 事件流
print("---- 2. 事件流 ----")

install()
EVENTS = []
r_ev = Researcher(cfg=CFG, on_event=lambda e, p: EVENTS.append(e))
res_ev = r_ev.run("Q")
check("事件顺序正确", EVENTS == [
    "run.start", "plan.start", "plan.done", "search.start", "search.done",
    "compress.start", "compress.done", "write.start", "write.done",
    "save.done", "run.done",
], str(EVENTS))

by_name = {e["event"]: e for e in r_ev.ctx.events}
check("plan.done 带子问题数", by_name["plan.done"]["count"] == 2)
check("search.done 带页数", by_name["search.done"]["pages"] == 1)
check("compress.done 带上下文字符数", by_name["compress.done"]["context_chars"] == len(CONTEXT))
check("write.done 带报告字数", by_name["write.done"]["chars"] > 0)
check("run.done 带落盘路径", by_name["run.done"]["path"] == "fake.md")
check("事件全量留痕", len(r_ev.ctx.events) == 11)

install()


def boom(event, payload):
    raise RuntimeError("回调炸了")


res_boom = Researcher(cfg=CFG, on_event=boom).run("Q")
check("回调异常不影响主流程", res_boom["path"] == "fake.md")

# ---------------------------------------------------------------- 3. 失败语义
print("---- 3. 失败语义 ----")

install()
FLAGS["pages"] = []
r_np = Researcher(cfg=CFG)
res_np = r_np.run("Q")
check("无页面 -> 明确说没抓到", "没有为" in res_np["report"], res_np["report"])
check("无页面 -> path=None", res_np["path"] is None)
check("无页面 -> 不写不存", not any(c[0] in ("write", "save") for c in CALLS))
check("无页面 -> 事件为 run.stop", r_np.ctx.events[-1] == {"event": "run.stop", "reason": "no_pages"})
check("无页面 -> 返回结构完整",
      set(res_np) == {"report", "sources", "cost_report", "path", "sub_queries", "curation"})

install()
FLAGS["pages"] = list(PAGES)
FLAGS["context"] = "   "
r_ec = Researcher(cfg=CFG)
res_ec = r_ec.run("Q")
check("空上下文 -> 明确说没上下文", "没有为" in res_ec["report"])
check("空上下文 -> 事件 reason=empty_context", r_ec.ctx.events[-1]["reason"] == "empty_context")
check("空上下文 -> 仍带回原始页面", res_ec["sources"] == PAGES)

# ---------------------------------------------------------------- 4. 管家
print("---- 4. 来源管家 ----")

install()
FLAGS["context"] = CONTEXT
FLAGS["curate_ok"] = True
Researcher(cfg=CFG).run("Q", curate=True)
check("curate=True 调一次管家", [c for c in CALLS if c[0] == "curate"] == [("curate", 1)])

install()
Researcher(cfg=CFG).run("Q", curate=False)
check("curate=False 一次都不调", not any(c[0] == "curate" for c in CALLS))

install()
FLAGS["curate_ok"] = False
r_cf = Researcher(cfg=CFG)
res_cf = r_cf.run("Q", curate=True)
check("管家失败 -> 降级继续出报告", res_cf["path"] == "fake.md")
check("管家失败 -> curation 里留原因", res_cf["curation"]["reason"] == "模型挂了")
check("管家失败 -> 事件有 curate.fallback",
      any(e["event"] == "curate.fallback" for e in r_cf.ctx.events))

FLAGS["curate_ok"] = True
install()

# ---------------------------------------------------------------- 5. 等价性
print("---- 5. 与旧链路等价 ----")

install()
old = pipeline.full_research("Q", cfg=CFG)
install()
new = Researcher(cfg=CFG).run("Q")

check("键集合一致", set(old) == set(new), str(set(old) ^ set(new)))
check("报告一致", old["report"] == new["report"])
check("子问题一致", old["sub_queries"] == new["sub_queries"])
check("落盘路径一致", old["path"] == new["path"])
check("来源一致", old["sources"] == new["sources"])
check("成本串一致", old["cost_report"] == new["cost_report"])
check("curation 一致", old["curation"] == new["curation"])

FLAGS["pages"] = []
install()
old_np = pipeline.full_research("Q", cfg=CFG)
new_np = Researcher(cfg=CFG).run("Q")
check("无页面分支也等价",
      old_np["report"] == new_np["report"] and old_np["path"] == new_np["path"])

# ---------------------------------------------------------------- 6. editorial
print("---- 6. 编辑部入口 ----")

agents.run_editorial = lambda q, **kw: {"report": "E", "path": None, "rounds": 0}
res_ed = Researcher(cfg=CFG).editorial("Q")
check("editorial 委托旧编排", res_ed["report"] == "E")

print()
if FAILED:
    print(f"{len(FAILED)} 项失败: {FAILED}")
    sys.exit(1)
print("阶段 16 全部通过")