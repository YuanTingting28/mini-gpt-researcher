"""阶段14 离线自检：工具层 + MCP 协议层。

用法：python check_stage14.py      （放在项目根目录或 checks/ 里都能跑）

两组：
  第 1 组 工具层（research_tools.py）—— 打桩，不需要装 mcp，永远跑；
  第 2 组 协议层（mcp_server.py）  —— 真要起一个子进程走 stdio JSON-RPC，
                                        需要 pip install mcp，没装就整组 SKIP。

第 2 组里有一条是**红线**：断言 server 的 stdout 上每一行都是合法 JSON-RPC。
stdio 传输下 stdout 就是协议通道，一句 print 就能把流冲坏，而且客户端不报错，
只表现为"服务没反应"。这条挂了，其它功能全都不用看了。
"""
import json
import logging
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import threading
import time

_ROOT = pathlib.Path(__file__).resolve().parent
while not (_ROOT / "research_tools.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))

logging.disable(logging.WARNING)

FAILED = []
SKIPPED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<38} {extra}")
    if not ok:
        FAILED.append(name)


def skip(name, why=""):
    print(f"SKIP {name:<38} {why}")
    SKIPPED.append(name)


# ============================================================ 第 1 组：工具层
import research_tools

import context as context_mod
import pipeline as pipeline_mod
import scrape as scrape_mod
import search as search_mod

CALLED = {}


class FakeRetriever:
    def __init__(self, results):
        self.results = results

    def search(self, n):
        CALLED["search_n"] = n
        return self.results[:n]


def stub_search(results):
    search_mod.get_retriever = lambda name, q: FakeRetriever(results)


def page(title, url, body):
    return {"title": title, "url": url, "raw_content": body}


def reset():
    CALLED.clear()
    # 默认把"抓取"设成一个会炸的桩：quick_search 敢碰它就该被抓住
    scrape_mod.fetch = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("quick_search 不该抓正文"))


# ---------------- 1. quick_search
reset()
stub_search([{"title": f"结果{i}", "url": f"https://x.test/{i}",
              "body": "摘要" * 200} for i in range(1, 6)])
out = research_tools.quick_search(
    "查询", max_results=3, cfg=type("C", (), {"retriever": "fake"})())
check("quick_search 带 [n] 编号", "[1] 结果1" in out and "[3] 结果3" in out)
check("quick_search 只取 max_results 条", "[4]" not in out, f"行数={out.count(chr(10)) // 3 + 1}")
check("quick_search 摘要有上限",
      max(len(l.strip()) for l in out.splitlines()) <= research_tools.MAX_SNIPPET_CHARS + 12,
      str(max(len(l.strip()) for l in out.splitlines())))
check("quick_search 不抓正文", True)   # 桩会抛，能跑到这行就说明没碰

reset()
stub_search([])
out = research_tools.quick_search("没人知道的词", cfg=type("C", (), {"retriever": "fake"})())
check("quick_search 空结果 -> 明确说没有", "没有" in out and "检索" in out, out[:40])
check("quick_search 空结果不装成功", "[1]" not in out)

# ---------------- 2. research_sources
reset()
stub_search([])
pipeline_mod.plan_research = lambda q, cfg=None: ["子问题甲", "子问题乙"]
pipeline_mod._collect_pages = lambda subs, cfg=None: [
    page("甲页", "https://a.test/1", "A" * 500),
    page("乙页", "https://b.test/2", "B" * 500),
    page("丙页", "https://c.test/3", "C" * 500),
]
context_mod.select_context = lambda q, pages, cfg=None: "[1] 甲页\nURL: u\n正文: 证据正文\n"
out = research_tools.research_sources("查询", cfg=object())
check("research_sources 头部写了子问题", "子问题甲" in out and "子问题乙" in out)
check("research_sources 头部写了抓了几页", "抓取 3 页" in out, out.splitlines()[2] if out else "")
check("research_sources 把证据原样交出去", "证据正文" in out)

out = research_tools.research_sources("查询", max_pages=2, cfg=object())
check("research_sources 按 max_pages 截断", "抓取 2 页" in out, out.splitlines()[2])

pipeline_mod._collect_pages = lambda subs, cfg=None: []
out = research_tools.research_sources("查询", cfg=object())
check("research_sources 一页没抓到 -> 明确说", "没有" in out and "证据" in out, out[:40])

pipeline_mod._collect_pages = lambda subs, cfg=None: [page("甲", "u", "x" * 500)]
context_mod.select_context = lambda q, pages, cfg=None: ""
out = research_tools.research_sources("查询", cfg=object())
check("research_sources 压缩后为空 -> 明确说", "没有留下可用证据" in out, out[:50])

# ---------------- 3. deep_research
reset()
pipeline_mod.full_research = lambda q, cfg=None: {
    "report": "# 报告正文",
    "sources": [{"n": 1, "title": "甲页", "url": "https://a.test/1"}],
    "path": "outputs/fake.md",
}
out = research_tools.deep_research("查询", cfg=object())
check("deep_research 交回报告", "# 报告正文" in out)
check("deep_research 附了来源清单", "[1] 甲页" in out and "https://a.test/1" in out)
check("deep_research 说了落盘路径", "outputs/fake.md" in out)

pipeline_mod.full_research = lambda q, cfg=None: {
    "report": '没有为"查询"抓到任何可用资料', "sources": [], "path": None,
}
out = research_tools.deep_research("查询", cfg=object())
check("deep_research 没落盘 -> 明说没产出", "没有产出报告" in out, out[:40])
check("deep_research 没落盘 -> 不编来源清单", "## 来源" not in out)

