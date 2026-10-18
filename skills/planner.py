"""拆解技能：把问题拆成可独立检索的子问题。

阶段16 这一版是"委派"——实现还在 pipeline.plan_research 里。
为什么用 `import pipeline` 再取属性，而不是 from-import 函数：
现有自检靠"在 pipeline 上打桩"来跑离线测试（pipeline.plan_research = fake_plan），
模块属性访问能保证那些打桩点继续生效。重构不该顺手把测试基础设施推翻。
"""

from __future__ import annotations

from typing import Any

from skills.base import Skill

class PlannerSkill(Skill):
    name = "planner"

    def plan(self,question:str,*,expert:Any=None)->list[str]:
        import pipeline
        self.ctx.emit("plan.start",question=question)
        sub_queries = pipeline.plan_research(question,cfg=self.cfg,expert=expert)
        self.ctx.emit(
            "plan.done", count=len(sub_queries), sub_queries=list(sub_queries)
        )
        return sub_queries