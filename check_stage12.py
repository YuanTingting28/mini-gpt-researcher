"""阶段 12 离线自检：SSRF 防护 + PDF 正文抽取。全程打桩，不联网。

用法：python check_stage12.py      （放在项目根目录或 checks/ 里都能跑）
"""
import os
import pathlib
import socket
import sys

# 放在 checks/ 子目录里也要能 import 到 scrape：往上找到项目根（有 scrape.py 的那层）
_ROOT = pathlib.Path(__file__).resolve().parent
while not (_ROOT / "scrape.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))

import requests

import scrape
from url_security import (
    HostNotResolvable, UnsafeURLError, is_safe_url, validate_url,
)

FAILED = []
SKIPPED = []

def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<42} {extra}")
    if not ok:
        FAILED.append(name)

def skip(name, why=""):
    print(f"SKIP {name:<42} {why}")
    SKIPPED.append(name)

# ============ 0. 把 DNS 打桩，离线也能测"解析到内网" ============
_REAL_GAI = socket.getaddrinfo
DNS = {
    "public.test": "93.184.216.34",
    "mixed.test": "MIXED",        # 一公一私，验证"任一条内网就拒"
    "private.test": "10.0.0.7",
    "lan.test": "192.168.1.10",
    "docker.test": "172.17.0.2",
    "loop.test": "127.0.0.1",
    "meta.test": "169.254.169.254",
    "unknown.test": None,         # 解析失败
}

