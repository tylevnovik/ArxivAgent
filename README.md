# 🔍 ArxivAgent — 多源论文检索研究工作台

当前版本：`v0.4.0`

基于 LLM 驱动的智能论文检索 Agent，支持 arXiv、OpenAlex、Crossref 等开放学术数据源，
带线程持久化、混合 RAG 正文检索、结构化引用证据，以及 Electron 桌面端。

## 功能特性

- 🗣️ **自然语言检索**：用中文描述需求，Agent 自动理解并构造检索式
- 🔄 **智能迭代优化**：自动审核检索结果，不满意时迭代优化检索策略
- 🌐 **多源检索**：arXiv、OpenAlex、Crossref、Semantic Scholar，带缓存、去重与排序
- 🧵 **线程持久化**：每个会话磁盘 JSON 存储，可重命名 / 删除 / 恢复
- 📚 **混合正文 RAG**：FastEmbed 向量化 + Qdrant + BM25S + RRF 融合，引用论文正文片段
- 🔗 **引用证据链**：报告引用回溯到具体论文正文切片（EvidenceChunk），前端可点击展开
- 💻 **Electron 桌面端**：左线程列表 + 中间聊天壳 + 右侧研究面板（文献/报告/Evidence）
- 🛡️ **打包版依赖检查**：uv 引导向导，前置探测 Python 版本与缺失模块
- 🔒 **Markdown 安全加固**：DOMPurify 集中消毒，防 XSS
- 📤 **多格式导出**：Markdown、CSV、JSON

## 快速开始

### 桌面端（推荐）

```bash
cd desktop
bun install
bun run dev
```

`dev` 启动 Vite (`http://localhost:5173`) + Electron。桌面主进程自动查找
Python 后端并启动于 `http://127.0.0.1:7860`。

首次运行需要安装依赖：

```bash
# 安装 uv（如未安装）
pip install uv

# 同步 Python 依赖（项目根目录）
uv sync
```

### 后端裸跑（仅调试，无 UI）

```bash
uv sync
python app.py
```

后端监听 `http://127.0.0.1:7860`，提供 `/api/threads/**` 等 REST API。
直接访问根路径无 UI（仅返回 404）。

## 项目结构

```
ArxivAgent/
├── core/                    # 后端核心模块
│   ├── agent.py              # Agent 主循环（intent → search → report → evidence）
│   ├── contracts.py          # Pydantic 产品契约（AgentEventEnvelope / ThreadDetail / EvidenceChunk）
│   ├── threads.py            # 线程持久化管理（磁盘 JSON）
│   ├── llm.py                # OpenAI 兼容 API 封装（DeepSeek / 自定义）
│   ├── arxiv_search.py       # arXiv API 封装
│   ├── search_service.py     # 多源检索、缓存与排序服务
│   ├── pdf_parser.py         # PDF 下载与文本提取分块
│   ├── rag.py                # 本地混合 RAG 检索器（Qdrant + BM25S + RRF）
│   ├── memory.py             # 对话记忆 + evidence_chunks
│   └── exporter.py          # 多格式导出（MD / CSV / JSON）
├── prompts/                  # 提示词模板（中文）
│   ├── system.txt            # 系统提示词
│   ├── query_parse.txt       # 需求理解 & 构造检索式
│   ├── result_review.txt     # 审核检索结果
│   ├── refine_query.txt      # 优化检索策略
│   ├── error_recovery.txt    # 检索出错恢复
│   ├── followup_chat.txt     # 对话追问
│   └── summary.txt           # 生成最终报告
├── desktop/                  # Electron + React + Vite 桌面端
│   ├── src/electron/         #   主进程（Python 后端启动 / 依赖诊断 / safeStorage）
│   ├── src/mainview/         #   渲染进程（线程列表 / 聊天 / 研究面板 / 引用 / 设置）
│   └── tests/                #   E2E + 集成 + mock 后端
├── tests/backend/            # 后端 pytest（27 tests）
├── app.py                    # FastAPI 后端入口
├── config.py                 # 配置管理
├── pyproject.toml            # Python 依赖 & uv 配置
├── .python-version           # 3.12
├── pytest.ini                # pytest 配置
└── implementation_plan.md    # 版本设计与验证记录
```

## 架构

### Agent 工作流

```
用户 query → [意图识别] → 构造检索式 → [多源检索] → [审核结果]
                                                           ↓
                                               满意？→ 否 → [优化策略] → 重新检索
                                                           ↓
                                                           是 → [RAG 增强] → [生成报告] → evidence → 完成
```

### 桌面端布局

- **左侧**：线程列表（可折叠）
- **中间**：assistant-ui 风格聊天壳（流式事件渲染）
- **右侧**：ResearchPanel（按需打开，Tab 切换「文献」与「报告」，报告下方挂 EvidenceList）

### 前后端契约

后端通过 `core/contracts.py`（Pydantic 模型）和前端 `api.ts`（TypeScript 类型）交互。
事件流走 `application/x-ndjson`，每个事件为 `AgentEventEnvelope`。

