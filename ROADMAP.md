# mini-gpt-researcher 分阶段实现路线

参照仓库：`D:\gpt-researcher`（v0.16.0，commit 0957c301）
目标：用 15 天，从零复现 gpt-researcher 的核心链路，每个阶段都能跑、能看到输出。

数据流主线（复现的就是这条链）：

```
query
  -> sub_queries        子问题拆解
  -> search_results     {url, title, body}
  -> scraped_pages      {raw_content}
  -> context            压缩后的上下文字符串
  -> report             Markdown 成稿 + 引用
```

## 总览表

| 阶段 | 天数 | 目标 | 对照源码 | 验收命令 |
|---|---|---|---|---|
| 0 | Day 1 | 骨架 + 配置 + LLM 连通 | `config/config.py`、`utils/llm.py` | `python main.py check` |
| 1 | Day 2 | 单次搜索 | `retrievers/base.py`、`duckduckgo.py` | `python main.py search "..."` |
| 2 | Day 3 | LLM 摘要（quick search） | `agent.py:531`、`prompts.py:532` | `python main.py summary "..."` |
| 3 | Day 4-5 | 子问题拆解 | `actions/query_processing.py:83`、`prompts.py:213` | `python main.py plan "..."` |
| 4 | Day 6-7 | 正文抓取 | `scraper/scraper.py`、`scraper/beautiful_soup/` | `python main.py fetch <url>` |
| 5 | Day 8-9 | 上下文筛选与压缩 | `context/select.py`、`context/lexical.py` | `python main.py context "..."` |
| 6 | Day 10-12 | 成稿 + 引用 | `skills/writer.py`、`actions/report_generation.py` | `python main.py report "..."` |
| 7 | Day 13 | 成本追踪 | `utils/costs.py` | `python main.py report "..." --costs` |
| 8 | Day 14-15 | 并发编排 + 容错 | `skills/researcher.py:97`、`skills/context_manager.py` | `python main.py research "..."` |
| 9 | 可选 | 深度研究 / 多 Agent | `skills/deep_research.py`、`multi_agents/` | - |

进度打勾：

- [ ] Day 1  阶段 0
- [ ] Day 2  阶段 1
- [ ] Day 3  阶段 2
- [ ] Day 4-5  阶段 3
- [ ] Day 6-7  阶段 4
- [ ] Day 8-9  阶段 5
- [ ] Day 10-12  阶段 6
- [ ] Day 13  阶段 7
- [ ] Day 14-15  阶段 8

---

## 阶段 0 / Day 1 — 骨架、配置、LLM 连通

**目标**：项目能起来，配置能读，模型能通。

**要做的事**

1. 建 venv，装 `requirements.txt`。
2. 写 `config.py`：从 `.env` 读配置，给出默认值，做一次校验（缺 key 要报清楚）。
3. 写 `llm.py`：一个 `chat()`，走 OpenAI 兼容接口。
4. 写 `main.py`：子命令分发，先实现 `check`。

**对照源码**

- `gpt_researcher/config/config.py`（288 行）：看 `merge_config`、`parse_llm`、`parse_retrievers`、`convert_env_value` 如何把字符串 env 转成目标类型。
- `gpt_researcher/config/variables/default.py`（58 行）：默认值长什么样。
- `gpt_researcher/llm_provider/generic/base.py:146` 的 `from_provider`：注意 `base_url` 是怎么透传给 SDK 的。
- `gpt_researcher/utils/llm.py`（192 行）：先扫一遍，阶段 2 再精读。

**验收**

```
python main.py check
```

打印出：Python 版本、模型名、base_url、key 是否存在（打码）、以及一次 1+1 的 ping 回应。

**坑**

- key 存在但 base_url 没配 -> 会打到 OpenAI 官方，报 401。
- `.env` 不要提交到 git。

---

## 阶段 1 / Day 2 — 单次搜索

**目标**：给一个 query，拿到结构化搜索结果。