# ---------------- 4. 工具不许写 stdout
import io
real_stdout = sys.stdout
buf = io.StringIO()
try:
    sys.stdout = buf
    research_tools.quick_search("查询", cfg=type("C", (), {"retriever": "fake"})())
finally:
    sys.stdout = real_stdout
check("工具不往 stdout 写东西", buf.getvalue() == "", repr(buf.getvalue()[:60]))

print()

# ============================================================ 第 2 组：协议层
try:
    import mcp  # noqa: F401
    has_mcp = True
except ImportError:
    has_mcp = False

if not has_mcp:
    skip("MCP 协议层整组", "未安装 mcp：pip install mcp")
else:
    SERVER = str(_ROOT / "mcp_server.py")

    class Stdio:
        """把 server 当子进程拉起来，按 newline-delimited JSON-RPC 说话。"""

        def __init__(self):
            env = dict(os.environ)
            # 关键：不设这个，子进程的 stdout 是块缓冲，写出来的响应会卡在缓冲区里，
            # 我们这边就"等不到回复"—— 而 server 其实早就答完了。MCP over stdio
            # 的调试有一半时间浪费在这个上面。
            env["PYTHONUNBUFFERED"] = "1"
            self.proc = subprocess.Popen(
                [sys.executable, SERVER], cwd=str(_ROOT), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1,
            )
            self.out = queue.Queue()
            self.err = queue.Queue()
            self.noise = []
            self._id = 0
            for stream, sink in ((self.proc.stdout, self.out), (self.proc.stderr, self.err)):
                threading.Thread(target=self._pump, args=(stream, sink), daemon=True).start()

        @staticmethod
        def _pump(stream, sink):
            try:
                for line in stream:
                    sink.put(line)
            except Exception:
                pass
            finally:
                sink.put(None)

        def _send(self, obj):
            self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

        def notify(self, method, params=None):
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            self._send(msg)

        def call(self, method, params=None, timeout=45):
            self._id += 1
            msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
            if params is not None:
                msg["params"] = params
            self._send(msg)
            return self.await_id(self._id, timeout)

        def await_id(self, rid, timeout):
            deadline = time.time() + timeout
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    return None, f"等 id={rid} 超时（{timeout}s）"
                try:
                    line = self.out.get(timeout=remain)
                except queue.Empty:
                    return None, f"等 id={rid} 超时（{timeout}s）"
                if line is None:
                    return None, "server 提前关掉了 stdout"
                text = line.strip()
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    self.noise.append(text)
                    continue
                if obj.get("id") == rid:
                    return obj, ""
                # 没 id 的是通知，丢掉

        def stderr_tail(self, n=8):
            lines = []
            while not self.err.empty():
                item = self.err.get()
                if item:
                    lines.append(item.rstrip())
            return " | ".join(lines[-n:])

        def close(self):
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    srv = Stdio()
    try:
        resp, err = srv.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "check_stage14", "version": "0"},
        })
        check("initialize 有响应", resp is not None, err or srv.stderr_tail())
        if resp:
            info = (resp.get("result") or {}).get("serverInfo") or {}
            check("serverInfo 名字对", info.get("name") == "gpt-researcher", str(info))
        srv.notify("notifications/initialized")

        resp, err = srv.call("tools/list")
        tools = ((resp or {}).get("result") or {}).get("tools") or []
        names = sorted(t.get("name") for t in tools)
        check("tools/list 有响应", resp is not None, err)
        check("三个工具都注册上了",
              names == ["deep_research", "quick_search", "research_sources"], str(names))
        check("每个工具都带 inputSchema",
              all(isinstance(t.get("inputSchema"), dict) for t in tools),
              str([t.get("name") for t in tools if not isinstance(t.get("inputSchema"), dict)]))
        check("每个工具都带描述（宿主靠它决定用哪个）",
              all((t.get("description") or "").strip() for t in tools),
              str([t.get("name") for t in tools if not (t.get("description") or "").strip()]))
        check("工具参数名是 query",
              all("query" in (t.get("inputSchema") or {}).get("properties", {}) for t in tools))

        resp, err = srv.call("prompts/list")
        prompts = ((resp or {}).get("result") or {}).get("prompts") or []
        check("prompts/list 里有 research_query",
              any(p.get("name") == "research_query" for p in prompts), str(prompts)[:60])

        resp, err = srv.call("tools/call", {"name": "不存在的工具", "arguments": {}})
        # 注意：MCP 里"工具自己失败了"和"协议层出错"是两回事。
        # 协议层出错才是 JSON-RPC 的 error；工具内部失败走正常响应 +
        # CallToolResult.isError=True —— mcp 1.29 把"工具名不存在"归到后者
        # （它在 stderr 里只留一句 WARNING）。所以两种都要认。
        result = (resp or {}).get("result") or {}
        failed = ("error" in (resp or {})) or result.get("isError") is True
        check("调不存在的工具 -> 明确报失败而不是静默成功", failed, str(resp)[:140])

        # ---- 红线 ----
        check("【红线】stdout 上只有 JSON-RPC", srv.noise == [], repr(srv.noise[:1]))
        check("【红线】server 全程没死", srv.proc.poll() is None,
              f"退出码={srv.proc.poll()} stderr={srv.stderr_tail()}")
    finally:
        srv.close()

print()
if SKIPPED:
    print(f"（{len(SKIPPED)} 组跳过：{SKIPPED}）")
print("全部通过" if not FAILED else f"{len(FAILED)} 项失败：{FAILED}")
raise SystemExit(1 if FAILED else 0)