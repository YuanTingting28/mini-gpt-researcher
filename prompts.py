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

def report_prompt(query: str, context: str, *, total_words: int = 1000, max_source: int = 0) -> str:
    """阶段 6 正文提示词。对照源码：gpt_researcher/prompts.py:262"""
    today = datetime.now(timezone.utc).strftime("%Y 年 %m 月 %d 日")
    range_hint = f"（只能是 1 到 {max_source}）" if max_source else "（见上面资料）"
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