"""提示词集中管理。

对照源码：gpt_researcher/prompts.py（750 行，PromptFamily 类）

为什么单独一个文件：
    提示词在这个项目里是「数据」而不是「逻辑」。阶段 3 的子问题拆解、
    阶段 6 的报告生成都会往这里加函数。集中放便于对比措辞、迭代版本。

    原项目把它们收进一个类，是为了给 Granite 等模型做变体覆盖
    （GranitePromptFamily 重写了 pretty_print_docs 等方法）。
    我们暂时用模块级函数，等真需要多套提示词时再改成类。
"""
from __future__ import annotations

from datetime import datetime, timezone


def quick_summary_prompt(query:str,context:str)->str:
    """
    阶段2 基于检索结果写一段带引用的短答案
    对照源码：gpt_researcher/prompts.py:532 generate_quick_summary_prompt
    原版的三条约束都保留，其中第4条最重要:
    "
    :param query:
    :param context:
    :return:
    """
    return f"""请仅根据下面提供的检索结果回答用户的问题。
            问题:"{query}"
            检索结果:{context}
            要求:
            1. 用一段连贯的话回答，不要分点罗列。
            2. 每个事实后面用 [1]、[2] 这样的编号标注来源，编号对应上面检索结果的序号。
            3. 控制在 200 字以内。
            4. 如果检索结果不足以回答问题，直接说「现有资料不足以回答这个问题」，
               不要用你自己的知识补充。
            5. 用和问题相同的语言回答。
            """
def search_queries_prompt(query:str,max_queries:int=4)->str:
    """阶段3 把复杂问题拆成N个可独立检索的子问题
    对照源码
    三个细节都是踩过坑的
    1.动态生成示例 且示例个数=max_queries 示例个数会强烈影响模型的输出个数
    2.注入当前日期。
    3.明确禁止搜索操作符
    """
    today = datetime.now(timezone.utc).strftime("%Y 年 %m 月 %d 日")
    example = "，".join(f'"子查询{i+1}"'for i in range(max_queries))

    return f"""请围绕下面的研究任务，生成{max_queries}条搜索查询:
"{query}"
要求：
1. 每条查询必须是能独立交给搜索引擎的完整问句，不要依赖其他查询的上下文。
2. 不要使用 site:、filetype:、inurl:、intitle:、OR、AND、NOT 这类搜索操作符，
   它们在很多搜索后端上不被支持，会导致空结果。
3. 如果任务涉及时间，参考当前日期：{today}。
4. 只输出一个 JSON 字符串数组，格式为 [{example}]，不要输出任何其他内容。
5. 如果研究任务过于模糊或信息不足，无法拆解成有意义的子问题（例如只有一个
   字母、一个含义不明的词、或明显的输入错误），直接返回空数组 []，
   不要为了凑数而生成泛泛而谈的查询。
"""

def report_prompt(query: str, context: str, *, total_words: int = 1000, max_source: int = 0,extra:str="") -> str:
    """阶段 6 正文提示词。对照源码：gpt_researcher/prompts.py:262

    extra:阶段9用，上一轮评审提出的缺口，追加在写作要求后面 用来把上一轮评审意见喂给写作者
    """
    today = datetime.now(timezone.utc).strftime("%Y 年 %m 月 %d 日")
    range_hint = f"（只能是 1 到 {max_source}）" if max_source else "（见上面资料）"
    extra_block = ""
    if extra:
        extra_block = f"\n补充要求（必须逐条正面处理）：\n{extra}\n"
    return f"""请仅根据下面提供的资料，就下面的研究任务写一篇详细的报告。

研究任务："{query}"

资料（每条以 [编号] 开头）：
{context}

写作要求：
1. 只使用上面资料里出现的信息，绝对不要补充你自己的背景知识。
   资料不足以支撑的论点就不要写。
2. 用 Markdown 写，标题层级：# 主标题、## 主要章节、### 子章节。
3. 每一处事实、数字、引语后面都要标来源角标，格式是 [编号]，
   编号必须是上面资料里真实存在的编号{range_hint}。
   多个来源并排写：[1][3]。不要写 [1-3] 这种区间形式。
4. 不要写"参考文献"章节，程序会自动追加。
5. 不要写"目录"。
6. 不要写"结论"章节，程序会自动追加。
7. 不要用 ``` 代码块把整篇报告包起来。
8. 对比类信息用 Markdown 表格呈现。
9. 全文约 {total_words} 字，力求详尽，把资料里有价值的信息都用上。
10. 要有明确的判断和取舍，不要写"各有优劣""取决于具体情况"这种不表态的话。
11. 用与"研究任务"相同的语言写作。
{extra_block}
假设当前日期是 {today}。
"""