## 后端 API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查（desktop 启动时轮询） |
| GET | `/api/system/deps` | Python 版本 + 缺失模块 + uv 命令模板 |
| GET | `/api/threads` | 列出所有线程 meta |
| POST | `/api/threads` | 新建线程 |
| GET | `/api/threads/{id}` | 取线程详情（消息 / papers / evidence / 报告） |
| PATCH | `/api/threads/{id}` | 重命名线程 |
| DELETE | `/api/threads/{id}` | 删除线程 |
| POST | `/api/threads/{id}/messages` | 发送 query，触发 Agent（NDJSON 事件流） |
| POST | `/api/threads/{id}/cancel` | 取消正在运行的任务 |
| PATCH | `/api/threads/{id}/messages/{i}` | 编辑某条消息 |
| DELETE | `/api/threads/{id}/messages/{i}` | 删除某条消息 |
| GET | `/api/threads/{id}/papers` | 返回候选文献列表 |
| GET | `/api/threads/{id}/report` | 返回最终报告 Markdown |
| POST | `/api/threads/{id}/export` | 导出（chat / md / csv / json / report） |
| POST | `/api/config/health` | 探测 LLM / 检索源连通性 |
| GET | `/api/config/health` | 获取上次探测结果 |

## 配置

也可通过 `.env` 文件设置：

### LLM 模型

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | （空） | API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 端点（可改为任意 OpenAI 兼容服务） |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 模型名 |

桌面端支持通过设置面板配置，API Key 走 Electron safeStorage 加密存储。

### 多源检索

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SEARCH_PROVIDERS` | `arxiv,openalex,crossref` | 启用的检索源（逗号分隔） |
| `SEARCH_PROVIDER_TIMEOUT_SECONDS` | `15` | 单源超时（秒） |
| `SEARCH_CACHE_TTL_SECONDS` | `86400` | 缓存 TTL（秒） |
| `OPENALEX_MAILTO` | （空） | OpenAlex polite pool 邮箱 |
| `CROSSREF_MAILTO` | （空） | Crossref polite pool 邮箱 |
| `SEMANTIC_SCHOLAR_API_KEY` | （空） | Semantic Scholar API Key（可选） |

### RAG 检索

默认使用本地方案，不需要 OpenAI API Key 或 Docker：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RAG_RETRIEVER_TYPE` | `hybrid` | 检索器类型（依赖不可用自动降级 TF-IDF） |
| `RAG_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | FastEmbed 模型 |
| `RAG_QDRANT_LOCATION` | `DATA_DIR/.cache/qdrant` | Qdrant 数据目录（也可配 URL） |
| `RAG_TOP_K` | `6` | 最终返回 chunk 数 |
| `RAG_DENSE_CANDIDATES` | `20` | 向量召回候选数 |
| `RAG_BM25_CANDIDATES` | `20` | BM25 召回候选数 |
| `RAG_RRF_K` | `60` | RRF 融合参数 |
| `RAG_ENABLE_RERANKER` | `false` | 是否启用 reranker（默认关闭） |
| `RAG_RERANKER_MODEL` | `BAAI/bge-reranker-base` | reranker 模型 |

### 数据目录

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ARXIV_AGENT_DATA_DIR` | 项目根目录 | 运行期数据根目录（桌面端设为 userData/backend-data） |

子目录：`threads/`（线程 JSON）、`exports/`（导出文件）、`.cache/search/`（检索缓存）、
`pdf_cache/`（PDF 缓存）、`.cache/qdrant/`（向量库）。

## 桌面端开发

```bash
cd desktop
bun install          # 安装前端依赖
bun run dev          # Vite HMR + Electron（自动启动 Python 后端）
bun run typecheck     # TypeScript 类型检查
bun run build         # typecheck + vite build
bun run test:unit    # vitest 单测 + 集成测试（含 mock 后端）
```

### 打包

```bash
cd desktop
bun run build:canary   # prepare:backend-runtime + build + electron-builder --win nsis
```

打包后内置 Python runtime + site-packages + `pyproject.toml`。
目标机器如需更新依赖，桌面端 SetupWizard 会给出 `uv sync` 命令。

## 后端测试

```bash
# 项目根目录
pytest tests/backend -q
```

当前 27 个测试覆盖：健康检查 / 错误协议 / 线程 CRUD + 持久化 / 检索事件序列 /
结构化 papers / evidence 链路 / 取消令牌 / 导出 / 依赖探测。

## 已知限制

- Qdrant local mode 适合单进程桌面运行；多进程并发时建议改用独立 Qdrant server。
- RAG 依赖不可用时自动降级为轻量 TF-IDF，不会中断主流程。
- 取消令牌不能中断已在飞的 HTTP 请求，只能在边界退出（同步 + requests 固有限制）。
- Electron safeStorage 在无 keyring 的 Linux 不可用 → 明文回退 + UI 标注。
- evidence chunk 文本截断到 500 字展示。
- chunks 无章节/标题信息（字符窗口分块），引用精确到"分块 N"。

## License

MIT
