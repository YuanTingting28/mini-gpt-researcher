"""阶段 17 离线自检：arXiv 检索器 + 多源并发合并。全程打桩，不联网。

用法：python check_stage17.py   （放根目录或 checks/ 里都能跑）

四组：
  1. arXiv 解析与降级 —— XML 千奇百怪，一条脏数据不能带走整批；
  2. 多源合并 —— 顺序、去重、单源失败隔离、真并发；
  3. 配置 —— RETRIEVERS 解析与回退；
  4. 接线 —— 注册表能造实例，_collect_pages 的全局去重仍然生效。
"""
import logging
import pathlib
import sys
import time

_ROOT = pathlib.Path(__file__).resolve().parent
while not (_ROOT / "search" / "arxiv.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))

logging.disable(logging.WARNING)

import requests

import config
import pipeline
from search import get_retriever
from search import arxiv as arxiv_mod

FAILED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<40} {extra}")
    if not ok:
        FAILED.append(name)


CFG = type("CFG", (), {"request_timeout": 5, "max_search_results": 5})()

ATOM_OK = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Differential Transformer</title>
    <summary>We propose a differential attention mechanism that cancels noise.</summary>
    <link href="http://arxiv.org/abs/2401.00001v1" rel="alternate"/>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00001v1" rel="related"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <title>No PDF Here</title>
    <summary>Second paper.</summary>
    <link href="http://arxiv.org/abs/2401.00002v1" rel="alternate"/>
  </entry>
