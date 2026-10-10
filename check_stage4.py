# check_stage4.py
"""阶段 4 验收：5 条用例，直接打印结论。"""
from scrape import fetch

CASES = [
    ("正常英文页", "https://github.com/assafelovic/gpt-researcher"),
    ("中文页（查乱码）", "https://zh.wikipedia.org/wiki/%E6%A3%80%E7%B4%A2%E5%A2%9E%E5%BC%BA%E7%94%9F%E6%88%90"),
    ("PDF", "https://arxiv.org/pdf/1706.03762"),
    ("404", "https://github.com/assafelovic/gpt-researcher/blob/main/NOPE.md"),
    ("域名不存在", "https://this-domain-does-not-exist-12345.com"),
]

for name, url in CASES:
    r = fetch(url)
    flag = "OK  " if r["ok"] else "FAIL"
    detail = r["title"][:45] if r["ok"] else r["reason"]
    print(f"{flag} {name:<16} status={r['status']:<5} len={len(r['raw_content']):<6} {detail}")