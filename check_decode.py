"""离线验证编码判定：不联网也能测中文。"""
import requests

from scrape import _decode

BODY = (
    "检索增强生成是一种把外部知识引入大模型的技术。"
    "它先在外部语料中检索相关片段，再把这些片段拼进提示词，"
    "让模型基于给定材料作答，从而降低幻觉的发生概率。"
)
HTML = f"<html><head><title>测试页</title></head><body><p>{BODY}</p></body></html>"


def fake(content: bytes, content_type: str, encoding: str | None) -> requests.Response:
    """造一个假的 Response，只填 _decode 会读到的那几个字段。"""
    r = requests.Response()
    r.headers["Content-Type"] = content_type
    r._content = content
    r.encoding = encoding
    return r


CASES = [
    ("声明缺失，GBK 字节", fake(HTML.encode("gbk"), "text/html", "ISO-8859-1")),
    ("声明正确 utf-8", fake(HTML.encode("utf-8"), "text/html; charset=utf-8", "utf-8")),
    ("声明正确 gbk", fake(HTML.encode("gbk"), "text/html; charset=gbk", "gbk")),
    ("encoding 为 None", fake(HTML.encode("utf-8"), "text/html", None)),
]

for name, resp in CASES:
    out = _decode(resp)
    ok = "检索增强生成" in out and "幻觉" in out
    print(f"{'OK  ' if ok else 'FAIL'} {name:<18} -> {out[:70]}")