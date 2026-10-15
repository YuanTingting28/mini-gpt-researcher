"""阶段13 离线自检：来源管家（curator）。全程打桩，不联网。

用法：python check_stage13.py      （放在项目根目录或 checks/ 里都能跑）
"""
import logging
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parent
while not (_ROOT / "curator.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))

# pipeline 顶层 import 了 search（真检索器要联网）。管家这里只借它的 parse_json，
# 所以离线自检塞一个空壳进去，别真去建检索器。
if "search" not in sys.modules:
    _fake = types.ModuleType("search")
    _fake.get_retriever = lambda *a, **k: None
    sys.modules["search"] = _fake

from curator import _catalog, _normalize_keep, _parse_drop_reasons, curate_pages
from llm import chat

# 断言本身覆盖了每条失败分支，这里把 INFO/WARNING 压掉，自检输出才看得清。
logging.disable(logging.WARNING)

FAILED = []
SKIPPED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<40} {extra}")
    if not ok:
        FAILED.append(name)


def skip(name, why=""):
    print(f"SKIP {name:<40} {why}")
    SKIPPED.append(name)


CFG = type("CFG", (), {"curate_max_results": 10, "curate_snippet_chars": 600})()


def page(title, url, body):
    return {"title": title, "url": url, "raw_content": body}


PAGES = [
    page("官方文档 A", "https://a.test/doc", "A" * 794 + "TAIL_A"),   # 正好 800 字符
    page("转载博客 B", "https://b.test/x", "B" * 50),
    page("论文 C", "https://c.test/paper", "C" * 2000),
    page("商品页 D", "https://d.test/buy", "D" * 30),
]

CALLS = []


def make_chat(reply=None, boom=None):
    """造一个打桩的 chat：记下每次调用，返回固定回复或抛固定异常。"""
    def _chat(messages, **kw):
        CALLS.append({"messages": messages, "kw": kw})
        if boom is not None:
            raise boom
        return reply
    return _chat


def run(reply=None, boom=None, pages=None, **kw):
    CALLS.clear()
    return curate_pages(
        "对比 A 和 B", pages if pages is not None else PAGES,
        cfg=cfg_or(CFG, kw.pop("cfg", None)),
        chat_fn=make_chat(reply, boom), **kw,
    )


def cfg_or(base, override):
    return override if override is not None else base


# ---------------- 1. 目录怎么编
cat = _catalog(PAGES, 600)
check("目录按传入顺序给编号", "[1] 官方文档 A" in cat and "[4] 商品页 D" in cat)
check("目录写了每篇的长度", "长度: 800 字符" in cat and "长度: 2000 字符" in cat)
check("目录只给开头，不给全文", "TAIL_A" not in cat, f"len={len(cat)}")
check("目录截断长度可配", len(_catalog(PAGES, 10)) < len(cat))
check("目录里序号是 1 基", "\n[1] " not in cat and cat.startswith("[1] "))

# ---------------- 2. keep 洗数据
check("编号越界被挡", _normalize_keep([1, 9, -1, 0], 4) == [1])
check("重复编号去重且保序", _normalize_keep([3, 1, 3, 1], 4) == [3, 1])
check("bool 不算编号", _normalize_keep([True, False, 2], 4) == [2])
check("非整数被挡", _normalize_keep(["2", 2.0, None, 2], 4) == [2])
check("不是列表 -> 空", _normalize_keep("2", 4) == [] and _normalize_keep(None, 4) == [])

check("drop 理由解析", _parse_drop_reasons(["3: 与任务无关", "4：与 1 重复"])
      == {3: "与任务无关", 4: "与 1 重复"})
check("drop 理由非列表 -> 空", _parse_drop_reasons(None) == {})

# ---------------- 3. 正常路径
r = run(reply='{"keep": [3, 1], "dropped": ["2: 内容过短", "4: 商品页"]}')
check("正常排序：按 keep 顺序取页", [p["url"] for p in r["pages"]] == ["https://c.test/paper", "https://a.test/doc"],
      str([p["url"] for p in r["pages"]]))
check("正常路径 ok=True", r["ok"] is True, r["reason"])
check("kept 记的是原顺序编号", [k["n"] for k in r["kept"]] == [3, 1])
check("dropped 是被剔掉的那些", [d["n"] for d in r["dropped"]] == [2, 4])
check("dropped 带上了理由", r["dropped"][0]["why"] == "内容过短", str(r["dropped"][0]))
check("kept + dropped 数量守恒", len(r["kept"]) + len(r["dropped"]) == len(PAGES))
check("页面对象没被就地修改", PAGES[0]["title"] == "官方文档 A")

# ---------------- 4. 呼叫参数
called = CALLS[0]
check("只调一次模型", len(CALLS) == 1, str(len(CALLS)))
check("温度 0（排序要可复现）", called["kw"].get("temperature") == 0.0)
check("走 JSON 模式", called["kw"].get("json_mode") is True)
check("不开推理（NO_THINKING）", called["kw"].get("extra_body") == {"reasoning_effort": "none"},
      str(called["kw"].get("extra_body")))
check("max_tokens 给得小（只回编号）", called["kw"].get("max_tokens") == 1000)
check("prompt 里写了保留上限", "最多 10 条" in called["messages"][0]["content"])