**要做的事**

1. `search/base.py`：定义检索器契约（`search(max_results) -> list[dict]`，以及 `requires_scraping`）。
2. `search/duckduckgo.py`：免 key，先跑通。
3. `search/tavily.py`：需要 key，但质量高。
4. `main.py` 加 `search` 子命令。

**对照源码**

- `gpt_researcher/retrievers/base.py`（47 行）：`requires_scraping` 这个声明是怎么来的，读文件顶部的注释，讲得很清楚。
- `gpt_researcher/retrievers/duckduckgo/duckduckgo.py`（73 行）：注意 `_MAX_PREFETCHED_LEN = 100` 那段注释，是踩坑记录。
- `gpt_researcher/retrievers/tavily/tavily_search.py`（137 行）：`site:` 操作符的兼容处理、异常吞掉返回空列表。
- `gpt_researcher/retrievers/utils.py`（108 行）：检索器名字到类的映射。

**验收**

```
python main.py search "gpt-researcher 是什么" --max 5
```

输出 5 条，每条有 url / title / body，且字段名统一。

**坑**

- 不同搜索源字段名不一致（`href` vs `link` vs `url`），必须归一化。
- 网络失败要返回空列表并打日志，不要抛异常炸掉主流程。

---

## 阶段 2 / Day 3 — LLM 摘要（quick search）

**目标**：把搜索结果喂给模型，产出一段带来源的短答案。这是原项目的 `quick_search`。

**要做的事**

1. `main.py` 加 `summary` 子命令：搜索 -> 拼 context -> 调 LLM -> 打印。
2. 在 `llm.py` 里补：重试、超时、`max_tokens` 控制。

**对照源码**

- `gpt_researcher/agent.py:531` `quick_search`：整个流程 50 行，最完整的入门样例。
- `gpt_researcher/prompts.py:532` `generate_quick_summary_prompt`：提示词模板。
- `gpt_researcher/utils/llm.py`：重试与错误处理的写法。
- `gpt_researcher/prompts.py:555` `pretty_print_docs`：文档怎么拼成字符串。

**验收**

```
python main.py summary "gpt-researcher 是什么" 
```

输出一段 200 字以内、带 1-3 个链接的答案。

**坑**

- DeepSeek 的输出上限是 8192，别照抄原项目 `SMART_TOKEN_LIMIT=12000`。

---

## 阶段 3 / Day 4-5 — 子问题拆解（研究计划）

**目标**：一个复杂 query，拆成 N 个可检索的子问题。这是"研究"和"搜索"的分水岭。

**要做的事**

1. `pipeline.py`：`plan_research(query) -> list[str]`。
2. 提示词里要求模型返回 JSON 数组。
3. 写一个健壮的解析函数，处理模型返回不干净 JSON 的情况。

**对照源码**

- `gpt_researcher/actions/query_processing.py:83` `generate_sub_queries`：三级降级（strategic LLM -> 限制 max_tokens 重试 -> 换 smart LLM）。
- `gpt_researcher/actions/query_processing.py:11` `_normalize_sub_queries`：**这个函数必读**，它把 list / dict / str / None 各种畸形返回都归一化成 `list[str]`。
- `gpt_researcher/prompts.py:213` `generate_search_queries_prompt`。
- `gpt_researcher/skills/researcher.py:68` `plan_research`。

**验收**

```
python main.py plan "对比 gpt-researcher 和 openai deep research"
```

输出 3-5 个子问题，每个都独立可检索。

**坑**

