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