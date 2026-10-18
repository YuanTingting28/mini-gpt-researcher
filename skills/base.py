"""技能层的地基：运行上下文 + 技能基类
阶段16：要解决的是 编排和实现 湖在一起
pipeline.full_research 一个函数里 既有 先拆再搜 再压再写 “的顺序
又有每一步的参数和降级处理

拆开之后
    -技能 只回答 这一步怎么做 一个输入 一个输出 失败则往上推
    -门面 只回答 “什么时候做、失败了怎么办”
技能之间不相互调用，也不持有门面，只拿一个RunContext
依赖方向永远是单向的：门面-》技能-》基础模块
这样任何一个技能都能在自检里单独实例化、单独替换
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from config import Config

logger = logging.getLogger(__name__)

EventHandler = Callable[[str,dict[str,Any]], None]

@dataclass
class RunContext:
    """一次研究运行的共享上下文：配置 + 事件流"""
    cfg:Config
    on_event:EventHandler | None = None
    events:list[dict[str, Any]] = field(default_factory=list)
#这里记录日志
    def emit(self,event:str,**payload:Any) -> None:
        """记一条事件 并 通知外部

        回调里的异常一律吞掉，事件流是”可观测性“，不是主链路
        让一个打印日志的回调把整次研究搞挂，是本末倒置。
        """
        record = {"event":event,**payload}
        self.events.append(record)
        if self.on_event is None:
            return
        try:
            self.on_event(event,payload)
        except Exception as exc:
            logger.warning("事件回调失败（已忽略，不影响主流程）：%s", exc)

class Skill:
    """所有技能的基类，只持有RunContext,不持有门面"""

    name:str = "skill"
    def __init__(self,ctx:RunContext) -> None:
        self.ctx = ctx
    @property
    def cfg(self)->Config:
        return self.ctx.cfg