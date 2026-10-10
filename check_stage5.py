"""阶段 5 离线自测：分词 / BM25 / 分块 / 兜底 / 短路。不需要网络。"""
from config import Config, load_config
from context import bm25_scores, chunk_text, select_context, tokenize

ok = True

def check(name: str, cond: bool, detail: str = "") -> None:
    global ok
    ok = ok and cond
    print(f"{'OK  ' if cond else 'FAIL'} {name:<28} {detail}")

# 1. 中文 2-gram
toks = tokenize("检索增强生成")
check("中文切 2-gram", "检索" in toks and "生成" in toks, f"{toks}")

# 2. 中文查询能命中文档
doc = "检索增强生成是一种把外部知识引入大模型的技术。"
hit = set(tokenize(doc)) & set(tokenize("检索增强生成是什么"))
check("中文查询有交集", len(hit) > 0, f"命中 {sorted(hit)}")

# 3. BM25 排名翻转
CORPUS = [
    "Python 是一种编程语言。Python 编程广泛应用于数据分析、机器学习和 Web 开发。"
    "Python 的语法简洁清晰，非常适合初学者学习编程。本页介绍 Python 编程的基础知识。",
    "asyncio 是 Python 标准库里的异步编程框架。异步编程让程序在等待网络或磁盘 IO 时"
    "去执行别的任务，而不是干等。协程 async await 都是异步编程的核心概念。",
    "本页讨论 JavaScript 的异步编程模型。前端开发中异步编程主要靠 Promise 和 async。",
    "本页介绍 Python 的编程规范 PEP8，包括命名、缩进和注释的写法。",
    "本页介绍 Python 的虚拟环境与包管理。",
    "本页是一份编程入门的阅读书单，和编程语言无关。",
]
q = "Python 异步编程"
scores = bm25_scores(q, CORPUS)
rank = sorted(range(len(CORPUS)), key=lambda i: -scores[i])
check("BM25 选中 asyncio 篇", rank[0] == 1, f"第 {rank[0]+1} 篇胜出，分数 {scores[rank[0]]:.3f}")
naive = [sum(c.lower().count(w) for w in ["python", "异步", "编程"]) for c in CORPUS]
check("计数法选错", naive.index(max(naive)) == 0, f"计数法选第 {naive.index(max(naive))+1} 篇")

# 4. 分块与重叠
text = "\n".join(f"第{i}行：这是第 {i} 段正文内容。" + "填充" * 12 for i in range(1, 41))
chunks = chunk_text(text, 400, 40)
check("分块数量合理", len(chunks) >= 2, f"{len(text)} 字符 -> {len(chunks)} 块，长度 {[len(c) for c in chunks]}")
check("相邻块有重叠", chunks[0][-40:] in chunks[1], "重叠生效")

# 5. 兜底：无词汇重合时不能返回空
PAGES = [{"title": "P1", "url": "u1", "raw_content": text},
         {"title": "P2", "url": "u2", "raw_content": "asyncio 是异步框架。" * 40}]
cfg = Config(compression_threshold=500, chunk_size=400)
ctx = select_context("量子引力 黑洞 相对论", PAGES, cfg=cfg)
check("无重合时非空", len(ctx) > 0, f"返回 {len(ctx)} 字符")

# 6. 短路
small = [{"title": "t", "url": "u", "raw_content": "一点点内容。" * 20}]
check("小语料不筛选", len(select_context("随便", small, cfg=load_config())) > 0, "短路生效")

# 7. 相关查询真的能选中
ctx = select_context("k1 参数控制什么", PAGES, cfg=cfg)
check("相关查询压缩生效", 0 < len(ctx) < sum(len(p["raw_content"]) for p in PAGES),
      f"{sum(len(p['raw_content']) for p in PAGES)} -> {len(ctx)} 字符")

print("\n全部通过" if ok else "\n有失败项")