def _fake_getaddrinfo(host, port, *args, **kwargs):
    if host in DNS:
        ip = DNS[host]
        if ip == "MIXED":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0)),
            ]
        if ip is None:
            raise socket.gaierror(f"[Errno 11001] getaddrinfo failed for {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    # 字面 IP（127.0.0.1 这种）交给真解析器，它只做本地格式转换，不查网络
    return _REAL_GAI(host, port, *args, **kwargs)

socket.getaddrinfo = _fake_getaddrinfo

def rejects(url, **kw):
    """被拒绝就返回异常，放行返回 None。"""
    try:
        validate_url(url, **kw)
    except UnsafeURLError as exc:
        return exc
    return None

# ---------------- 1. 协议与形态
for url, why in (
    ("file:///etc/passwd", "file 协议"),
    ("ftp://public.test/x.pdf", "ftp 协议"),
    ("javascript:alert(1)", "javascript 协议"),
    ("/etc/passwd", "本地路径（没有协议）"),
    ("", "空 URL"),
    ("http://", "没有主机名"),
):
    check(f"拒绝：{why}", rejects(url) is not None)

# ---------------- 2. 指向内网
for url, why in (
    ("http://loop.test/", "回环 127.0.0.1"),
    ("http://private.test/", "私有 10.0.0.7"),
    ("http://lan.test/", "私有 192.168.1.10"),
    ("http://docker.test/", "私有 172.17.0.2"),
    ("http://meta.test/latest/meta-data/", "云元数据 169.254.169.254"),
    ("http://127.0.0.1/", "字面回环"),
    ("http://[::1]/", "字面 IPv6 回环"),
):
    check(f"拒绝：{why}", rejects(url) is not None)

check("放行公网主机", validate_url("https://public.test/a") == "https://public.test/a")
check("一公一私的 A 记录 -> 拒绝", rejects("http://mixed.test/") is not None)

exc = rejects("http://unknown.test/")
check("解析失败归到 HostNotResolvable", isinstance(exc, HostNotResolvable), repr(exc))
check("解析失败时 is_safe_url=False", is_safe_url("http://unknown.test/") is False)
check("file 协议 is_safe_url=False", is_safe_url("file:///etc/passwd") is False)

# ---------------- 3. 内网自建服务的放行开关
os.environ["ALLOW_PRIVATE_URLS"] = "1"
check("ALLOW_PRIVATE_URLS=1 放行内网", validate_url("http://loop.test/") == "http://loop.test/")
os.environ.pop("ALLOW_PRIVATE_URLS", None)
check("去掉环境变量又拦回来", rejects("http://loop.test/") is not None)
check("allow_private=True 参数也能放行",
      validate_url("http://10.0.0.7/", allow_private=True) == "http://10.0.0.7/")

# ============ 4. 假的 HTTP 层 ============
class FakeResp:
    """够 fetch 用的最小响应对象。"""

    def __init__(self, status=200, body=b"", content_type="text/html; charset=utf-8",
                 location=None, url="", content_length=None):
        self.status_code = status
        self.url = url
        self.headers = {"Content-Type": content_type}
        if location is not None:
            self.headers["Location"] = location
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True

class FakeSession:
    """按 URL 发预设响应；没预设就抛网络错误。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self.kwargs = []

    def get(self, url, **kw):
        self.calls.append(url)
        self.kwargs.append(kw)
        item = self.routes.get(url)
        if item is None:
            raise requests.ConnectionError(f"FAKE: 没给 {url} 准备响应")
        if isinstance(item, Exception):
            raise item
        return item

CFG = type("CFG", (), {"scrape_timeout": 5, "scrape_max_chars": 12000})()
CFG_TINY = type("CFG", (), {"scrape_timeout": 5, "scrape_max_chars": 12000,
                            "scrape_max_bytes": 1000})()

SENTENCES = [
    "GPT Researcher 是一个自主研究代理。",
    "它先规划子问题，再并发检索与抓取。",
    "抓回来的正文经过压缩后交给写作模型。",
    "报告里的每个事实后面都要带引用编号。",
    "检索、压缩、写作三步是可以分别验收的。",
]
LONG = "".join(SENTENCES * 4)
HTML = (f"<html><head><title>测试页</title></head><body>"
        f"<article><p>{LONG}</p></article></body></html>").encode("utf-8")
HTML_MARK = "GPT Researcher"   # 用 ASCII：不管解码猜成什么，ASCII 都不会变；用来确认"这页正文被解析出来了"
# ---------------- 5. 重定向：跟得上、也拦得住
# 声明是 PDF、后缀也是 .pdf，但字节其实是网页 —— 走"退回按网页解析"那条路。
# 这条正是在验 _pdf_confidence 的两档区分：没有 %PDF- 文件头，就不该硬判成 PDF。
sess = FakeSession(
    {
        "https://public.test/fake.pdf": FakeResp(
            200,
            body=HTML,
            content_type="application/pdf",
            url="https://public.test/fake.pdf",
        )
    }
)
r = scrape.fetch("https://public.test/fake.pdf", cfg=CFG, session=sess)
check(
    "声明是 PDF 但字节是网页 -> 退回按网页解",
    r["ok"] and HTML_MARK in r["raw_content"],
    r["reason"] or r["raw_content"][:40],
)
check("自己接管重定向（关掉 requests 自动跳转）",
      bool(sess.kwargs) and all(k.get("allow_redirects") is False for k in sess.kwargs),
      str(sess.kwargs[:1]))
check("最终那一页的正文抓到了", r["ok"] and len(r["raw_content"]) > 300, r["reason"])

sess = FakeSession({
    "https://public.test/evil": FakeResp(
        302, location="http://169.254.169.254/latest/meta-data/",
        url="https://public.test/evil"),
    "http://169.254.169.254/latest/meta-data/": FakeResp(
        200, body=b"AKIA-secret", url="http://169.254.169.254/latest/meta-data/"),
})
r = scrape.fetch("https://public.test/evil", cfg=CFG, session=sess)
check("重定向到云元数据 -> 拒绝",
      (not r["ok"]) and "不安全" in r["reason"], r["reason"])
check("被拒的那一跳根本没发出去", len(sess.calls) == 1, str(sess.calls))

routes = {}
for i in range(8):
    routes[f"https://public.test/h{i}"] = FakeResp(
        302, location=f"/h{i+1}", url=f"https://public.test/h{i}")
routes["https://public.test/h8"] = FakeResp(200, body=HTML, url="https://public.test/h8")
r = scrape.fetch("https://public.test/h0", cfg=CFG, session=FakeSession(routes))
check("重定向超上限 -> 报错不硬跟",
      (not r["ok"]) and "重定向超过" in r["reason"], r["reason"])

# ---------------- 6. 直接给内网 / 解析不了
sess = FakeSession()
r = scrape.fetch("http://loop.test/", cfg=CFG, session=sess)
check("内网 URL 直接拒（一个请求都不发）",
      (not r["ok"]) and "不安全" in r["reason"] and not sess.calls, r["reason"])
check("策略拒绝不算 transient", r["transient"] is False, str(r["transient"]))

r = scrape.fetch("https://unknown.test/x", cfg=CFG, session=FakeSession())
check("解析不了 -> 可重试（transient）", (not r["ok"]) and r["transient"] is True, r["reason"])
check("解析不了报的是「解析失败」而不是「不安全」", "域名解析失败" in r["reason"], r["reason"])
# ---------------- 7. 响应体上限
r = scrape.fetch("https://public.test/big", cfg=CFG, session=FakeSession({
    "https://public.test/big": FakeResp(200, body=b"x" * 10, content_length=10 ** 9,
                                        url="https://public.test/big")}))
check("Content-Length 撒谎 -> 提前拒", (not r["ok"]) and "过大" in r["reason"], r["reason"])

r = scrape.fetch("https://public.test/chunked", cfg=CFG_TINY, session=FakeSession({
    "https://public.test/chunked": FakeResp(200, body=b"y" * 5000,
                                            url="https://public.test/chunked")}))
check("不声明长度也拦得住（读的过程中叫停）",
      (not r["ok"]) and "过大" in r["reason"], r["reason"])

# ============ 8. PDF ============
def make_pdf(fitz, lines):
    """用 PyMuPDF 现场造一个测试 PDF —— 不联网也能测真实解析。

    为什么不塞一个二进制 fixture 进仓库：二进制在 git 里没法 review，
    也没法改一行字再跑一次。现场造的好处是"输入是什么"直接写在代码里。
    返回 (字节, 中文行是否写进去了)。
    """
    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    cjk_ok = True
    for line in lines:
        kwargs = {}
        if any("\u4e00" <= ch <= "\u9fff" for ch in line):
            # 中文得指定 PyMuPDF 内置的 CJK 字体：默认的 helv 没有汉字字形，
            # 写进去是一堆方框，抽回来就不是原文了。
            kwargs["fontname"] = "china-s"
        try:
            page.insert_text((56, y), line, fontsize=11, **kwargs)
        except Exception:
            if kwargs:
                cjk_ok = False     # 内置 CJK 字体不可用：跳过这行，别把整组自检带崩
                continue
            raise
        y += 18
    data = doc.tobytes()
    doc.close()
    return data, cjk_ok

PDF_LINES = [
    "Differential Transformer replaces softmax attention with a difference of two attention maps.",
    "Subtracting one attention map from another cancels the shared noise component.",
    "Reported gains: lower perplexity on language modelling, better long-context retrieval.",
    "The construction keeps the same parameter count and the same asymptotic cost.",
    "检索增强生成把外部知识引入大模型，先检索片段再拼进提示词，从而降低幻觉。",
]

fitz = scrape._pymupdf()
if fitz is None:
    skip("PDF 整组", "未安装 pymupdf：pip install pymupdf")
else:
    pdf, cjk_ok = make_pdf(fitz, PDF_LINES)
    check("测试用 PDF 生成成功", pdf[:5] == b"%PDF-", repr(pdf[:8]))
    check("字节判定：文件头 -> 2", scrape._pdf_confidence(pdf) == 2,
          str(scrape._pdf_confidence(pdf)))
    check("字节判定：普通文章 -> 0", scrape._pdf_confidence(LONG.encode("utf-8")) == 0)
    check("字节判定：只有结构标记 -> 1",
          scrape._pdf_confidence(b"...endobj...endstream...trailer...") == 1)
    check("_sniff 字节优先于声明", scrape._sniff("text/html", 2) == "pdf")
    check("_sniff 认 application/pdf", scrape._sniff("application/pdf", 0) == "pdf")
    check("_sniff 普通二进制 -> binary",
          scrape._sniff("application/octet-stream", 0) == "binary")
    check("_sniff 网页 -> html", scrape._sniff("text/html; charset=utf-8", 0) == "html")
    check("_sniff 没有 Content-Type 也当网页", scrape._sniff("", 0) == "html")

    sess = FakeSession({"https://public.test/paper.pdf": FakeResp(
        200, body=pdf, content_type="application/pdf", url="https://public.test/paper.pdf")})
    r = scrape.fetch("https://public.test/paper.pdf", cfg=CFG, session=sess)
    check("PDF 抽出了正文", r["ok"] and len(r["raw_content"]) > 100,
          r["reason"] or r["raw_content"][:50])
    check("PDF 里没有二进制垃圾", "endobj" not in r["raw_content"], r["raw_content"][:50])
    if cjk_ok:
        check("PDF 里的中文抽回原文", "检索增强生成" in r["raw_content"],
              r["raw_content"][:60])
    else:
        skip("PDF 里的中文抽回原文", "内置 CJK 字体不可用")

    sess = FakeSession({"https://public.test/bitstreams/9f3a/download": FakeResp(
        200, body=pdf, content_type="application/octet-stream",
        url="https://public.test/bitstreams/9f3a/download")})
    r = scrape.fetch("https://public.test/bitstreams/9f3a/download", cfg=CFG, session=sess)
    check("无后缀 + octet-stream 也认得出 PDF",
          r["ok"] and "检索增强生成" in r["raw_content"],
          r["reason"] or r["raw_content"][:40])

    sess = FakeSession({"https://public.test/x": FakeResp(
        200, body=pdf, content_type="text/html", url="https://public.test/x")})
    r = scrape.fetch("https://public.test/x", cfg=CFG, session=sess)
    check("类型被写错也拦得住（看字节）",
          r["ok"] and "检索增强生成" in r["raw_content"],
          r["reason"] or r["raw_content"][:40])

    blank, _ = make_pdf(fitz, [])
    sess = FakeSession({"https://public.test/scan.pdf": FakeResp(
        200, body=blank, content_type="application/pdf", url="https://public.test/scan.pdf")})
    r = scrape.fetch("https://public.test/scan.pdf", cfg=CFG, session=sess)
    check("扫描件 -> 明说没有文字层",
          (not r["ok"]) and "没有可抽取的文字" in r["reason"], r["reason"])

    sess = FakeSession({"https://public.test/fake.pdf": FakeResp(
        200, body=HTML, content_type="text/html", url="https://public.test/fake.pdf")})
    r = scrape.fetch("https://public.test/fake.pdf", cfg=CFG, session=sess)
    check(
        "后缀 .pdf 但内容是网页 -> 不误判",
        r["ok"] and HTML_MARK in r["raw_content"],
        r["reason"],
    )

socket.getaddrinfo = _REAL_GAI

print()
if SKIPPED:
    print(f"（{len(SKIPPED)} 组跳过：{SKIPPED}）")
print("全部通过" if not FAILED else f"{len(FAILED)} 项失败：{FAILED}")
raise SystemExit(1 if FAILED else 0)