# ---------------- 5. 上限截断
r = run(reply='{"keep": [4, 3, 2, 1]}', max_results=2)
check("保留数被 max_results 截断", [k["n"] for k in r["kept"]] == [4, 3], str(r["kept"]))

# ---------------- 6. 部分越界
r = run(reply='{"keep": [9, 2]}')
check("混进越界编号：留下合法的", [k["n"] for k in r["kept"]] == [2], str(r["kept"]))
check("混进越界编号：仍算成功", r["ok"] is True)

# ---------------- 7. 失败一律放行（fail-open）
r = run(reply='{"keep": []}')
check("keep 为空 -> 原样放行", r["pages"] == PAGES and r["ok"] is False, r["reason"])
check("keep 为空 -> 原因说清", "编号" in r["reason"], r["reason"])

r = run(reply='{"keep": [7, 8, 9]}')
check("keep 全越界 -> 原样放行", r["pages"] == PAGES and r["ok"] is False, r["reason"])

r = run(reply="抱歉，我无法完成这个请求。")
check("JSON 解析不出 -> 原样放行", r["pages"] == PAGES and r["ok"] is False, r["reason"])

r = run(reply='{"sources": [1, 2]}')
check("缺 keep 键 -> 原样放行", r["pages"] == PAGES and r["ok"] is False, r["reason"])

r = run(boom=RuntimeError("网络断了"))
check("模型调用异常 -> 原样放行", r["pages"] == PAGES and r["ok"] is False)
check("异常信息进 reason", "网络断了" in r["reason"], r["reason"])

check("失败时 kept 覆盖全部页面", [k["n"] for k in run(reply="x")["kept"]] == [1, 2, 3, 4])
check("失败时 dropped 为空", run(reply="x")["dropped"] == [])

# ---------------- 8. 单篇不调模型
one = [page("唯一来源", "https://only.test/x", "Z" * 500)]
r = run(reply='{"keep": [1]}', pages=one)
check("只有一篇 -> 不调模型", len(CALLS) == 0, str(len(CALLS)))
check("只有一篇 -> 原样通过", r["pages"] == one and r["ok"] is True, r["reason"])
check("只有一篇 -> 原因说明跳过", "跳过" in r["reason"], r["reason"])

# ---------------- 9. 模型说全都要
r = run(reply='{"keep": [2, 1, 3, 4], "dropped": []}')
check("要求全留：顺序仍按模型给的", [k["n"] for k in r["kept"]] == [2, 1, 3, 4])
check("要求全留：没有丢弃项", r["dropped"] == [])

# ---------------- 10. 接线：full_research 真的把筛过的页面交给了压缩
WIRE_PAGES = [
    page("甲", "https://a.test/1", "A" * 700),
    page("乙", "https://b.test/2", "B" * 700),
    page("丙", "https://c.test/3", "C" * 700),
]
WIRE_CFG = type("CFG", (), {"curate_sources": False, "curate_max_results": 10,
                            "curate_snippet_chars": 600})()
_seen = {}
_calls = []
_saved = None

try:
    import context as context_mod
    import pipeline as pipeline_mod
    import report as report_mod

    _saved = (
        pipeline_mod.plan_research, pipeline_mod._collect_pages,
        pipeline_mod._cost_report_hook, context_mod.select_context,
        report_mod.write_report, report_mod.save_report, chat,
    )

    def _fake_select(query, pages, *, cfg=None):
        _seen["pages"] = pages
        return "[1] 占位\nURL: https://x\n正文: body\n"

    pipeline_mod.plan_research = lambda q, cfg=None: ["s1"]
    pipeline_mod._collect_pages = lambda subs, cfg=None: list(WIRE_PAGES)
    pipeline_mod._cost_report_hook = lambda cfg: ""
    context_mod.select_context = _fake_select
    report_mod.write_report = lambda q, c, s, cfg=None: "报告"
    report_mod.save_report = lambda q, r: pathlib.Path("outputs/fake.md")

    def _chat(messages, **kw):
        _calls.append(kw)
        return '{"keep": [3]}'

    import curator as curator_mod
    curator_mod.chat = _chat

    pipeline_mod.full_research("q", cfg=WIRE_CFG, curate=True)
    check("接线：curate=True 只把筛过的页交给压缩",
          [p["url"] for p in _seen["pages"]] == ["https://c.test/3"],
          str([p["url"] for p in _seen["pages"]]))
    check("接线：curate=True 调了一次模型", len(_calls) == 1, str(len(_calls)))

    _seen.clear()
    _calls.clear()
    result = pipeline_mod.full_research("q", cfg=WIRE_CFG, curate=False)
    check("接线：curate=False 把全部页交给压缩",
          len(_seen["pages"]) == len(WIRE_PAGES), str(len(_seen["pages"])))
    check("接线：curate=False 一次模型都不调", _calls == [], str(len(_calls)))
    check("接线：curate=False 时 curation 为 None", result["curation"] is None)
finally:
    if _saved is not None:
        (pipeline_mod.plan_research, pipeline_mod._collect_pages,
         pipeline_mod._cost_report_hook, context_mod.select_context,
         report_mod.write_report, report_mod.save_report, curator_mod.chat) = _saved

print()
if SKIPPED:
    print(f"（{len(SKIPPED)} 组跳过：{SKIPPED}）")
print("全部通过" if not FAILED else f"{len(FAILED)} 项失败：{FAILED}")
raise SystemExit(1 if FAILED else 0)