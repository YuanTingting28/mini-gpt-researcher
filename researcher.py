"""阶段 16：编排门面（Facade）。

对照源码：D:\gpt-researcher\gpt_researcher\agent.py 的 GPTResearcher
    上游那个类也是门面：自己不干活，只组合 7 个 skill
    （context_manager / scraper_manager / source_curator / researcher / writer ...）。

和上游的差别：
    上游的技能对象持有 agent 引用（self.agent），技能反过去读 agent 的状态；
    这里技能只拿 RunContext，状态由门面持有。依赖是单向的
    门面 -> 技能 -> 基础模块，所以技能可以单独实例化测试。

本阶段用的是绞杀者（Strangler Fig）策略：
    新门面在外，旧实现（pipeline.full_research）还在内，先用等价性自检
    证明两条路产出一致，确认后再删旧的。技能内部一律走模块属性调用
    （import pipeline; pipeline._collect_pages），现有自检的打桩点全部保留。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from config import Config, load_config
from skills import (
    CompressorSkill,
    PlannerSkill,
    ReviewerSkill,
    SearcherSkill,
    WriterSkill,
)
from skills.base import RunContext, Skill

logger = logging.getLogger(__name__)

class Researcher:
    """编排门面：只决定“什么时候做什么、失败了怎么办。"""

    def __init__(self,
                 *,
                 cfg: Config | None=None,
                 on_event:Callable[[str,dict[str,Any]],None] | None=None,
                 planner:Any=None,
                 searcher:Any=None,
                 compressor:Any=None,
                 writer:Any=None,
                 reviewer:Any = None,) -> None:
        self.ctx = RunContext(cfg=cfg or load_config(), on_event=on_event)
        # 五个技能都能从外部替换 —— 这是本阶段真正的收益：
        # 想换检索器 / 换写作模型 / 换评审策略，不用改门面一行代码。
        self.planner = planner or PlannerSkill(self.ctx)
        self.searcher = searcher or SearcherSkill(self.ctx)
        self.compressor = compressor or CompressorSkill(self.ctx)
        self.writer = writer or WriterSkill(self.ctx)
        self.reviewer = reviewer or ReviewerSkill(self.ctx)
        # ------------------------------------------------------------ 直线链路
    def run(self,question:str,*,curate:bool | None=None)->dict[str,Any]:
        """拆-》搜-》筛-》压-》写-》存 返回结构与full_research完全一致"""
        cfg = self.ctx.cfg
        import pipeline
        self.ctx.emit("run.start",question=question)
        sub_queries = self.planner.plan(question)
        pages = self.searcher.collect(sub_queries)
        curation:dict[str,Any] | None = None
        if curate is None:
            curate = cfg.curate_sources
        if curate and pages:
            import curator

            self.ctx.emit("curate.start", pages=len(pages))
            curation = curator.curate_pages(question, pages, cfg=cfg)
            if curation["ok"]:
                logger.info(
                    "来源管家：保留 %d/%d 篇", len(curation["pages"]), len(pages)
                )
                self.ctx.emit(
                    "curate.done", kept=len(curation["pages"]), total=len(pages)
                )
            else:
                # 局部失败降级：筛不成不是错误，退回未筛选接着跑
                logger.warning("来源管家没筛成，按未筛选继续：%s", curation["reason"])
                self.ctx.emit("curate.fallback", reason=curation["reason"])
            pages = curation["pages"]

        if not pages:
            # 全局失败停下，但要把原因说清楚
            logger.warning("一个页面都没抓到，不出报告")
            self.ctx.emit("run.stop", reason="no_pages")
            return {
                "report": f'没有为"{question}"抓到任何可用资料',
                "sources": [],
                "cost_report": pipeline._cost_report_hook(cfg),
                "path": None,
                "sub_queries": sub_queries,
                "curation": curation,
            }
        raw_chars = sum(len(p["raw_content"]) for p in pages)
        context_text = self.compressor.build(question, sub_queries, pages)
        logger.info(
            "抓取 %d 页 / %d 字符 -> 上下文 %d 字符",
            len(pages),
            raw_chars,
            len(context_text),
        )
        if not context_text.strip():
            logger.warning("压缩后上下文为空，不出报告")
            self.ctx.emit("run.stop", reason="empty_context")
            return {
                "report": f'没有为"{question}"找到可用上下文',
                "sources": pages,
                "cost_report": pipeline._cost_report_hook(cfg),
                "path": None,
                "sub_queries": sub_queries,
                "curation": curation,
            }

        import report

        sources = report.parse_context_sources(context_text)
        report_text = self.writer.write(question, context_text, sources)
        path = self.writer.save(question, report_text)
        logger.info(
            "报告已写入 %s（%d 字，%d 个来源）", path, len(report_text), len(sources)
        )
        self.ctx.emit(
            "run.done", path=str(path), chars=len(report_text), sources=len(sources)
        )
        return {
            "report": report_text,
            "sources": sources,
            "cost_report": pipeline._cost_report_hook(cfg),
            "path": str(path),
            "sub_queries": sub_queries,
            "curation": curation,
        }
    # ------------------------------------------------------ 多智能体链路
    def editorial(
        self,
        question: str,
        *,
        max_rounds: int = 3,
        ask: Callable[[str], str] | None = None,
        with_fact_check: bool = False,
        dynamic_role: bool = False,
    ) -> dict[str, Any]:
        """编辑部那条链路的门面入口。

        编排本身还在 agents.run_editorial 里（它是个带终止条件的循环，
        比直线链路复杂，搬过来放到阶段17 一起做）。这里先统一入口。
        """
        import agents

        self.ctx.emit("editorial.start", question=question, rounds=max_rounds)
        result = agents.run_editorial(
            question,
            cfg=self.ctx.cfg,
            max_rounds=max_rounds,
            ask=ask,
            with_fact_check=with_fact_check,
            dynamic_role=dynamic_role,
        )
        self.ctx.emit(
            "editorial.done", rounds=result.get("rounds"), path=result.get("path")
        )
        return result
