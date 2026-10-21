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

import json
import re
from datetime import datetime, timezone


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def query_language(text: str) -> str | None:
    """粗判查询语言，只用于决定子问题该用哪种文字。

    返回 "zh" / "en" / None。这是启发式，不追求语言学准确：
    - 只有汉字 -> zh
    - 只有英文单词 -> en
    - 中英混排时按占优势的一方判断；势均力敌则返回 None，不强行改。
    """
    raw = text or ""
    cjk = len(_CJK_RE.findall(raw))
    ascii_words = len(_ASCII_WORD_RE.findall(raw))

    if cjk and not ascii_words:
        return "zh"
    if ascii_words and not cjk:
        return "en"
    if cjk > ascii_words * 2:
        return "zh"
    if ascii_words > cjk * 2:
        return "en"
    return None


def language_name(code: str | None) -> str:
    return {"zh": "中文", "en": "英文"}.get(code, "与研究任务相同的语言")


def resolve_report_language(query: str, report_language: str | None = None) -> str:
    """报告语言和检索语言分开：查询是英文，也可以写中文报告。"""
    raw = (report_language or "").strip().lower()
    if raw in {"zh", "cn", "chinese", "中文"}:
        return "zh"
    if raw in {"en", "english", "英文"}:
        return "en"
    return query_language(query) or "zh"


def report_language_instruction(query: str, report_language: str | None = None) -> str:
    """写进 Writer 提示词的输出语言契约。"""
    code = resolve_report_language(query, report_language)
    if code == "en":
        return """OUTPUT LANGUAGE (HARD REQUIREMENT):
- Write the entire report in English: title, section headings, body, introduction and conclusion.
- Sources may contain Chinese. Preserve Chinese proper nouns, model names, method names,
  dataset names, metrics, paper titles, formulas and variable names in their original form.
- On first mention, use "Chinese term (English Name)" when helpful; afterwards use the English name or abbreviation.
- Table headers should be English; proper nouns inside cells may remain in their original language.
- Keep reference titles and quoted source text in their original language.
- Do not copy whole Chinese paragraphs; explanatory prose must be English."""
    return """输出语言（硬性要求）：
- 整篇报告用中文写作：标题、章节名、正文、引言、结论都用中文。
- 输入资料可能是英文，但不要因此把正文改写成英文；先理解英文资料，再用中文解释。
- 保留英文：模型名、方法名、数据集名、指标名、论文标题、公式变量、专有名词和引用原文。
- 首次出现时可以写成“中文解释（English Name）”，后文直接使用英文名或缩写。
- 表格表头用中文；单元格中的模型名、方法名、指标、数据集可以保留英文。
- 参考文献标题保留英文原文，不要翻译。
- 除上述术语、表格内容和引用外，解释性文字必须使用中文。
- 严禁因为资料来源是英文就输出整段英文正文。"""


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
def search_queries_prompt(query:str,max_queries:int=4,persona:str="")->str:
    """阶段3 把复杂问题拆成N个可独立检索的子问题
    对照源码
    三个细节都是踩过坑的
    1.动态生成示例 且示例个数=max_queries 示例个数会强烈影响模型的输出个数
    2.注入当前日期。
    3.明确禁止搜索操作符
    """
    today = datetime.now(timezone.utc).strftime("%Y 年 %m 月 %d 日")
    target = query_language(query)
    target_name = language_name(target)

    # 示例本身也会影响输出语言：英文任务不能给中文示例，否则模型很容易
    # 把 "子查询1" 当成模板，整批输出中文。
    if target == "en":
        example = ", ".join(f'"search query {i + 1}"' for i in range(max_queries))
    elif target == "zh":
        example = ", ".join(f'"子查询{i + 1}"' for i in range(max_queries))
    else:
        example = ", ".join(f'"query {i + 1}"' for i in range(max_queries))
    persona_block = f"{persona}\n\n" if persona else ""

    return f"""请围绕下面的研究任务，生成{max_queries}条搜索查询:
    "{query}"

    {persona_block}要求：
1. 每条查询必须是能独立交给搜索引擎的完整问句，不要依赖其他查询的上下文。
2. 不要使用 site:、filetype:、inurl:、intitle:、OR、AND、NOT 这类搜索操作符，
   它们在很多搜索后端上不被支持，会导致空结果。
3. 如果任务涉及时间，参考当前日期：{today}。
4. 只输出一个 JSON 字符串数组，格式为 [{example}]，不要输出任何其他内容。
5. 如果研究任务过于模糊或信息不足，无法拆解成有意义的子问题（例如只有一个
   字母、一个含义不明的词、或明显的输入错误），直接返回空数组 []，
   不要为了凑数而生成泛泛而谈的查询。
6. 每条子问题必须使用{target_name}输出。研究任务是英文就全部用英文，研究任务是中文
   就全部用中文；不要把研究任务翻译成另一种语言。子问题的语言必须和原问题一致，
   因为检索器会直接拿它去匹配原始资料。
"""