</feed>
"""

REAL_GET = requests.get
CALLS = []


class FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


def stub_get(text=ATOM_OK, status=200, boom=None):
    def _get(url, params=None, timeout=None):
        CALLS.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        if boom is not None:
            raise boom
        return FakeResp(text, status)

    arxiv_mod.requests.get = _get


# ---------------------------------------------------------------- 1. arXiv
print("---- 1. arXiv 检索器 ----")

CALLS.clear()
stub_get()
r = arxiv_mod.ArxivRetriever("differential transformer", cfg=CFG)
hits = r.search(max_results=5)
check("正常解析出 2 条", len(hits) == 2, str(len(hits)))
check("标题被清洗", hits[0]["title"] == "Differential Transformer")
check("摘要压成单行", "\n" not in hits[0]["body"])
check("href 优先用 pdf", hits[0]["href"] == "http://arxiv.org/pdf/2401.00001v1")
check("没有 pdf 时退回 abs 页", hits[1]["href"] == "http://arxiv.org/abs/2401.00002v1")
check("requires_scraping=True", r.requires_scraping is True)
check("请求参数是 relevance 排序", CALLS[0]["params"]["sortBy"] == "relevance")
check("查询包成 all:\"...\"", CALLS[0]["params"]["search_query"].startswith('all:"'))
check("max_results 透传", CALLS[0]["params"]["max_results"] == 5)
check("超时用 cfg.request_timeout", CALLS[0]["timeout"] == 5)

check("畸形 XML -> []", arxiv_mod.ArxivRetriever._normalize("<not-xml", 5) == [])
check("max_results 截断生效", len(arxiv_mod.ArxivRetriever._normalize(ATOM_OK, 1)) == 1)

CALLS.clear()
stub_get(status=500)
check("HTTP 500 -> []", arxiv_mod.ArxivRetriever("hello world", cfg=CFG).search(5) == [])

CALLS.clear()
stub_get(boom=requests.ConnectionError("网络断了"))
check("网络异常 -> []", arxiv_mod.ArxivRetriever("hello world", cfg=CFG).search(5) == [])

CALLS.clear()
stub_get()
zh = arxiv_mod.ArxivRetriever("差分注意力基于原始注意力的优势", cfg=CFG).search(5)
check("纯中文不发请求", CALLS == [] and zh == [])

CALLS.clear()
stub_get()
arxiv_mod.ArxivRetriever("diff attention", cfg=CFG).search(5)
check("含英文才发请求", len(CALLS) == 1)

arxiv_mod.requests.get = REAL_GET

# ---------------------------------------------------------------- 2. 多源
print("---- 2. 多源并发合并 ----")

CFG2 = type("CFG", (), {
    "max_concurrency": 4, "max_pages": 12, "max_search_results": 5,
    "retrievers": ("tavily", "arxiv"), "retriever": "tavily",
})()


def make_search(table, delay=0.0, fail=()):
    def _search_with(name, sub_query, cfg):
        if name in fail:
            raise RuntimeError(f"{name} 挂了")
        if delay:
            time.sleep(delay)
        return list(table.get(name, []))
    return _search_with


base = pipeline._search_with

pipeline._search_with = make_search({
    "tavily": [{"href": "https://a.test/1", "body": "t1"}, {"href": "https://b.test/2", "body": "t2"}],
    "arxiv": [{"href": "https://c.test/3", "body": "a1"}, {"href": "https://a.test/1", "body": "dup"}],
})
merged = pipeline._search_one("Q", CFG2)
urls = [h["href"] for h in merged]
check("两源结果都进来了", len(merged) == 3, str(urls))
check("顺序按配置（tavily 先）", urls[:2] == ["https://a.test/1", "https://b.test/2"], str(urls))
check("跨源重复 URL 被去掉", urls.count("https://a.test/1") == 1)
check("重复项保留先出现的源", merged[0]["body"] == "t1")

pipeline._search_with = make_search({
    "tavily": [{"href": "https://a.test/1"}],
    "arxiv": [{"href": "https://a.test/1#frag"}, {"href": "https://c.test/3"}],
})
urls2 = [h["href"] for h in pipeline._search_one("Q", CFG2)]
check("归一化后也算重复", len(urls2) == 2, str(urls2))

pipeline._search_with = make_search(
    {"tavily": [{"href": "https://a.test/1"}], "arxiv": [{"href": "https://c.test/3"}]},
    fail=("arxiv",),
)
urls3 = [h["href"] for h in pipeline._search_one("Q", CFG2)]
check("单源失败不影响另一源", urls3 == ["https://a.test/1"], str(urls3))

pipeline._search_with = make_search({"tavily": [], "arxiv": [{"href": "https://c.test/3"}]})
check("一个源空 -> 另一个照常", [h["href"] for h in pipeline._search_one("Q", CFG2)] == ["https://c.test/3"])

pipeline._search_with = make_search(
    {"tavily": [{"href": "https://a.test/1"}], "arxiv": [{"href": "https://c.test/3"}]},
    delay=0.2,
)
t0 = time.time()
pipeline._search_one("Q", CFG2)
elapsed = time.time() - t0
check("两个源是真并发", elapsed < 0.35, f"{elapsed:.2f}s（串行会是 0.40s）")

pipeline._search_with = make_search({"tavily": [{"href": "https://a.test/1"}], "arxiv": []})
CFG1 = type("CFG", (), {"max_search_results": 5, "retrievers": ("tavily",), "retriever": "tavily"})()
check("单源走老路径", [h["href"] for h in pipeline._search_one("Q", CFG1)] == ["https://a.test/1"])

check("老 cfg 没有 retrievers 也能用",
      pipeline._retriever_names(type("C", (), {"retriever": "tavily"})()) == ("tavily",))

pipeline._search_with = base

# ---------------------------------------------------------------- 3. 配置
print("---- 3. 配置 ----")

import os

os.environ["RETRIEVERS"] = " Tavily , arxiv ,, TAVILY "
check("逗号分隔 + 去空格 + 小写 + 去重",
      config._env_list("RETRIEVERS", ("x",)) == ("tavily", "arxiv"),
      str(config._env_list("RETRIEVERS", ("x",))))

os.environ["RETRIEVERS"] = ""
os.environ["RETRIEVER"] = "arxiv"
check("RETRIEVERS 空 -> 回退 RETRIEVER", config._default_retrievers() == ("arxiv",))

os.environ.pop("RETRIEVERS", None)
os.environ.pop("RETRIEVER", None)
check("两个都没设 -> tavily", config._default_retrievers() == ("tavily",))

os.environ["RETRIEVERS"] = "tavily;arxiv"
check("分号也认", config._env_list("RETRIEVERS", ()) == ("tavily", "arxiv"))
os.environ.pop("RETRIEVERS", None)

# ---------------------------------------------------------------- 4. 接线
print("---- 4. 接线 ----")

check("注册表能造出 arxiv", isinstance(get_retriever("arxiv", "x"), arxiv_mod.ArxivRetriever))
check("注册表能造出 tavily", type(get_retriever("tavily", "x")).__name__ == "TavilyRetriever")

FETCHED = []


def fake_fetch(url, title, cfg):
    FETCHED.append(url)
    return {"title": title or "T", "url": url, "raw_content": "正文" * 30}


real_fetch = pipeline._fetch_one
real_search_with = pipeline._search_with
pipeline._fetch_one = fake_fetch
pipeline._search_with = make_search({
    "tavily": [{"title": "T1", "href": "https://a.test/1"}],
    "arxiv": [{"title": "T2", "href": "https://a.test/1"}, {"title": "T3", "href": "https://c.test/3"}],
})
FETCHED.clear()
pages = pipeline._collect_pages(["子问题"], CFG2)
urls = [p["url"] for p in pages]
check("_collect_pages 全程只抓一次重复 URL", urls == ["https://a.test/1", "https://c.test/3"], str(urls))
check("抓取次数与去重后一致", len(FETCHED) == 2, str(FETCHED))

pipeline._fetch_one = real_fetch
pipeline._search_with = real_search_with

print()
if FAILED:
    print(f"{len(FAILED)} 项失败: {FAILED}")
    sys.exit(1)
print("阶段 17 全部通过")