"""正文抓取：把 URL 变成可引用的文本。

对照源码：gpt_researcher/scraper/scraper.py（305 行）
    - Scraper(109) / run(142)          并发抓一批
    - extract_data_from_url(194)       单页抓取
    - get_scraper(307)                 按 URL 类型选抓取器
    - _looks_like_block_page(73)       判断抓回来的是不是验证码/登录墙
    - _looks_like_word_list(78)        判断是不是词表垃圾
    - _looks_like_unextracted_pdf(102) 判断是不是没解析的 PDF

    这三个 _looks_like_* 判断是整份文件最实用的部分：
    抓取失败最怕的不是报错，是"抓到了一堆垃圾但流程以为成功"。

辅助阅读：scraper/beautiful_soup/beautiful_soup.py（80 行）、scraper/utils.py（201 行）
 本阶段做的事第三件最重要：
 1。拿回HTML
 2.从HTML里抽出正文
 3.判断抽出来的东西是不是正文
"""

from __future__ import annotations

from typing import Any

from config import Config, load_config

import logging
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from url_security import HostNotResolvable, UnsafeURLError, validate_url
logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
#这里要判断命中的是不是垃圾页面或者是验证页面
BLOCK_MARKERS = (
    "just a moment",
    "enable javascript",
    "checking your browser",
    "verify you are human",
    "cf-browser-verification",
    "attention required",
    "access denied",
    "captcha",
)
#这里的标签的文字几乎一定是噪音
NOISE_TAGS = (
    "script", "style", "noscript", "nav", "footer", "header",
    "aside", "form", "iframe", "svg", "button",
)
#正文容器，按优先级尝试
MAIN_SELECTORS = (
    "article", "main", "[role=main]", "#content", "#main",
    ".post-content", ".article-content", ".markdown-body",
)

#块级标签。只有它们的边界才是真正的换行。
#用 "\n" 做分隔符会在每一个元素边界插换行，包括 <a> <strong> 这种行内
#标签，句子会被腰斩；而中文 2-gram 按连续汉字串切，换行会打断这个串。
BLOCK_TAGS = (
    "p", "div", "section", "article", "li", "ul", "ol",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "tr", "td", "table",
)

MIN_CONTENT_CHARS = 300

def _looks_like_block_page(text:str)->str|None:
    """拦截页/验证页的典型特征
    只在文本很短时才查关键词
    不应该被误判。
    """
    if len(text)>=1500:
        return None
    low = text.lower()
    for marker in BLOCK_MARKERS:
        if marker in low:
            return f"意思反爬拦截页面(命中{marker!r})"
    return None
def _looks_like_word_list(text:str)->bool:
    """判断抽到的是一堆孤立短行 （导航栏/标签云） 还是连续正文
    注意：原项目用「每行单词数 <= 2」，对中文完全失效 ——
    一整句中文按空格切也只有 1 个「词」，正文会被整段误杀。
    这里改成按字符长度，中英文都成立。
    """
    lines = [line for line in text.splitlines() if line]
    if len(lines)<25:
        return False
    short = sum(1 for line in lines if len(line) <= 12)
    return short / len(lines) > 0.6 and len(text) < 6000

def _extract_text(node)->str:
    """把 DOM 取成文本，只在真正的块级边界换行。

    实测：段落里只要有一个行内标签，用 "\n" 做分隔符就会把句子切成三行 ——
    "旨在对<a>各种任务</a>。" 会变成 "旨在对" / "各种任务" / "。该代理..."。
    而中文 2-gram 按连续汉字串切，换行会打断这个串，跨行的 bigram 永远
    切不出来。不只是难看，是真的丢信息。

    对照源码：gpt_researcher/scraper/beautiful_soup/beautiful_soup.py
    """
    for br in node.find_all("br"):
        br.replace_with("\n")

    # 只给「叶子块级元素」补换行：自身不再包含块级子元素的块。
    # 这样 <div><p>a</p><p>b</p></div> 不会多出一堆空行，
    # 而 <div>直接放文字</div> 这种写法也能正确分段。
    for tag in node.find_all(BLOCK_TAGS):
        if tag.find(BLOCK_TAGS) is None:
            tag.append("\n")

    text = node.get_text("")                      # 行内元素之间不插东西
    text = re.sub(r"[ \t\u00a0]+", " ", text)     # 合并空白，保留换行
    text = re.sub(r"\n\s*\n+", "\n", text)        # 合并连续换行
    return text.strip()


