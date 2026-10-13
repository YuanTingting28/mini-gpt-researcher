"""阶段 7 离线自检：只验算数。用法：python check_stage7.py"""
from costs import PRICING, PRICING_AS_OF, CostTracker

FAILED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + f"{name:<34} {extra}")
    if not ok:
        FAILED.append(name)


t = CostTracker()
c = t.add("deepseek-flash", 1_000_000, 1_000_000)
check("100万入 + 100万出", abs(c - 10.0) < 1e-9, f"¥{c:.4f}（期望 10.0000）")

t = CostTracker()
c = t.add("deepseek-flash", 1_000_000, 0, cache_hit_tokens=1_000_000)
check("100万全部命中缓存", abs(c - 0.5) < 1e-9, f"¥{c:.4f}（期望 0.5000）")

t = CostTracker()
c = t.add("deepseek-flash", 100, 0, cache_hit_tokens=999)
check("命中数 > 输入数时被夹住", abs(c - 0.5 * 100 / 1e6) < 1e-12, f"¥{c:.8f}")

t = CostTracker()
c = t.add("some-unknown-model", 1_000_000, 1_000_000)
check("未知模型 → 0 元", c == 0.0)
check("未知模型被记进 unpriced", "some-unknown-model" in t.unpriced)
check("report 显式声明低估", "低估" in t.report())

t = CostTracker()
c = t.add("deepseek-flash-20260101", 1_000_000, 0)
check("带日期的模型名走前缀匹配", abs(c - 2.0) < 1e-9, f"¥{c:.4f}（期望 2.0000）")

t = CostTracker()
t.add("deepseek-flash", 1_000_000, 0, step="正文")
t.add("deepseek-flash", 1_000_000, 0, step="正文")
t.add("deepseek-flash", 1_000_000, 0, step="结论")
check("合计 = 各次之和", abs(t.total - 6.0) < 1e-9, f"¥{t.total:.4f}（期望 6.0000）")
check("按步骤拆分正确",
      abs(t.by_step["正文"] - 4.0) < 1e-9 and abs(t.by_step["结论"] - 2.0) < 1e-9,
      str({k: round(v, 5) for k, v in t.by_step.items()}))

r = t.report()
check("report 含取值日期", PRICING_AS_OF in r)
check("report 含合计 6.00000", "6.00000" in r)
check("金额降序", r.index("正文") < r.index("结论"))

print()
print("单价表：", PRICING)
print("全部通过" if not FAILED else f"{len(FAILED)} 项失败：{FAILED}")
raise SystemExit(1 if FAILED else 0)