def report_introduction_prompt(query: str, context: str, *, max_source: int = 0) -> str:
    """引言。对照源码：gpt_researcher/prompts.py:707

    上游让这一调用负责写 H1 主标题；我们让正文负责 H1，引言只写段落，
    否则正文和引言各出一个 # 标题，报告会有两个主标题。
    """
    range_hint = f"（1 到 {max_source}）" if max_source else ""
    return f"""下面是为研究任务收集的资料：
{context}

请为"研究任务：{query}"写一段报告引言，要求：
1. 不要写 "# " 一级标题，报告主标题已经由正文写好了。
2. 3 到 5 句话：说清这个任务为什么值得研究、报告会覆盖哪几个方面。
3. 事实性内容要带 [编号] 角标，编号必须是资料里真实存在的编号{range_hint}。
4. 不要写"参考文献"章节。
5. 用与"研究任务"相同的语言写作。
"""


def report_conclusion_prompt(query: str, report: str) -> str:
    """结论。对照源码：gpt_researcher/prompts.py:720

    上游把这条规则写进提示词："如果没有 ## Conclusion 就自己加一个"。
    我们反过来：让模型别加标题，由程序加 —— 标题格式统一由代码控制。
    """
    return f"""下面是一篇已经写好的研究报告：
{report}

请就"研究任务：{query}"写一段结论，要求：
1. 不要写 "# " 一级标题，也不要写 "## 结论" 这个标题本身（程序会加）。
2. 分 2 到 3 段：先概括主要发现，再给出最重要的判断，最后说局限或下一步。
3. 只能依据报告里已有的内容，不要引入新事实；可以直接复用报告里的 [编号] 角标。
4. 不要写"参考文献"章节。
5. 用与"研究任务"相同的语言写作。
"""

def reviewer_prompt(query: str, report: str, context: str, *,
                    round_no: int, max_rounds: int,
                    previous_gaps: list[str] | None = None) -> str:
    """阶段 9 评审者。对照源码：multi_agents/ 的评审角色。

    三分类是这版的核心：missing 会长成下一轮的检索词，所以只有"真能搜回来"的
    才配进 missing。把"资料里没有的数据"记成 missing，等于让研究者去找不存在的东西。
    """
    prev_block = ""
    if previous_gaps:
        prev_block = (
            "\n上一轮你提过的缺口（研究者已经针对它们做过一轮检索）：\n"
            + "\n".join(f"{i}. {g}" for i, g in enumerate(previous_gaps, 1))
            + "\n如果某条缺口本轮资料里**仍然**没有对应信息，那它就不是"
              "\"检索没做够\"，而是资料边界 —— 请放进 unfixable，不要重复放进 missing。\n"
        )
    return f"""你是这份研究报告的评审者。只做评审，不要重写报告。

研究任务："{query}"

报告草稿：
{report}

报告可用的全部资料（带 [编号]，报告只能引用这些编号）：
{context}
{prev_block}
这是第 {round_no} 轮，最多 {max_rounds} 轮。请检查并**分类**：

1. unsupported：报告里哪些话是资料支撑不了的？引用编号越界、把资料没说的当事实写、
   把"初步验证"写成"已验证"这类强度放大的都算。
2. missing：哪些缺口是"再检索一轮就有机会补上"的？每条一句话，必须具体到能当检索词用，
   最多 3 条。不要写"可以更详细"这种没法执行的意见。
3. unfixable：哪些是资料里根本没有、再检索也大概率补不上的？
   （典型：某类对照实验根本没被公开做过、某个指标没有实测数据、某个机制没有公开讨论。）
   这类**不要**放进 missing。对每一条，说明"报告应该怎样如实声明这个边界"。
4. 有没有自相矛盾、或者该表态却写"各有优劣""取决于具体情况"的地方？记进 missing。

只输出一个 JSON 对象，不要任何其他内容：
{{"pass": true, "score": 8, "missing": [], "unsupported": [], "unfixable": []}}

pass 的判定（三条**同时**满足才写 true）：
- unsupported 为空；
- missing 为空；
- unfixable 里的每一条，报告都已经在正文或局限里如实写明了 —— 要写清"现有资料无法回答
  什么、为什么无法判断"，只写"资料未覆盖"四个字不算。

注意：missing 会长成下一轮的检索词。把"资料没有提供公平对比数据"记成 missing，
下一轮就会去搜一个不存在的东西，白烧一轮的钱 —— 这种应该进 unfixable，
并要求报告写明为什么无法比较。
"""