def _parse_html(html:str)->tuple[str,str]:
    """

    :param html:  页面
    :return:(标题,正文文本)
    """
    soup = BeautifulSoup(html, "lxml") #这里是解析之后的对象
    #标题先去 ——decompose会改变DOM 很多站点的<title>带“-站点名”后缀
    title = ""
    og = soup.find("meta",attrs={"property":"og:title"})
    if og and og.get("content"):
        title = og["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(" ",strip=True)
    title = re.sub(r"\s+", " ", title)[:200] #找到一堆空白字符，全部换成一个空白字符
    #先删噪音标签 不可逆 所以凡在依赖结构之前
    for tag in soup(NOISE_TAGS):
        tag.decompose() #把一些噪音的标签直接删除
    node = None
    for selector in MAIN_SELECTORS: #定位之前的正文标签，找到了就这个 然后加入了回退机制
        node  = soup.select_one(selector)
        if node is not None:
            break
    node = node or soup.body or soup
    return title, _extract_text(node)

def _clean_lines(text:str,min_len:int=3)->str:
    """
    按行清洗，压空白，丢过短行、去重
    去重的理由，导航栏和侧栏会在同一页里反复出现
    原项目utils.py里也有对应的处理
    :param text:
    :param min_len:
    :return:
    """
    lines: list[str] = []
    seen:set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line)<min_len:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)

def _truncate(text:str,max_chars:int) ->str:
    """截断并尽量落在句子边界上，破坏性操作，放在最后"""
    if len(text)<=max_chars:
        return text
    cut = text[:max_chars]
    for sep in ("\n", "。", "！", "？", ". ", ".\n"):
        pos = cut.rfind(sep)
        if pos >= max_chars * 0.6:
            return cut[:pos+len(sep)].rstrip()
    return cut
def _decode(resp: requests.Response) -> str:
    """把响应体解成文本。

    requests 只看 Content-Type 或 <meta charset> 里声明的编码。
    中文站经常声明错（把 GBK 写成 ISO-8859-1），这时就会乱码，
    所以要交给 charset_normalizer 猜一次。

    注意别无条件用 apparent_encoding —— 它对长文本要跑统计，
    而且对声明正确的页面反而可能猜错。只在声明缺失或明显可疑时才猜。
    """
    declared = (resp.encoding or "").lower()
    if declared and declared not in ("iso-8859-1", "ascii", "latin-1"):
        return resp.text
    return resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")

# ==================== 阶段 12：SSRF 防护 + PDF 正文抽取 ====================
#单个响应体的硬上限（字节） Content-Type 和 Content-Length 都是服务器说了算，
#: 只有这个是本地说了算的 —— 所以字节也得自己数（见 _read_body）。
DEFAULT_MAX_BODY_BYTES = 30 * 1024 * 1024
#: 最多跟几跳重定向。requests 默认 30 跳，对研究场景太宽松。
MAX_REDIRECTS = 5
#: PDF 的内部结构标记。它们本身也是 ASCII，所以在"被当成网页解出来的垃圾文本"里
#: 同样找得到 —— 但真实散文里几乎不会同时出现三个，所以≥3 个就能认定是 PDF 语法。
_PDF_MARKER_BYTES = (b"endobj", b"endstream", b"/FlateDecode", b"xref", b"trailer")

def _pymupdf():
    """拿到PyMuPDF import名是fitz模块 没装返回 None
     为什么不用上游那套 langchain_community.PyMuPDFLoader：为了"逐页取文本"
    这一件事要拖进整条 langchain 依赖链。直接调 fitz 是十几行。
    """
    try:
        import fitz
    except ImportError:
        return None
    return fitz

def _pdf_confidence(body:bytes)->int:
    """0=不像PDF,1=结构标记像，2=文件头，板上钉钉
    为什么分两档：字节里出现 PDF 的内部语法（endobj/xref/trailer）还有另一种
    可能 —— 这是一篇**讲 PDF 格式的文章**，正文里就写着这些词。
    所以标记只能算"像"，只有 %PDF- 文件头才算"是"。
    这个区分决定了解析失败时是"报错"还是"退回按网页解析"，见 fetch。

    """
    if body[:5] == b"%PDF-":
        return 2
    hits = sum(1 for marker in _PDF_MARKER_BYTES if marker in body)
    return 1 if hits >= 3 else 0

def _sniff(content_type:str,confidence:int)->str:
    """这批字节该按哪种文档解析？返回 'pdf' / 'html' / 'binary'。

    顺序是有讲究的：先看字节（服务器改不了），再看 Content-Type（服务器说了算）。
    这里**故意不看 .pdf 后缀** —— 后缀是最弱也最容易撒谎的信号（一个路由就能
    叫 /x.pdf），而真 PDF 的文件头或 application/pdf 总有一个会中。
    上游是多一层"后缀选抓取器 + 文本兜底重抓"，我们把判断做在字节上，一层就够。
    """
    if confidence:
        return "pdf"
    ctype = (content_type or "").lower()
    if "application/pdf" in ctype:
        return "pdf"
    if ctype and not any(k in ctype for k in ("html", "xml", "text")):
        return "binary"
    return "html"

