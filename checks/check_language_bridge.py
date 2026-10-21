"""离线验证：英文证据 -> 中文报告的语言桥。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import load_config
from prompts import report_conclusion_prompt, report_introduction_prompt, report_prompt
from paper_wiki import should_enrich
import report

FAILS = []

def check(name, condition, detail=""):
    if condition:
        print(f"OK   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        FAILS.append(name)

cfg = load_config()
check("读取 REPORT_LANGUAGE=zh", cfg.report_language == "zh", cfg.report_language)

prompt = report_prompt("How do knowledge conflicts affect RAG?", "[1] Paper\nURL: https://x\n正文: ...", report_language="zh")
check("正文提示词要求中文", "整篇报告用中文写作" in prompt)
check("正文提示词不再跟随查询语言", '用与"研究任务"相同的语言' not in prompt)
check("正文提示词允许英文术语", "模型名、方法名、数据集名" in prompt)

intro = report_introduction_prompt("English query", "[1] Paper", report_language="zh")
check("引言提示词要求中文", "输入资料可能是英文" in intro and "整篇报告用中文写作" in intro)
conclusion = report_conclusion_prompt("English query", "# T\n正文 [1]", report_language="zh")
check("结论提示词要求中文", "整篇报告用中文写作" in conclusion)

sources = [{"n": 1, "title": "Paper · 第 3 页", "url": "file:///x.pdf#page=3"}]
zh = report.add_references("# T\n正文 [1]", sources, query="English query", cfg=cfg)
check("中文报告用中文文献标题", "## 参考文献" in zh and "## References" not in zh)
check("中文报告保留页码中文样式", "第 3 页" in zh)
cfg_en = load_config()
cfg_en.report_language = "en"
en = report.add_references("# T\n正文 [1]", sources, query="English query", cfg=cfg_en)
check("英文报告用英文文献标题", "## References" in en and "## 参考文献" not in en)
check("英文报告页码转英文", "page 3" in en)

english_page = {"url": "file:///D:/papers/a.pdf#page=1", "raw_content": "Retrieval augmented generation reduces hallucination."}
chinese_page = {"url": "file:///D:/papers/b.pdf#page=1", "raw_content": "检索增强生成可以降低幻觉。"}
check("本地英文 PDF 进入中文卡片桥", should_enrich(english_page, cfg))
check("中文 PDF 不重复做中文卡片", not should_enrich(chinese_page, cfg))
check("英文报告不触发中文卡片桥", not should_enrich(english_page, cfg_en))

calls = []
original_chat = report.chat
def fake_chat(messages, **kwargs):
    calls.append(messages)
    return "# 中文标题\n中文正文 [1]"
report.chat = fake_chat
try:
    report._generate("写报告", cfg, step="正文", temperature=0.0, query="English query")
finally:
    report.chat = original_chat
check("Writer 收到中文 system 指令", calls and calls[0][0]["role"] == "system" and "最终输出必须是中文" in calls[0][0]["content"])

orig_generate = report._generate
orig_optional = report._generate_optional
report._generate = lambda prompt, cfg, **kwargs: "# 中文标题\n中文正文 [1]"
report._generate_optional = lambda prompt, cfg, *, step, query=None: "中文结论 [1]"
try:
    out = report.write_report("English query", "[1] Paper\nURL: https://x\n正文: evidence [1]", sources, cfg=cfg)
finally:
    report._generate = orig_generate
    report._generate_optional = orig_optional
check("最终报告补中文章节", "## 结论" in out and "## 参考文献" in out)
check("最终报告没有混入英文章节名", "## Conclusion" not in out and "## References" not in out)

if FAILS:
    print(f"\n{len(FAILS)} 项失败：{FAILS}")
    raise SystemExit(1)
print("\n全部通过")
