"""阶段 8 离线自检：验证去重与顺序恢复，网络层打桩。

用法：python check_stage8.py
"""
import time

import pipeline

FAILED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<34} {extra}")
    if not ok:
        FAILED.append(name)


# ---------------- 1. URL 归一化
norm = pipeline._normalize_url
check("去片段", norm("https://a.com/x#sec") == "https://a.com/x")
check("去末尾斜杠", norm("https://a.com/x/") == "https://a.com/x")
check("域名后的斜杠保留一个", norm("https://a.com/") == "https://a.com")
check("保留 query", norm("https://a.com/x?id=1") == "https://a.com/x?id=1")
check("空串", norm("") == "")

# ---------------- 2. 打桩：假检索 + 假抓取
HITS = {
    "q1": [
        {"url": "https://a.com/1", "title": "A1"},
        {"url": "https://b.com/2", "title": "B2"},
        {"url": "https://c.com/3#frag", "title": "C3"},
    ],
    "q2": [
        {"url": "https://c.com/3", "title": "C3 重复"},
        {"url": "https://d.com/4", "title": "D4"},
    ],
}
CALLS = {"fetch": []}


def fake_search(sub, cfg):
    # q1 故意睡一会，让它在 q2 之后完成，验证顺序不依赖完成先后
    time.sleep(0.08 if sub == "q1" else 0)
    return HITS[sub]


def fake_fetch(url, title, cfg):
    CALLS["fetch"].append(url)
    if "d.com" in url:
        return None                      # 模拟抓取失败
    time.sleep(0.08 if "a.com" in url else 0)
    return {"title": title, "url": url, "raw_content": "x" * 10}


pipeline._search_one = fake_search
pipeline._fetch_one = fake_fetch

CFG = type("CFG", (), {"max_concurrency": 4, "max_pages": 99})
pages = pipeline._collect_pages(["q1", "q2"], CFG)

got = [p["url"] for p in pages]
c_calls = [u for u in CALLS["fetch"] if "c.com" in u]

check("重复 URL 只抓一次", len(c_calls) == 1, str(c_calls))
check("抓取失败的页面被跳过", all("d.com" not in u for u in got), str(got))
check("顺序 = 检索顺序（与完成先后无关）",
      got == ["https://a.com/1", "https://b.com/2", "https://c.com/3#frag"], str(got))
check("返回条数正确", len(got) == 3, str(len(got)))

# ---------------- 3. 单子问题失败只跳过
def boom(sub, cfg):
    if sub == "qx":
        raise RuntimeError("模拟检索报错")
    return HITS[sub]


pipeline._search_one = boom
pages2 = pipeline._collect_pages(["qx", "q1"], CFG)
check("检索报错的子问题被跳过，其余照常",
      [p["url"] for p in pages2] == ["https://a.com/1", "https://b.com/2",
                                     "https://c.com/3#frag"],
      str([p["url"] for p in pages2]))

# ---------------- 4. 上限与空输入
CFG1 = type("CFG", (), {"max_concurrency": 2, "max_pages": 1})
check("MAX_PAGES 生效", len(pipeline._collect_pages(["q1"], CFG1)) == 1)
check("空子问题列表返回空", pipeline._collect_pages([], CFG) == [])

print()
print("全部通过" if not FAILED else f"{len(FAILED)} 项失败：{FAILED}")
raise SystemExit(1 if FAILED else 0)