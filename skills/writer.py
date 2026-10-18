"""写作技能：上下文 -> 报告 -> 落盘。

write 和 save 拆成两个方法，是因为多轮编辑部里每轮都要 write，
但只有收工那次才 save。技能方法只做一件事，调用方才有得选。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skills.base import Skill


class WriterSkill(Skill):
    name = "writer"

    def write(
        self, question: str, context_text: str, sources: list[dict[str, Any]]
    ) -> str:
        import report

        self.ctx.emit("write.start", sources=len(sources))
        text = report.write_report(question, context_text, sources, cfg=self.cfg)
        self.ctx.emit("write.done", chars=len(text))
        return text

    def save(self, question: str, text: str) -> Path:
        import report

        path = report.save_report(question, text)
        self.ctx.emit("save.done", path=str(path))
        return path