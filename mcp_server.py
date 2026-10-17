"""
阶段14 MCP server 把这个研究代理暴露给别的AI应用
跑法：
    python mcp_server.py                     # stdio，给本地客户端当子进程拉起来
    python mcp_server.py --transport streamable-http   # 起 HTTP，默认 http://127.0.0.1:8000/mcp

【红线】stdio 传输下，stdout 就是 JSON-RPC 通道，**只许协议写**。
一行 print / 一个写到 stdout 的日志，就会把协议流冲坏。客户端那边的表现是
"服务器无声无息地挂了"—— 没有报错、没有堆栈，最难查的一种。所以：
  1. 模块顶部就把 logging 绑到 stderr（默认 handler 也是 stderr，但写死才是契约）；
  2. 全文件禁止 print 到 stdout（真的需要输出，用 print(..., file=sys.stderr)）；
  3. check_stage14.py 里有一条专门盯这个：它把 server 当子进程拉起来，
     断言 stdout 上每一行都是合法 JSON-RPC。
对照源码：见 research_tools.py 抬头（上游 server 已拆到 gptr-mcp 独立仓库，
本地只剩文档 + 客户端）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

#必须在 import mcp 任何业务模块
#stream = sys.stderr
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
    stream=sys.stderr,
    force=True,
)

from mcp.server.fastmcp import FastMCP

import research_tools

logger = logging.getLogger("mcp_server")

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8170"))

mcp = FastMCP("gpt-researcher",host=MCP_HOST,port =  MCP_PORT)

@mcp.tool()
def quick_search(query:str,max_results:int=6)->str:
    """快速检索：返回搜索结果的标题/链接/摘要。不抓正文、不成稿，秒级返回。

    想先看看有哪些来源、或者只想拿几条链接时用它；要正文证据用 research_sources。
    """
    return research_tools.quick_search(query,max_results=max_results)

@mcp.tool()
def research_sources(query:str) ->str:
    """检索并抓取网页，返回压缩后的证据（带 [n] 引用编号），但不写报告。

    你想用自己的上下文来组织答案、或只想拿证据做二次推理时用这个。
    """
    return research_tools.research_sources(query)

@mcp.tool()
def deep_research(query:str)->str:
    """端到端深度研究：拆子问题 -> 并发检索 -> 抓正文 -> 压缩 -> 出一篇带引用的完整报告。

    这是最慢的一个（一般 30~90 秒），也是质量最高的一个。报告会同时落盘到 outputs/。
    """
    return research_tools.deep_research(query)

@mcp.prompt()
def research_query(topic:str)->str:
    """把一句主题，改成一条更适合交给deep_research的研究任务"""
    return (
        f"请对下面这个主题做一次深度研究，并输出带引用的报告：{topic}\n"
        "要求：覆盖关键事实、具体数字与来源差异；不确定的地方要明说，不要编。"
    )
#让同一个MCP Server 支持两种通信方式，由启动参数决定使用哪种
#argv 允许外部传入参数（方便测试）
def main(argv:list[str]| None=None) -> int:
    parser = argparse.ArgumentParser(description="gpt-research MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio","streamable-http"),
        default="stdio",
        help="stdio= 本地客户端拉起子进程（默认）；"
            "streamable-http = 起HTTP服务供远程调用"
    )
    args = parser.parse_args(argv)
    if args.transport == "streamable-http":
        #注意这里也走stderr stdio
        # 注意这里也走 stderr。stdio 下连这行都没有。
        print(
            f"MCP server listening on http://{MCP_HOST}:{MCP_PORT}/mcp", file=sys.stderr
        )
    logger.info("启动 MCP server：transport=%s", args.transport)
    mcp.run(transport=args.transport)
    return 0

if __name__ == "__main__":
    sys.exit(main())