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
"""

from __future__ import annotations

from typing import Any

from config import Config, load_config


def fetch(url: str, *, cfg: Config | None = None) -> dict[str, Any]:
    """抓一个 URL，返回 {url, title, raw_content}。

    TODO(阶段 4)：
      1. requests.get(url, timeout=cfg.scrape_timeout, headers={"User-Agent": ...})
      2. 非 200 或 content-type 不是 text/html -> 返回 raw_content=""
      3. BeautifulSoup(html, "lxml")，先干掉 script/style/nav/footer/header/aside
      4. 取标题：soup.title.string
      5. 取正文：优先 <article>/<main>，没有就退回全页 get_text，
         用 "\n".join(line.strip() for line in text.splitlines() if line.strip()) 压掉空行
      6. 加三个健康检查：正文太短(< 200 字) / 像验证码页 / 像词表 -> 视为抓取失败
      7. 任何异常都返回 raw_content=""，别让一个坏链接炸掉整轮研究
    """
    raise NotImplementedError("阶段 4：实现 fetch")


def fetch_many(urls: list[str], *, cfg: Config | None = None) -> list[dict[str, Any]]:
    """批量抓取。

    TODO(阶段 4)：先用串行跑通；阶段 8 再换成线程池/异步并发。
    失败的单条丢弃并记录，不要影响其他 URL。
    """
    raise NotImplementedError("阶段 4：实现 fetch_many")