def _extract_pdf(body:bytes,fitz) ->tuple[str,str,str | None]:
    """
    pdf 字节 ——》 （标题、正文、失败原因） 失败原因非None 表示没抽出来。
    逐页全取 、标题只从metadata拿。
    取所有页面。
    论文首页通常只有标题和作者，只取第一页会得到几十字的正文
    然后被正文过短的健康检查判死

    :param body:
    :param fitz:
    :return:
    """
    try:
        doc = fitz.open(stream=body,filetype="pdf")
    except Exception as exc:
        return "", "", f"{type(exc).__name__}: {str(exc)[:120]}"

    try:
        if not getattr(doc,"is_pdf",True):
            #PyMuPDF 是对非PDF字节是宽容的，一段HTML也能打开成功
            return "","","内容不是PDF （PyMuPDF 识别为 %s)" %(
            (doc.metadata or {}).get("format") or "未知格式"
            )
        if getattr(doc, "needs_pass", False):
            return "", "", "文档已加密，需要密码"
        pages = [page.get_text("text",sort=True) for page in doc]
        title = (doc.metadata or {}).get("title") or ""
        return str(title).strip(),"\n".join(pages),None
    except Exception as exc:
        return "", "", f"抽文字失败 {type(exc).__name__}: {str(exc)[:120]}"
    finally:
        doc.close()

def _read_body(resp,*,max_bytes:int)->bytes:
    """流式读响应体，超过上限当场教廷
    为什么不用 resp.content：那是无条件的一次性全量读取。服务器可以把
    Content-Length 写成 1KB 然后推 2GB，也可以用 chunked 干脆不写长度 ——
    前者我们能从声明里看出苗头，后者只能在读的过程中发现。所以两条都要查。
    """
    declared = (resp.headers.get("Content-Length") or "").strip()
    if declared.isdigit() and int(declared) > max_bytes:
        raise ValueError(f"响应体过大（声明 {int(declared)} 字节 > 上限 {max_bytes}）")
    chunks:list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=65536): #用流式读 而不是读完整的
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"响应体过大（已读 {total} 字节 > 上限 {max_bytes}）")
        chunks.append(chunk)
    return b"".join(chunks)

def _body_response(body:bytes,content_type:str,*,url:str="")->requests.Response:
    """把已经读下来的字节包装成一个最小的Response
    为什么不另写一份"从 bytes 判编码"：GBK 被声明成 ISO-8859-1、声明整个缺失
    这些坑踩过一次就够了，两份实现迟早会分叉 —— _decode 里用的是
    resp.encoding + resp.apparent_encoding 这套 requests 自己的判定。
    """
    fake = requests.Response()
    fake.status_code = 200
    fake.url = url
    fake.headers["Content-Type"] = content_type or ""
    fake._content = (
        body  # 私有属性；requests 自己也拿它当缓存，见 check_decode.py 同样的做法
    )
    fake.encoding = requests.utils.get_encoding_from_headers(fake.headers)
    return fake