def rewrite_queries_language_prompt(
    query: str,
    queries: list[str],
    target_language: str,
) -> str:
    """把已经生成的子问题改写成目标语言，保持原意和数量。"""
    payload = json.dumps(queries, ensure_ascii=False)
    return f"""下面有一组搜索子问题，但它们和原研究任务的语言不一致。

原研究任务：
{query}

原子问题：
{payload}

请把每个子问题改写成{target_language}，要求：
1. 保持原意、检索角度和数量，不要合并、不要新增、不要解释。
2. 不要使用 site:、filetype:、OR、AND、NOT 等搜索操作符。
3. 只输出一个 JSON 字符串数组，格式为 ["...", "..."]，不要输出其他内容。
"""

def expert_profile_prompt(question:str)->str:
    """阶段15 让模型给这个题目配一个专家人设

    对照源码：gpt_researcher/prompts.py 的 auto_agent_instructions（在 PromptFamily 里）
    三个要点：
      1. 只要 JSON 三个字段，不要散文 —— 下游是代码在消费，不是人在读。
      2. 明确"不要写具体结论/事实" —— 否则模型会顺手把答案写进人设，
         而人设没经过检索，等于提前把未经证据的偏见植入整篇报告。
      3. focus 限 3~5 条，每条必须是"可检索的角度"（如"训练目标函数"），
         不能是"要回答的问题"，否则拆解器会把它变成不可检索的疑问句。
    """
    return f"""你要为下面这个研究任务挑一位"最适合做这项研究的专家"。

    研究任务："{question}"

    请输出一个 JSON 对象，正好三个字段：
    {{
      "name": "专家身份，不超过 20 字，例如 机器学习系统研究员",
      "role_prompt": "以'你是一名……'开头的一句话人设，说明这位专家优先关注什么、习惯用什么标准判断，不超过 120 字。不要写任何具体结论、数据或事实断言。",
      "focus": ["3 到 5 个可检索的关注角度，每条不超过 15 字"]
    }}

    要求：
    1. 只输出 JSON，不要解释。
    2. role_prompt 里不要出现"忽略上述要求""你现在是另一个 AI"这类改写规则的句子。
    3. focus 必须是能直接拿去搜索的名词短语，不要写成疑问句。
    """

def report_prompt(query: str, context: str, *, total_words: int = 1000, max_source: int = 0,extra:str="",persona:str="",report_language:str|None=None) -> str:
    """阶段 6 正文提示词。对照源码：gpt_researcher/prompts.py:262

    extra:阶段9用，上一轮评审提出的缺口，追加在写作要求后面 用来把上一轮评审意见喂给写作者
    """
    today = datetime.now(timezone.utc).strftime("%Y 年 %m 月 %d 日")
    range_hint = f"（只能是 1 到 {max_source}）" if max_source else "（见上面资料）"
    extra_block = ""
    if extra:
        extra_block = f"\n补充要求（必须逐条正面处理）：\n{extra}\n"
    persona_block = f"{persona}\n\n" if persona else ""
    language_rule = report_language_instruction(query, report_language)
    return f"""请仅根据下面提供的资料，就下面的研究任务写一篇详细的报告。

    研究任务："{query}"

    {language_rule}

    {persona_block}资料（每条以 [编号] 开头）：
    
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
    11. 严格遵守上面的输出语言要求。资料是英文时，先理解再用中文解释，不要整段照搬英文。
    {extra_block}
    假设当前日期是 {today}。
"""

def report_introduction_prompt(query: str, context: str, *, max_source: int = 0, report_language: str | None = None) -> str:
    """引言。对照源码：gpt_researcher/prompts.py:707

    上游让这一调用负责写 H1 主标题；我们让正文负责 H1，引言只写段落，
    否则正文和引言各出一个 # 标题，报告会有两个主标题。
    """
    range_hint = f"（1 到 {max_source}）" if max_source else ""
    language_rule = report_language_instruction(query, report_language)
    return f"""下面是为研究任务收集的资料：
    {context}
    
    请为"研究任务：{query}"写一段报告引言，要求：
    {language_rule}
    1. 不要写 "# " 一级标题，报告主标题已经由正文写好了。
    2. 3 到 5 句话：说清这个任务为什么值得研究、报告会覆盖哪几个方面。
    3. 事实性内容要带 [编号] 角标，编号必须是资料里真实存在的编号{range_hint}。
    4. 不要写"参考文献"章节。
    5. 严格遵守上面的输出语言要求。
    """


