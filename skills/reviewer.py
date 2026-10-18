"""评审技能：草稿 -> Verdict。

只包了一层：真正的提示词和 JSON 兜底在 agents.review_draft 里。
放进来是为了让"评审"和"写作"在门面里是平级的两个技能 ——
将来换评审策略（比如换成多评审投票）只需要换这个类。
"""

from __future__ import annotations

from typing import Any

from skills.base import Skill


class ReviewerSkill(Skill):
    name = "reviewer"

    def review(self, state: Any):
        import agents

        verdict = agents.review_draft(state, self.cfg)
        self.ctx.emit("review.done", passed=verdict.passed, score=verdict.score)
        return verdict