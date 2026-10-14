"""URL 安全校验（阶段 12）：SSRF 与本地文件读取防护。

对照源码：gpt_researcher/utils/url_security.py（125 行，几乎是照着搬）

为什么需要它：在这之前，被抓的 URL 只有两种来源 —— 我们自己拆的子问题、
搜索引擎返回的结果，看着"都是公网"。但只要有一天 URL 来自外部输入
（API 参数、source_urls、后面阶段 14 的 MCP server 入参），
http://169.254.169.254/latest/meta-data/ 就是一个能直接读走云主机凭证的洞。
防线要建在"凡是外部来的 URL 都校验"这一层，而不是"我确定它是公网"这一层。

拦掉三类：
  1. 非 http/https 协议（file:// / ftp:// / gopher:// / javascript:）；
  2. 没协议也没主机的本地路径（/etc/passwd）—— 有些文档加载器会直接读硬盘；
  3. 解析到内网的地址：回环、私有网段、链路本地（含 169.254.169.254 云元数据）、
     保留段、多播、未指定。

上游在这个文件里留了一句很重要的自我说明，我们连结论一起搬过来：
  校验时解析一次域名、真正发请求时 HTTP 客户端会再解析一次，中间留了一个
  DNS rebinding 的窗口。要彻底关掉得把校验出的 IP 钉进连接里（自定义
  HTTPAdapter）。所以这里定位是**纵深防御** —— 把"随手就能打的"那批洞堵掉，
  而不是宣称从此免疫。能声明就别猜，这句话写在代码里，不留在脑子里。

例外：要抓内网自建服务的，设 ALLOW_PRIVATE_URLS=true 或传 allow_private=True。
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")
_TRUTHY = ("1", "true", "yes", "on")

class UnsafeURLError(ValueError):
    """URL 被安全策略拒绝（协议不对 / 没有主机 / 指向内网）。"""

class HostNotResolvable(UnsafeURLError):
    """主机名解析不出来。

    刻意从 UnsafeURLError 派生，但单独一类：它不是"不安全"，是"够不着"。
    调用方要按网络错误处理（可重试），而不是当成策略拒绝（不可重试）——
    混成一类，一个 DNS 抽风的域名就会被写进 pipeline 的负缓存，从此永久跳过。
    """

def _private_allowed() -> bool:
    return os.getenv("ALLOW_PRIVATE_URLS", "").strip().lower() in _TRUTHY

def _is_internal(ip) -> bool:
    """这个地址该不该被我们访问。"""
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )

def validate_url(url: str, *, allow_private: bool | None = None) -> str:
    """校验 url 可以安全访问，原样返回它；不合格抛 UnsafeURLError。

    为什么返回 url 而不是 True/False：调用点写成 `safe = validate_url(raw)`，
    传下去的是"校验过的那个值"，而不是"我校验过另一个变量"——
    少一次拿错变量的机会。
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL 不能为空")

    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"协议 {scheme or '(没有协议)'!r} 不允许，只接受 http/https"
        )
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL 里没有主机名")

    if allow_private is None:
        allow_private = _private_allowed()
    if allow_private:
        return url

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HostNotResolvable(f"{host}（{exc}）") from exc

    # 一个域名可能有好几条 A/AAAA 记录，任何一条落在内网都拒绝 ——
    # 只看第一条等于把决定权交给 DNS 的返回顺序。
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise UnsafeURLError(f"主机 {host} 解析出了非法地址 {ip_str!r}") from exc
        if _is_internal(ip):
            raise UnsafeURLError(
                f"主机 {host} 指向内部地址 {ip_str}"
                f"（要抓内网服务就设 ALLOW_PRIVATE_URLS=true）"
            )
    return url

def is_safe_url(url: str, *, allow_private: bool | None = None) -> bool:
    """validate_url 的布尔版，用来问"能不能抓"而不是直接拦。"""
    try:
        validate_url(url, allow_private=allow_private)
        return True
    except UnsafeURLError:
        return False