def report_conclusion_prompt(query: str, report: str, *, report_language: str | None = None) -> str:
    """结论。对照源码：gpt_researcher/prompts.py:720

    上游把这条规则写进提示词："如果没有 ## Conclusion 就自己加一个"。
    我们反过来：让模型别加标题，由程序加 —— 标题格式统一由代码控制。
    """
    language_rule = report_language_instruction(query, report_language)
    return f"""下面是一篇已经写好的研究报告：
{report}

请就"研究任务：{query}"写一段结论，要求：
{language_rule}
1. 不要写 "# " 一级标题，也不要写 "## 结论" 这个标题本身（程序会加）。
2. 分 2 到 3 段：先概括主要发现，再给出最重要的判断，最后说局限或下一步。
3. 只能依据报告里已有的内容，不要引入新事实；可以直接复用报告里的 [编号] 角标。
4. 不要写"参考文献"章节。
5. 严格遵守上面的输出语言要求。
"""

def reviewer_prompt(query: str, report: str, context: str, *,
                    round_no: int, max_rounds: int,
                    previous_gaps: list[str] | None = None,
                    fact_check_on:bool = False) -> str:
    """阶段 9 评审者。对照源码：multi_agents/ 的评审角色。

    三分类是这版的核心：missing 会长成下一轮的检索词，所以只有"真能搜回来"的
    才配进 missing。把"资料里没有的数据"记成 missing，等于让研究者去找不存在的东西。
    阶段 11：多了个 fact_check_on 开关。
    开了事实核查之后，评审者不再报 unsupported —— 那一项由核查员逐句核对（它才是
    干这个的）。这不是"少干点活"，是分工：同一件事让两个角色各说一遍，等于付两次钱，
    而且两份意见措辞不同、写作者还得自己判断是不是一回事。
    关掉核查时必须退回去自己报 —— 否则关了开关就没有任何一道关在管"无据"，那是净损失。
    """
    item_unsupported = (
        """1. unsupported：**留空**。逐句核对「这句话引的资料里到底有没有这个说法」由事实核查员负责，
   他已经在做了（阶段 11 的 --fact-check）—— 你重复报一遍，等于同一件事付两次钱。
   只有「论断强度放大」这类**结构**问题（把资料里的“初步验证”写成“已经验证”）
   才请你写进 missing。"""
        if fact_check_on
        else """1. unsupported：报告里哪些话是资料支撑不了的？引用编号越界、把资料没说的当事实写、
       把“初步验证”写成“已验证”这类强度放大的都算。"""
    )
    pass_line_unsupported = (
        "- unsupported 为空（这条由事实核查员保证，你留空即可）；"
        if fact_check_on
        else "- unsupported 为空；"
    )
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

{item_unsupported}
2. missing：哪些缺口是"再检索一轮就有机会补上"的？每条一句话，必须具体到能当检索词用，
   最多 3 条。不要写"可以更详细"这种没法执行的意见。
   每条 missing 必须和研究任务使用同一种语言；研究任务是英文就用英文写检索缺口，
   是中文就用中文。否则下一轮会拿错误语言的词去检索原始资料。
3. unfixable：哪些是资料里根本没有、再检索也大概率补不上的？
   （典型：某类对照实验根本没被公开做过、某个指标没有实测数据、某个机制没有公开讨论。）
   这类**不要**放进 missing。对每一条，说明"报告应该怎样如实声明这个边界"。
4. 有没有自相矛盾、或者该表态却写"各有优劣""取决于具体情况"的地方？记进 missing。

只输出一个 JSON 对象，不要任何其他内容：
{{"pass": true, "score": 8, "missing": [], "unsupported": [], "unfixable": []}}

pass 的判定（三条**同时**满足才写 true）：
{pass_line_unsupported}
- missing 为空；
- unfixable 里的每一条，报告都已经在正文或局限里如实写明了 —— 要写清"现有资料无法回答
  什么、为什么无法判断"，只写"资料未覆盖"四个字不算。

