"""检索技能：子问题 -> 去重后的页面列表。

这里只负责"要哪些页面"。并发、去重、单条失败跳过都在 pipeline._collect_pages 里，
阶段17 要换检索后端时，改的是那一层，不是这里。
"""

from __future__ import annotations

from typing import Any

from skills.base import Skill

class SearcherSkill(Skill):
    name:str = "searcher"

    def collect(self,sub_queries:list[str]) -> list[dict[str,Any]]:
        import pipeline

        self.ctx.emit("search.start",targets=len(sub_queries))
        pages = pipeline._collect_pages(sub_queries,cfg=self.cfg)
        self.ctx.emit("search.done",pages=len(pages))
        return pages
