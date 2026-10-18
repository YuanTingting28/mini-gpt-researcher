"""压缩技能：页面列表 -> 带 [n] 编号的上下文。

为什么查询词是"原问题 + 子问题"拼起来的：子问题是模型按检索需要生成的，
比原问题更贴近资料里的措辞，BM25 命中率更高。
"""

from __future__ import annotations

from typing import Any

import pylab as p

from skills.base import Skill

class CompressorSkill(Skill):
    name = "compressor"

    def build(self,question:str,sub_queries:list[str],pages:list[dict[str,Any]])->str:
        import context
        filter_query = " ".join([question,*sub_queries])
        raw_chars = sum(len(p.get("raw_content") or "") for p in pages)
        self.ctx.emit("compress.start", pages=len(pages), raw_chars=raw_chars)
        text = context.select_context(filter_query, pages, cfg=self.cfg)
        self.ctx.emit("compress.done", context_chars=len(text))
        return text