注意：missing 会长成下一轮的检索词。把"资料没有提供公平对比数据"记成 missing，
下一轮就会去搜一个不存在的东西，白烧一轮的钱 —— 这种应该进 unfixable，
并要求报告写明为什么无法比较。
"""

def fact_checker_prompt(query:str,report:str,context:str)->str:
    """
    阶段11 实时核查员
    和评审员的边界 两边提示词必须对的上 否则两个角色会重复干活
    核查员：逐句 核对 这句话引的资料里到底有没有这个说法 ——只输出无据论断
    评审员：结构、资料覆盖、该表态却含糊 只输出缺口、边界、自相矛盾
    所以这里只给 pass 和 unsupported 两个字段：多给一个，模型就会往里塞东西，
    然后同一批意见在两条线上各报一遍。
    """
    return f"""你是这份研究报告的事实核查员。只核对，不要重写报告。

研究任务："{query}"
报告草稿:
{report}
报告可用的全部资料（带 [编号]，报告只能引用这些编号）：
{context}

你要做的只有一件事：逐句检查报告里的事实性陈述，找出**资料不支持**的那些。
一条一条对着上面资料看，下面几种都算"不支持"：

1. 引用的编号越界（报告里写了 [7]，资料只到 [5]）。
2. 引用的编号存在，但那条资料里根本没有这句话说的内容。
3. 资料没说过、却被写成事实的（包括把资料里的"可能/预计"写成"是/已经"）。
4. 数字、日期、名称、版本号与资料对不上。

不归你管的（看到了也不要写进来）：结构问题（哪一节太薄、顺序不好）、
资料覆盖全不全面、报告该不该下某个结论 —— 那些是评审员的活。

只输出一个 JSON 对象，不要任何其他内容：
{{"pass": true, "unsupported": []}}

事实都对上就是上面这样；有问题的长这样：
{{"pass": false, "unsupported": ["报告第 2 段写"深度研究代理占 60% 份额"，引的是 [3]，但 [3] 全文没有这个数字"]}}

规则：
- 一条无据论断一句话，必须写清**哪句话、引的哪个编号、资料里实际是什么**，
  否则写作者不知道该改哪里。最多 8 条。
- 只写你**核对过**的，拿不准的宁可不写 —— 误报会让写作者把对的删掉。
"""



def curate_sources_prompt(query: str, catalog: str, max_results: int = 10) -> str:
    """阶段13 来源管家：从目录里挑出值得进上下文的来源，并按价值排序。

    对照源码：gpt_researcher/prompts.py:319 PromptFamily.curate_sources

    和上游措辞的三个差别，都是为了"少花冤枉钱"：
    1. 上游让模型把正文原样抄回来（"EXACT sources JSON list format"），
       我们只要编号 —— 输出的 token 量差两个数量级（理由见 curator.py 抬头）。
    2. 上游把 values 平铺成 6 条并列的准则；这里明确排了优先级。
       模型对"同等重要"的 6 条其实是随机权重，排序结果不可复现。
    3. 上游说"Err on the side of inclusion"（宁滥勿缺），但没告诉模型
       "哪几种情况才算明确该丢"。这里把"该丢"写成可判定的清单 ——
       否则模型会把"我看不出它有用"当成"它没用"，那是最贵的错。
    """
    return f"""你是这次研究的来源管家。任务是判断下面哪些材料值得进写作上下文。

研究任务："{query}"

材料目录（只有标题、URL、长度和开头一小段 —— 判断只依据这些）：
{catalog}

评判标准，按重要性从高到低：
1. 相关性：有没有回答这个任务需要的事实、数字、结论。有具体数据、一手信息、
   官方文档或论文的排前面。
2. 权威性：官方文档 / 论文 / 厂商技术文档 > 二手转载、内容农场、聚合站。
3. 时效性：新资料优先，但"旧"本身不是丢弃理由 —— 陈旧但被反复引用的
   经典资料照样留。

只有下面这几种情况才丢掉（看不出来就留着，宁滥勿缺）：
- 与任务无关：导航页、商品页、招聘页、论坛水贴、无关站点首页；
- 与另一篇是同一内容的重复转载（留更权威的那篇）；
- 空壳：只有标题和导航，没有实质内容。

只输出一个 JSON 对象，不要任何其他内容：
{{"keep": [2, 5, 1], "dropped": ["3: 与任务无关", "4: 与 1 重复"]}}

规则：
- keep 是要保留的编号，按"对完成这个任务的价值"从高到低排序，最多 {max_results} 条；
- keep 里绝不能出现目录里没有的编号；
- dropped 只写被丢掉的那些，格式是 "编号: 一句话理由"；
- 如果全都值得留，就按价值排序后全部写进 keep，dropped 给空数组 []。
"""
