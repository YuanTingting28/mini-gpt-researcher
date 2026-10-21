"""阶段 19 离线自检：本地 PDF / 文本索引与检索。"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
import sys
from contextlib import contextmanager
from types import SimpleNamespace

_ROOT = pathlib.Path(__file__).resolve().parent
while not (_ROOT / "document").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))
logging.disable(logging.WARNING)

from config import Config, _env_paths
from document.index import empty_index, index_paths, load_index
from document.loader import iter_files, load_file
from document.retriever import LocalDocumentRetriever
import pipeline

FAILED: list[str] = []
SKIPPED: list[str] = []


def check(name: str, ok: bool, extra: object = "") -> None:
    print(("OK   " if ok else "FAIL ") + f"{name:<42} {extra}")
    if not ok:
        FAILED.append(name)


def skip(name: str, why: str) -> None:
    print(f"SKIP {name:<42} {why}")
    SKIPPED.append(name)


def make_pdf(path: pathlib.Path, pages: list[str]) -> bool:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return False
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(path)
    doc.close()
    return True


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@contextmanager
def temp_workspace():
    # 不用 tempfile.TemporaryDirectory：Windows 上它会把目录建成 0700，
    # 在本项目的沙箱/受控目录里再往下 mkdir 会被 ACL 拒绝。
    root = _ROOT / f".stage19-check-{os.getpid()}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    with temp_workspace() as raw:
        root = pathlib.Path(raw)
        docs = root / "docs"
        docs.mkdir()

        notes = docs / "notes.md"
        write(notes, "# 差分注意力\n\n差分注意力用两个注意力图的差抑制噪声，适合长上下文。\n")
        other = docs / "recipe.txt"
        write(other, "Bread needs flour, butter and yeast. This is not attention research.\n")
        write(docs / "ignore.bin", "not a document")
        write(docs / ".hidden" / "secret.md", "hidden")
        write(docs / "nested" / "extra.txt", "nested document about retrieval")

        paper = docs / "paper.pdf"
        pdf_ok = make_pdf(
            paper,
            [
                "Differential Transformer replaces softmax attention with differential attention.",
                "Long context noise cancellation is discussed on this second page.",
            ],
        )

        print("---- 1. 加载器 ----")
        files = iter_files([docs])
        names = [p.name for p in files]
        check("目录递归找到 4 个支持文件", len(files) == 4, names)
        check("跳过不支持的 .bin", "ignore.bin" not in names)
        check("跳过隐藏目录", "secret.md" not in names)
        check("文本按 UTF-8 读出", "差分注意力" in load_file(notes)[0]["text"])
        check("文本 page=1", load_file(notes)[0]["page"] == 1)
        if pdf_ok:
            pages = load_file(paper)
            check("PDF 抽出 2 页", len(pages) == 2, len(pages))
            check("PDF 保留真实页码", [p["page"] for p in pages] == [1, 2])
            check("PDF 第二页内容正确", "noise cancellation" in pages[1]["text"])
        else:
            skip("PDF 加载", "未安装 pymupdf")
        try:
            load_file(docs / "ignore.bin")
            check("不支持后缀明确报错", False)
        except ValueError:
            check("不支持后缀明确报错", True)
        try:
            load_file(notes, max_bytes=3)
            check("大小上限生效", False)
        except RuntimeError:
            check("大小上限生效", True)

        print("---- 2. 索引 ----")
        index_path = root / "index" / "index.json"
        cfg = SimpleNamespace(
            document_paths=(str(docs),),
            document_index=str(index_path),
            document_max_file_mb=10,
            chunk_size=90,
            chunk_overlap=20,
            document_relative_threshold=0.5,
        )
        first = index_paths([docs], cfg=cfg)
        check("首次索引成功", first["ok"], first.get("errors"))
        check("扫描 4 个文件", first["scanned"] == 4, first["scanned"])
        check("新增 4 个文件", first["added"] == 4, first["added"])
        check("索引落盘", index_path.exists())

        index = load_index(index_path)
        check("索引 version=1", index["version"] == 1)
        check("files 有 4 条", len(index["files"]) == 4, len(index["files"]))
        check("chunks 非空", bool(index["chunks"]), len(index["chunks"]))
        check("chunk 都有 source", all(c.get("source") for c in index["chunks"]))
        check("chunk 都有 sha1", all(c.get("sha1") for c in index["chunks"]))
        check("chunk 都有页码", all(int(c.get("page") or 0) >= 1 for c in index["chunks"]))
        if pdf_ok:
            pdf_pages = {p["page"] for p in index["pages"] if p["source"] == str(paper.resolve())}
            check("索引保留 PDF 两页", pdf_pages == {1, 2}, pdf_pages)

        before_chunks = len(index["chunks"])
        second = index_paths([docs], cfg=cfg)
        index2 = load_index(index_path)
        check("第二次全部跳过", second["skipped"] == 4, second["skipped"])
        check("跳过时块数不变", len(index2["chunks"]) == before_chunks)

        write(notes, "# 差分注意力\n\n新的内容：差分注意力在视觉任务中也能抑制噪声。\n")
        third = index_paths([docs], cfg=cfg)
        index3 = load_index(index_path)
        check("文件变化触发更新", third["updated"] == 1, third["updated"])
        check("其他文件仍跳过", third["skipped"] == 3, third["skipped"])
        check("旧内容已移除", not any("长上下文" in c["text"] for c in index3["chunks"]))
        check("新内容已进入", any("视觉任务" in c["text"] for c in index3["chunks"]))
        check("文件数没有翻倍", len(index3["files"]) == 4, len(index3["files"]))

        forced = index_paths([docs], cfg=cfg, force=True)
        check("force 重解析全部", forced["updated"] == 4, forced["updated"])

        missing = index_paths([root / "no-such-dir"], cfg=cfg)
        check("不存在的路径会报错", not missing["ok"] and missing["errors"], missing["errors"])

        bad = root / "bad" / "index.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{ definitely not json", encoding="utf-8")
        recovered = load_index(bad)
        check("损坏索引退回空索引", recovered == empty_index())
        check("损坏索引被备份", bool(list(bad.parent.glob("index.json.corrupt-*.bak"))))

        print("---- 3. 检索器 ----")
        retriever = LocalDocumentRetriever(
            "差分注意力 噪声", cfg=cfg, index_path=index_path
        )
        hits = retriever.search(max_results=5)
        check("中文查询命中本地文档", bool(hits), len(hits))
        check("命中 notes.md", bool(hits) and hits[0]["source"].endswith("notes.md"))
        check("标题带页码", bool(hits) and "第 1 页" in hits[0]["title"])
        check("href 是 file://", bool(hits) and hits[0]["href"].startswith("file://"))
        check("href 带 page 锚点", bool(hits) and "#page=1" in hits[0]["href"])
        check("raw_content 已带正文", bool(hits) and "差分注意力" in hits[0]["raw_content"])
        check("声明不需要抓取", bool(hits) and hits[0]["requires_scraping"] is False)
        check("body 与 raw_content 一致", bool(hits) and hits[0]["body"] == hits[0]["raw_content"])
        check("返回条数不超上限", len(hits) <= 5)

        english = LocalDocumentRetriever(
            "bread flour butter", cfg=cfg, index_path=index_path
        ).search(max_results=5)
        check("英文查询命中菜谱", bool(english) and english[0]["source"].endswith("recipe.txt"))

        none = LocalDocumentRetriever(
            "量子引力与弦论", cfg=cfg, index_path=index_path
        ).search(max_results=5)
        check("无词法重合不硬塞结果", none == [])

        one = LocalDocumentRetriever(
            "document", cfg=cfg, index_path=index_path
        ).search(max_results=1)
        check("max_results=1 生效", len(one) <= 1, len(one))

        empty_cfg = SimpleNamespace(
            document_index=str(root / "missing-index.json"),
            document_relative_threshold=0.5,
        )
        empty = LocalDocumentRetriever(
            "anything", cfg=empty_cfg, index_path=root / "missing-index.json"
        ).search(max_results=5)
        check("索引不存在时返回空列表", empty == [])

        if pdf_ok:
            page2 = LocalDocumentRetriever(
                "noise cancellation", cfg=cfg, index_path=index_path
            ).search(max_results=5)
            check("PDF 命中第二页", bool(page2) and page2[0]["page"] == 2, page2[:1])
            check("PDF 引用带 #page=2", bool(page2) and "#page=2" in page2[0]["href"])

        print("---- 4. 编排接线 ----")
        run_cfg = SimpleNamespace(
            retriever="local",
            retrievers=("local",),
            document_index=str(index_path),
            document_max_results=5,
            document_relative_threshold=0.5,
            max_pages=5,
            max_concurrency=2,
            max_search_results=5,
        )
        original_fetch = pipeline._fetch_one
        pipeline._fetch_one = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("本地文档不应触发抓取")
        )
        try:
            pages = pipeline._collect_pages(["差分注意力"], run_cfg)
        finally:
            pipeline._fetch_one = original_fetch
        check("_collect_pages 收下本地页", bool(pages), len(pages))
        check("本地页保留正文", bool(pages) and "差分注意力" in pages[0]["raw_content"])
        check("本地页 href 仍是 file://", bool(pages) and pages[0]["url"].startswith("file://"))
        check(
            "file URL 去重保留页码",
            pipeline._normalize_url("file:///x.pdf#page=2").endswith("#page=2"),
        )
        check(
            "网页 URL 仍去掉锚点",
            pipeline._normalize_url("https://x.test/a#section") == "https://x.test/a",
        )

        called: list[str] = []
        original_search = pipeline._search_one
        original_fetch = pipeline._fetch_one
        pipeline._search_one = lambda q, c: [
            {
                "title": "web",
                "href": "https://x.test/a",
                "body": "preview",
                "raw_content": "preview",
                "requires_scraping": True,
            }
        ]
        pipeline._fetch_one = lambda url, title, c: (
            called.append(url) or {"title": title, "url": url, "raw_content": "full"}
        )
        try:
            web = pipeline._collect_pages(["q"], run_cfg)
        finally:
            pipeline._search_one = original_search
            pipeline._fetch_one = original_fetch
        check("网页来源仍会抓取", called == ["https://x.test/a"], called)
        check("抓取后的正文被采用", bool(web) and web[0]["raw_content"] == "full")

        print("---- 5. 配置与 CLI ----")
        cfg_obj = Config()
        check("Config 有 document_paths", hasattr(cfg_obj, "document_paths"))
        check("Config 有 document_index", hasattr(cfg_obj, "document_index"))
        os.environ["DOCUMENT_PATHS"] = r"D:\Docs;D:\Papers"
        try:
            parsed = _env_paths("DOCUMENT_PATHS", ())
        finally:
            os.environ.pop("DOCUMENT_PATHS", None)
        check("路径读取保留大小写", parsed == (r"D:\Docs", r"D:\Papers"), parsed)

        import main
        parser = main.build_parser()
        parsed_args = parser.parse_args(["local-index", "a", "b"])
        check("CLI 有 local-index", parsed_args.command == "local-index")
        check("CLI 收多个路径", parsed_args.paths == ["a", "b"])
        check("STAGE_OF 标记阶段 19", main.STAGE_OF.get("local-search") == 19)

        from search import get_retriever
        got = get_retriever("local", "x")
        check("检索器注册表认识 local", isinstance(got, LocalDocumentRetriever))

    print()
    if FAILED:
        print(f"{len(FAILED)} 项失败：{FAILED}")
        return 1
    print(f"全部通过（{len(SKIPPED)} 项跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
