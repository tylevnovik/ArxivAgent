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

从源码运行时首次需要安装 Python 依赖（**打包版安装包不需要**：
安装包内置 Python 运行时与全部 site-packages，见下文"打包"）：

```bash
# 仅源码/开发运行需要 uv（打包版用户无需安装）
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
│   ├── providers.py          # LLM 供应商目录与凭据/端点解析（唯一事实源）
│   ├── auth.py               # 本地 HMAC-SHA256 请求签名与校验
│   ├── arxiv_search.py       # arXiv API 封装
│   ├── search_service.py     # 多源检索、缓存与排序服务
│   ├── pdf_parser.py         # PDF 下载与文本提取分块
│   ├── chunk_identity.py     # 正文分块内容指纹（缓存键 / Qdrant 集合身份共用）
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
├── tests/backend/            # 后端 pytest（228 tests）
├── app.py                    # FastAPI 后端入口
├── config.py                 # 配置管理
├── pyproject.toml            # Python 依赖 & uv 配置
├── .python-version           # 3.12
├── pytest.ini                # pytest 配置
├── implementation_plan.md    # 版本设计与验证记录
└── docs/plans/               # 本次稳定性与发布加固计划
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
| GET | `/api/download` | 下载 exports 目录内的导出文件（防路径穿越） |
| POST | `/api/config/health` | 探测 LLM / 检索源连通性 |
| GET | `/api/config/health` | 获取上次探测结果 |
| GET | `/api/auth/status` | 认证状态（HMAC 是否启用，启动诊断用） |
| POST | `/api/shutdown` | 优雅关闭后端（HMAC 保护，桌面端退出时调用） |

`/api/health`、`/api/system/deps`、`/api/auth/status` 仅用于启动诊断；配置健康探测
需要桌面端 HMAC 签名。CORS 只允许本地 Vite 开发源和 Electron `file://` 页面，
不会接受任意网站来源。

## 数据目录

运行期数据（线程、PDF 缓存、检索缓存、导出文件、Qdrant 索引）默认放在用户数据目录：

- **macOS**：`~/Library/Application Support/arxivagent`
- **Linux**：`~/.local/share/arxivagent`
- **Windows**：`%APPDATA%\arxivagent`

可用环境变量 `ARXIV_AGENT_DATA_DIR` 覆盖（桌面端会自动指向 Electron 的 userData 目录）。

> 从旧版本升级：若之前数据写在项目根目录，手动把 `threads/`、`pdf_cache/`、`exports/`、`.cache/` 复制到上述新目录即可。

## 配置

也可通过 `.env` 文件设置：

### LLM 模型

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | （空） | API Key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 端点（可改为任意 OpenAI 兼容服务） |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 模型名 |
| `OPENCODE_GO_API_KEY` | （空） | OpenCode Go 订阅 Key |
| `OPENCODE_GO_API_BASE` | `https://opencode.ai/zen/go/v1` | OpenCode Go 端点 |
| `OPENAI_API_KEY` | （空） | OpenAI 或任意 OpenAI-compatible 端点的 Key |
| `OPENAI_API_BASE` | `https://api.openai.com/v1` | OpenAI-compatible 端点 |
| `MIMO_API_KEY` | （空） | MiMo Key |
| `MIMO_API_BASE` | `https://token-plan-cn.xiaomimimo.com/v1` | MiMo 端点 |
| `ZHIPU_API_KEY` | （空） | 智谱 GLM Key |
| `ZHIPU_API_BASE` | `https://open.bigmodel.cn/api/paas/v4` | 智谱 OpenAI-compatible 端点 |
| `MOONSHOT_API_KEY` | （空） | Moonshot Kimi Key |
| `MOONSHOT_API_BASE` | `https://api.moonshot.cn/v1` | Moonshot 端点 |
| `DASHSCOPE_API_KEY` | （空） | 阿里百炼 Key |
| `DASHSCOPE_API_BASE` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | DashScope 兼容端点 |
| `OLLAMA_API_BASE` | `http://localhost:11434/v1` | 本地 Ollama，无需 Key |
| `VLLM_API_BASE` | `http://localhost:8000/v1` | 本地 vLLM，无需 Key（可选 `VLLM_API_KEY`） |
| `CUSTOM_API_BASE` | （空） | 自定义 OpenAI-compatible 端点（需同时设置 `CUSTOM_MODEL`） |
| `CUSTOM_API_KEY` | （空） | 自定义端点的可选 Key |

模型请求会按“请求输入 > 对应 provider 环境变量 > provider 默认值”解析；桌面端
设置面板的 Key 按 provider 分开存储，并优先走 Electron safeStorage。Gemini 和
Anthropic 的环境变量已纳入目录，但当前后端尚未接入其原生 transport，不会伪装成可用。

### 多源检索

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SEARCH_PROVIDERS` | `arxiv,openalex,crossref` | 启用的检索源（逗号分隔） |
| `SEARCH_PROVIDER_TIMEOUT_SECONDS` | `15` | 单源超时（秒） |
| `SEARCH_CACHE_TTL_SECONDS` | `86400` | 缓存 TTL（秒） |
| `SEARCH_CACHE_MAX_BYTES` | `268435456` | 搜索缓存总容量上限（字节） |
| `SEARCH_CACHE_MAX_FILES` | `500` | 搜索缓存文件数上限，按最旧优先清理 |
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
| `ARXIV_AGENT_DATA_DIR` | 平台用户数据目录 | 运行期数据根目录（桌面端设为 userData/backend-data） |

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

打包后内置 Python runtime + site-packages + `pyproject.toml`：
最终用户开箱即用，**不依赖目标机器安装 Python 或 uv**。
桌面端 SetupWizard 的依赖诊断面向源码/开发部署；仅当使用系统 Python
（而非内置 runtime）运行时，才需要按其给出的 `uv sync` 命令恢复依赖。

## 后端测试

```bash
# 项目根目录
pytest tests/backend -q
```

当前后端测试覆盖：健康检查 / 错误协议 / 线程 CRUD + 持久化 / running 崩溃恢复与 schema 迁移 /
检索事件序列 / 结构化 papers / evidence 链路 / 取消令牌与任务实例绑定 / 导出 / 依赖探测 /
删除与重命名竞态 / 原子缓存与 PDF 下载 / 正文缓存内容身份 / 中文排序去重 / 多 provider Key 与
endpoint 解析 / 多源部分失败状态与 canonical record 合并。

Playwright E2E（7 用例）覆盖：检索完成 / 慢检索取消 / sticky 停止等待 / 乱序详情不覆盖当前会话 /
切换线程并重命名删除，以及真实 FastAPI 后端全链路（检索 → 正文证据 → 导出 → 关闭重启 → 历史恢复）
与 7860 端口占用 fail-fast；两个 spec 均独占 7860，串行执行（workers=1）。

## 已知限制

- Qdrant local mode 适合单进程桌面运行；多进程并发时建议改用独立 Qdrant server。
- RAG 依赖不可用时自动降级为轻量 TF-IDF，不会中断主流程。
- 取消令牌不能中断已在飞的 HTTP 请求，只能在边界退出（同步 + requests 固有限制）。
- Electron safeStorage 在无 keyring 的 Linux 不可用 → 明文回退 + UI 标注。
- evidence chunk 文本截断到 500 字展示。
- 页码/章节信息依赖 PDF 文本层的页边界和保守标题识别；扫描版 PDF 仍可能没有可靠元数据。
- 引用校验能确认“标题 + 分块号”是否命中已保存 evidence，但不能替代人工核对论断的语义真实性。

## License

MIT
