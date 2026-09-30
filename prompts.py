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