def _get_safely(client, url: str, *, headers: dict, timeout: float):
    """带逐跳校验的 GET，返回最终那一跳的响应。

    为什么不用 allow_redirects=True：requests 只认第一跳。一个公网 URL
    302 到 http://169.254.169.254/ 它就照跳，SSRF 防护形同虚设。
    所以这里关掉自动跳转，自己一跳一跳地校验（顺手限制跳数）。
    每一跳都过 validate_url —— 这才是"外部给的 URL 一律不信任"。
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        validate_url(current)
        resp = client.get(
            current, headers=headers, timeout=timeout,
            allow_redirects=False, stream=True,
        )
        location = resp.headers.get("Location")
        if resp.status_code in (301, 302, 303, 307, 308) and location:
            resp.close()
            current = urljoin(current, location)   # Location 可能是相对路径
            continue
        return resp
    raise UnsafeURLError(f"重定向超过 {MAX_REDIRECTS} 跳，放弃")

def fetch(url: str, *, cfg: Config | None = None,session:requests.Session |None = None) -> dict[str, Any]:
    """抓一个 URL，返回 {url, title, raw_content}。
    返回 {"url", "title", "raw_content", "ok", "reason", "status"}。
    任何失败都通过 ok=False + reason 表达，不抛异常 ——
    阶段 8 会一次抓几十个链接，一个链接炸掉不能拖垮整轮。
    TODO(阶段 4)：

      2. 非 200 或 content-type 不是 text/html -> 返回 raw_content=""
      3. BeautifulSoup(html, "lxml")，先干掉 script/style/nav/footer/header/aside
      4. 取标题：soup.title.string
      5. 取正文：优先 <article>/<main>，没有就退回全页 get_text，
         用 "\n".join(line.strip() for line in text.splitlines() if line.strip()) 压掉空行
      6. 加三个健康检查：正文太短(< 200 字) / 像验证码页 / 像词表 -> 视为抓取失败
      7. 任何异常都返回 raw_content=""，别让一个坏链接炸掉整轮研究\

    阶段 12 的两件事：
      1. 请求之前、以及**每一次重定向之前**都过一遍 SSRF 校验（见 url_security）；
      2. 拿回来的是 PDF 就抽正文，而不是当成"不支持的格式"丢掉。
    """
    #1. requests.get(url, timeout=cfg.scrape_timeout, headers={"User-Agent": ...})
    cfg = cfg or load_config()
    out:dict[str,Any] = {
        "url":url,"title":"","raw_content":"","ok":False,"reason":"","status":0,"transient":False
    }
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    client = session or requests
    max_bytes = getattr(cfg, "scrape_max_bytes", DEFAULT_MAX_BODY_BYTES)

    try:
        resp = _get_safely(client, url, headers=headers, timeout=cfg.scrape_timeout)
    except HostNotResolvable as exc:
        out["reason"] = f"域名解析失败：{exc}"
        out["transient"] = True  # DNS 抽风是暂时的，别写进负缓存
        return out
    except UnsafeURLError as exc:
        out["reason"] = f"拒绝不安全的 URL：{exc}"
        return out  # 不是 transient：重试一万次它还是内网地址
    except requests.Timeout:
        out["reason"] = f"超时（>{cfg.scrape_timeout}s）"
        out["transient"] = True
        return out
    except requests.RequestException as exc:
        out["reason"] = f"网络错误：{type(exc).__name__}: {str(exc)[:150]}"
        out["transient"] = True
        return out
    try:

        out["status"] =resp.status_code
        out["url"] = resp.url or url
        ctype = resp.headers.get("Content-Type") or ""
        try:
            body = _read_body(resp,max_bytes=max_bytes)
        except ValueError as exc:
            out["reason"] = str(exc)
            return out
        if resp.status_code >= 400:
            out["reason"] = f"HTTP {resp.status_code}"
            return out

        #决定按哪种文档解析
        confidence = _pdf_confidence(body)
        kind = _sniff(ctype,confidence)
        if kind == "binary":
            out["reason"] = f"非网页内容（Content-Type: {ctype.split(';')[0].strip()}）"
            return out
        used_pdf = False
        if kind == "pdf":
            fitz_mod = _pymupdf()
            if fitz_mod is None:
                # 装完依赖再跑就行 —— 算 transient，别写进负缓存
                out["reason"] = "这是 PDF，但没装 pymupdf（pip install pymupdf）抽不了文字"
                out["transient"] = True
                return out
            title, text, err = _extract_pdf(body, fitz_mod)
            if err is None:
                used_pdf = True
            elif confidence == 2:
                # 文件头就是 %PDF-：抽不出来就是抽不出来，不许退回当网页糊过去
                out["reason"] = f"PDF 抽取失败：{err}"
                return out
            else:
                # 只有结构标记像：大概率是"讲 PDF 格式的文章"，按网页解才对
                logger.info("字节里有 PDF 结构标记但不是文件头（%s），按网页解析：%s",
                            err, resp.url)
        if not used_pdf:
            title, text = _parse_html(
                _decode(_body_response(body, ctype, url=resp.url or url))
            )
        text = _clean_lines(text)
        out["title"] = title
        if used_pdf and not text:
            out["reason"] = "PDF 里没有可抽取的文字（可能是扫描件，本阶段不做 OCR）"
            return out

        #检查可能是拦截
        reason = _looks_like_block_page(text)
        if reason:
            out["reason"] = reason
            return out
        #检查可能是一堆孤立的短行
        if _looks_like_word_list(text):
            out["reason"] = "疑似导航/标签云，未取到正文"
            return out

        # 检查 4：太短，没有信息量
        if len(text) < MIN_CONTENT_CHARS:
            out["reason"] = f"正文过短（{len(text)} 字符）"
            return out
        #填充信息
        out["raw_content"] = _truncate(text, cfg.scrape_max_chars)
        out["ok"] = True
        return out
    finally:
        resp.close()


def fetch_many(urls: list[str], *, cfg: Config | None = None) -> list[dict[str, Any]]:
    """批量抓取。

    TODO(阶段 4)：先用串行跑通；阶段 8 再换成线程池/异步并发。
    失败的单条丢弃并记录，不要影响其他 URL。
    """
    raise NotImplementedError("阶段 4：实现 fetch_many")