- 模型经常返回 ```json 包裹的内容，或 `{"queries": [...]}`，解析必须容错。
- 解析失败要有兜底：退化成用原始 query 检索。

---

## 阶段 4 / Day 6-7 — 正文抓取

**目标**：拿到 URL 后，把网页正文抓下来。这是"可引用"的前提。

**要做的事**

1. `scrape.py`：`fetch(url) -> {url, title, raw_content}`。
2. 用 `requests` + `BeautifulSoup` 去 nav/script/style/footer。
3. 加超时、加 user-agent、加失败重试。
4. `main.py` 加 `fetch` 子命令。
5. 把阶段 1 的检索结果接上抓取：搜索结果 -> 抓正文 -> `raw_content`。

**对照源码**

- `gpt_researcher/scraper/scraper.py:109`：`run`（142）并发跑、`extract_data_from_url`（194）、`get_scraper`（307）按 URL 类型选抓取器。
- `gpt_researcher/scraper/scraper.py:73` `_looks_like_block_page`、`:78` `_looks_like_word_list`、`:102` `_looks_like_unextracted_pdf`：**怎么判断抓回来的是垃圾**，很值得抄。
- `gpt_researcher/scraper/beautiful_soup/beautiful_soup.py`（80 行）：最简实现。
- `gpt_researcher/scraper/utils.py`（201 行）：正文提取与清洗的细节。
- `gpt_researcher/skills/researcher.py:851` `_search_relevant_source_urls` 和 `:936` `_scrape_data_by_urls`：抓取在流程里的位置。
- `gpt_researcher/retrievers/base.py` 的 `requires_scraping`：判断"要不要抓"。

**验收**

```
python main.py fetch "https://example.com/some-article"
```

打印标题 + 正文前 300 字，和浏览器里看到的正文基本一致。

**坑**

- 抓回来是登录墙 / 验证码页 / 空壳 SPA，要能识别并丢弃。
- PDF 链接要单独处理（原项目用 pymupdf）。
- 不要忘了 `timeout`，否则会卡死。

---

## 阶段 5 / Day 8-9 — 上下文筛选与压缩

**目标**：5 篇文章几万字，塞不进模型，要挑出和 query 最相关的片段。

**要做的事**

1. `context.py`：`select_context(query, pages) -> str`。
2. 先实现最简单的：不筛选，直接拼（对应 `none` 模式）。
3. 再实现 BM25 关键词排序（对应 `keyword` 模式）——本地、无依赖、无 API。
4. 加阈值裁剪：只保留分数 >= 最高分 50% 的块，上限 25 块。
5. 加短路：内容本来就少（< 8000 字符）就不筛选。

**对照源码**

- `gpt_researcher/context/select.py`（79 行）：`resolve_context_filter`（42）与 `select_context`（57）。五种模式、失败回落到 keyword，这个设计直接抄。
- `gpt_researcher/context/lexical.py`（112 行）：`tokenize`（44）、`bm25_scores`（48）、`select`（95）。BM25 手写实现，约 40 行，重点读。
- `gpt_researcher/context/compression.py`（222 行）：向量相似度版本 `ContextCompressor`（85），等你想升级再回来看。
- `gpt_researcher/prompts.py:555` `pretty_print_docs`。

**验收**

```
python main.py context "gpt-researcher 是什么"
```

打印：原始字符数 -> 压缩后字符数，以及选中的片段里确实包含关键句。

**坑**

- DeepSeek 没有 embedding 接口，`embeddings` 模式用不了，选 `keyword`。
- 分块时按字符切会把句子切断，按段落或按句切更好。

---

## 阶段 6 / Day 10-12 — 成稿 + 引用

**目标**：把上下文写成一篇 Markdown 报告，并且每句话能追溯到来源。这是整个项目最有价值的部分。

**要做的事**

1. `report.py`：`write_report(query, context, sources) -> str`。
2. 提示词要求：带小标题、带 `[1]` 式角标引用、末尾附参考文献表。
3. 实现 `add_references`：把角标编号映射到 URL 列表。
4. 加引言、结论两段（可以各自一次调用）。
5. `main.py` 加 `report` 子命令，输出存到 `outputs/*.md`。

**对照源码**

- `gpt_researcher/skills/writer.py:20` `ReportGenerator`，`:49` `write_report`（主流程）、`:136` `write_report_conclusion`、`:175` `write_introduction`。
- `gpt_researcher/actions/report_generation.py:209` `generate_report`、`:12` `write_report_introduction`、`:63` `write_conclusion`、`:115` `summarize_url`（长文先摘要再进上下文）。
- `gpt_researcher/prompts.py:262` `generate_report_prompt`（**核心提示词**，注意它怎么约束引用格式）。
- `gpt_researcher/prompts.py:418` `generate_deep_research_prompt`。
- `gpt_researcher/agent.py:700` `add_references`、`:712` `extract_headers`、`:734` `table_of_contents`。
- `gpt_researcher/actions/markdown_processing.py`（108 行）：Markdown 后处理。

**验收**

```
python main.py report "对比 gpt-researcher 和 openai deep research"
```

产出 `outputs/xxx.md`：有标题层级、有引用角标、参考文献表里的链接都能点开。

**坑**

- 模型会编造引用编号（引用了不存在的 [7]），写完要校验编号范围。
- 引用编号要在拼 context 时就固定下来，不要让模型自己发明。

---

## 阶段 7 / Day 13 — 成本追踪

**目标**：知道每跑一次花多少钱。这既是工程能力，也是简历上的加分项。

**要做的事**

1. `costs.py`：按模型名查单价，用 usage 里的 token 数算钱。
2. 包一层 LLM 调用，每次调用后回调累加。
3. 支持按步骤（搜索/摘要/成稿）分别统计。

**对照源码**

- `gpt_researcher/utils/costs.py`（286 行）：`estimate_llm_cost`（71）粗估版、`calculate_llm_cost`（265）精确版、`_extract_usage_tokens`（233）。
- `gpt_researcher/agent.py:761` `get_costs`、`:769` `get_step_costs`、`:785` `add_costs`（回调怎么串起来）。

**验收**

```
python main.py report "..." --costs
```

打印本次总花费与各步骤明细。

**坑**

- 优先用 API 返回的 `usage`，字符数估算误差很大。
- DeepSeek 便宜，单次报告通常不到 1 分钱，别以为算错了。

---

## 阶段 8 / Day 14-15 — 并发编排 + 容错

**目标**：多个子问题并发跑，单点失败不影响整体。到这里就是一个完整的研究 Agent 了。

**要做的事**

1. `pipeline.py`：`conduct_research(query) -> context + sources`，串起阶段 3-5。
2. 用 `asyncio.gather` 并发处理子问题，加并发上限。
3. 失败降级：某个子问题检索失败 -> 跳过并记录，不中断。
4. 去重：多个子问题命中同一 URL 只抓一次。
5. `main.py` 加 `research`：一键 从 query 到报告。

**对照源码**

- `gpt_researcher/skills/researcher.py:97` `conduct_research`（**主循环，全文最重要的函数**）、`:520` `_process_sub_query`、`:298` `_get_context_by_web_search`、`:813` `_get_new_urls`（去重）、`:1065` `_extract_content`、`:1102` `_summarize_content`。
- `gpt_researcher/skills/context_manager.py`（134 行）：上下文调度的封装。
- `gpt_researcher/utils/workers.py`（42 行）：并发池。
- `gpt_researcher/utils/rate_limiter.py`（82 行）：限流。
- `gpt_researcher/agent.py:343` `conduct_research`：顶层入口。
- `gpt_researcher/utils/logger.py`（75 行）、`logging_config.py`（75 行）。

**验收**

```
python main.py research "对比 gpt-researcher 和 openai deep research"
```

一次跑完：子问题 -> 搜索 -> 抓取 -> 压缩 -> 成稿，输出报告 + 成本。

**故意做破坏测试**：把某个 URL 换成不存在的域名，确认流程照样跑完。

---

## 阶段 9 / 可选进阶 — 深度研究与多 Agent

到这里你已经复现了核心。想继续的话：

- **深度研究**：`gpt_researcher/skills/deep_research.py`（557 行）——多轮递归研究，每轮基于上一轮结论生成新问题。
- **来源管家**：`gpt_researcher/skills/curator.py`（95 行）——给来源打分排序。
- **动态角色**：`gpt_researcher/actions/agent_creator.py`（130 行）——根据 query 自动选研究者人设。
- **多 Agent 编辑部**：`D:\gpt-researcher\multi_agents\`——编辑、研究者、评审、修订四个角色协作。
- **LangGraph 版**：`D:\gpt-researcher\deep_agents\`。

---

## PyCharm 操作步骤

1. `File > Open` 选择 `D:\mini-gpt_researcher`。
2. `File > Settings > Project > Python Interpreter > Add Interpreter > Add Local Interpreter > Virtualenv`，
   位置选 `D:\mini-gpt_researcher\.venv`，基础解释器用你的 3.13.9（3.12/3.13 都可以）。
3. 打开 PyCharm 底部 `Terminal`，装依赖：`pip install -r requirements.txt`。
4. 复制 `.env` 为 `.env`，填 key。
5. 运行配置：右上角 `Add Configuration > Python`，脚本选 `main.py`，参数填 `check`。
   之后每个阶段只改参数（`search "..."` / `summary "..."` ...），不用新建配置。
6. 开 `Settings > Tools > Python Integrated Tools` 不用改；调试时直接在函数里打断点。

## DeepSeek 配置要点

```
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
FAST_LLM=deepseek:deepseek-chat
SMART_LLM=deepseek:deepseek-chat
STRATEGIC_LLM=deepseek:deepseek-chat
```

三个已知的坑（照抄原项目配置时会踩）：

1. DeepSeek 没有 embedding 接口 -> 上下文筛选只能用 `keyword`，不要用 `embeddings`。
2. 最大输出 8192 -> 任何 `max_tokens` 不要超过这个数（原项目默认 12000，会报错）。
3. base_url 必须是 `https://api.deepseek.com`，写错了会打到 OpenAI 官方然后 401。
---

## 附：两个小坑

**中文乱码**：如果在 cmd / PowerShell 里跑 `python main.py check` 看到中文是乱码，
是控制台编码不是 UTF-8。PyCharm 的 Terminal 默认 UTF-8，一般不会遇到。
真遇到了就设环境变量 `PYTHONUTF8=1`。

**git 报 dubious ownership**：如果 git 提示仓库所有者不对，执行一次：

```
git config --global --add safe.directory D:/mini-gpt_researcher
```

## 附：文件与阶段对照

| 文件 | 负责阶段 | 状态 |
|---|---|---|
| `config.py` | 0 | 已实现，可直接跑 |
| `llm.py` | 0 / 2 / 7 | 最小实现，待补重试与 usage |
| `main.py` | 全部 | 已实现（子命令分发） |
| `search/base.py` | 1 | 契约已定，待实现检索器 |
| `search/duckduckgo.py` | 1 | 待实现 |
| `search/tavily.py` | 1 | 待实现 |
| `scrape.py` | 4 | 待实现 |
| `context.py` | 5 | 待实现 |
| `report.py` | 6 | 待实现 |
| `costs.py` | 7 | 待实现 |
| `pipeline.py` | 2 / 3 / 5 / 6 / 8 | 待实现（编排层，最后成型） |

每个待实现函数都带 `TODO(阶段 N)` 和验收方式，照着 ROADMAP 填即可。

## 附：工作流建议

- 每天早上先跑一遍上个阶段的验收命令，确认没被改坏。
- 卡住超过 40 分钟就去看对照源码的对应函数——原项目踩过的坑基本都在注释里。
- 每完成一个阶段就 `git commit` 一次，方便回滚（例如 `git commit -m "阶段 1: 单次搜索"`）。
---

## 阶段 0 验收标准（补充）

命令：

```
python main.py check --offline   # 不联网：验解释器、依赖、.env、配置合法性
python main.py check             # 再加一项：真实调用一次模型
```

**通过的样子**（`--offline` 下）：

```
阶段 0 自检
====================================================================
Python 版本          OK    3.13.9
依赖 openai          OK    2.49.0（调用大模型）
依赖 dotenv          OK    1.2.1（读取 .env）
依赖 ddgs            SKIP  未安装（阶段 1 免 key 检索，后期阶段才用）
.env 文件            OK    已找到 .env
配置 模型              OK    deepseek:deepseek-chat -> provider=deepseek
配置 max_tokens      OK    4000
配置 context_filter  OK    keyword
配置 temperature     OK    0.4
模型连通               SKIP  指定了 --offline
====================================================================

阶段 0 通过（2 项跳过，属正常：依赖 ddgs, 模型连通）
```

判定规则：

- 出现任何 `FAIL` -> 没过，按提示修。
- `SKIP` 是正常的，表示"这一项现在还用不到"。
- 只有 `依赖 openai` / `依赖 dotenv` / `.env 文件` / `配置 *` / `模型连通` 全为 `OK`（或连通项 SKIP），才算阶段 0 完成。
- `ddgs`、`json_repair` 等在后面对应阶段再装，现在 SKIP 不影响。

常见 FAIL 与修法：

| FAIL 项 | 原因 | 修法 |
|---|---|---|
| `.env 文件` | 没建 .env | 复制 `.env` 为 `.env` |
| `模型连通` | key 缺失 / 拼错 | 检查 `DEEPSEEK_API_KEY` |
| `模型连通` | base_url 不对 | 必须是 `https://api.deepseek.com` |
| `模型连通` | 网络不通 | 检查代理 |
| `配置 max_tokens` | 超过 8192 | 调小 `SMART_TOKEN_LIMIT` |
---

## 阶段 0 实测记录（DeepSeek 推理模型）

跑通时发现的一个真问题，对后面所有阶段都成立。

**现象**：`python main.py check` 显示连通 OK，但模型回复是空字符串。

**原因**：DeepSeek 的推理模型会先输出一段思维链，`max_tokens` 是
**「思考 + 正文」共享的额度**，不是只算正文。实测同一个提示词：

| max_tokens | finish_reason | 推理 token | content |
|---|---|---|---|
| 16 | length | 16 | `''` （空） |
| 128 | stop | 115 | `'正常'` |
| 512 | stop | 14 / 72 | `'正常'` |

同一个提示词的推理长度在 14 ~ 115 token 之间波动。所以：

1. `max_tokens` 给小了，额度全被思考吃掉，`content` 直接是空字符串，
   而且 API 返回 200，不报错——**静默失败**，最难查。
2. `SMART_TOKEN_LIMIT` 要保持 >= 1024，本项目默认 4000。

**代码里对应的处理**（`llm.py`）：

- `chat()` 返回前检查正文是否为空；为空且 `finish_reason == "length"`
  就抛 `LLMTruncated`，消息里带上"多少 token 用在了推理上"。
- `USAGE_LOG` 额外记录 `reasoning_tokens`——**算钱时必须算进去**，
  否则成本会低估一大截。
- `max_tokens` 不传时回落 `cfg.smart_token_limit`（早期版本漏了这一步，
  导致 .env 里的 `SMART_TOKEN_LIMIT` 设了却完全不生效）。

**一个顺带学到的 Python 坑**：`from llm import LAST_FINISH_REASON`
拿到的是导入那一刻的**值快照**，`chat()` 之后更新模块全局变量，
这个局部名字不会跟着变。要看最新值必须 `import llm` 再用
`llm.LAST_FINISH_REASON`。自检里原本就踩了这个坑，
表现为"明明调用成功却显示 推理 0 tokens"。

**当前实测输出**：

```
模型连通   OK   https://api.deepseek.com / deepseek-flash -> '正常'（finish=stop, 推理 72 tokens）
阶段 0 通过（2 项跳过，属正常：依赖 ddgs, 依赖 json_repair）
```