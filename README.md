# 🔍 多源论文检索 Agent

基于 DeepSeek 大模型 API 驱动的智能论文检索代理，支持 arXiv、OpenAlex、Crossref 等开放学术数据源。

当前版本：`v0.2.0`

## 功能特性

- 🗣️ **自然语言检索**：用中文描述检索需求，Agent 自动理解并构造检索式
- 🔄 **智能迭代优化**：自动审核检索结果，不满意时迭代优化检索策略
- 🌐 **多源检索**：支持 arXiv、OpenAlex、Crossref、Semantic Scholar，并带本地缓存、去重与轻量排序
- 📡 **实时流式展示**：Agent 思考过程、检索状态实时流式展示
- 🧠 **检索记忆**：记住历史检索轮次，避免重复无效检索
- 💬 **意图识别**：区分闲聊、检索、修正和结果追问，避免无意义检索
- 📚 **混合正文 RAG**：使用本地 FastEmbed 向量化、Qdrant 向量库、BM25S 关键词索引和 RRF 融合，在最终报告和后续追问中引用论文正文片段
- 📤 **多格式导出**：支持 Markdown、CSV、JSON 格式导出对话和结果
- 🎨 **Web UI**：基于 FastAPI + 单页前端的现代化交互界面

## 项目结构

```
ArxivAgent/
├── prompts/             # 提示词模板（纯文本，中文）
│   ├── system.txt       # 系统提示词
│   ├── query_parse.txt  # 需求理解 & 构造检索式
│   ├── result_review.txt# 审核检索结果
│   ├── refine_query.txt # 优化检索策略
│   └── summary.txt      # 生成最终报告
├── core/                # 核心模块
│   ├── llm.py           # DeepSeek API 封装
│   ├── arxiv_search.py  # arXiv API 封装
│   ├── memory.py        # 对话 & 检索记忆
│   ├── agent.py         # Agent 主循环
│   └── exporter.py      # 导出功能
├── index.html           # 单页前端
├── app.py               # FastAPI 服务入口
├── config.py            # 配置管理
└── requirements.txt     # 依赖
```

## Agent 工作流程

```
用户输入 → [理解需求] → 构造检索式 → [多源检索] → [审核结果]
                                                       ↓
                                           满意？→ 否 → [优化策略] → 重新检索
                                                       ↓
                                                       是 → [生成报告] → 完成
```

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # Linux/Mac

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python app.py
```

访问 `http://localhost:7860` 使用。

## 配置

编辑 `config.py` 可修改：
- DeepSeek API Key 和模型配置
- arXiv / OpenAlex / Crossref 检索参数
- 最大检索轮次

也可以通过 `.env` 调整：

```bash
DEEPSEEK_API_KEY=你的key
SEARCH_PROVIDERS=arxiv,openalex,crossref
SEARCH_PROVIDER_TIMEOUT_SECONDS=15
SEARCH_CACHE_TTL_SECONDS=86400
OPENALEX_MAILTO=your-email@example.com
CROSSREF_MAILTO=your-email@example.com
SEMANTIC_SCHOLAR_API_KEY=可选
```

如需启用 Semantic Scholar，可把 `SEARCH_PROVIDERS` 改为：

```bash
SEARCH_PROVIDERS=arxiv,openalex,semantic_scholar,crossref
```

### RAG 配置

默认 RAG 使用本地方案，不需要 OpenAI API Key，也不要求 Docker：

- `FastEmbed` 负责本地 embedding，默认模型为 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- `Qdrant local mode` 负责 dense vector 检索，默认数据目录为 `.cache/qdrant`
- `BM25S` 负责关键词召回
- `RRF` 负责融合向量召回和 BM25 召回
- 可选 reranker 默认关闭，避免首次下载和推理成本过高

可通过 `.env` 调整：

```bash
RAG_RETRIEVER_TYPE=hybrid
RAG_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RAG_QDRANT_LOCATION=.cache/qdrant
RAG_TOP_K=6
RAG_DENSE_CANDIDATES=20
RAG_BM25_CANDIDATES=20
RAG_RRF_K=60
RAG_ENABLE_RERANKER=false
RAG_RERANKER_MODEL=BAAI/bge-reranker-base
```

如果本地机器暂时无法安装或下载 Qdrant/FastEmbed/BM25S 依赖，Agent 会自动降级为轻量 TF-IDF RAG，不会中断主流程。首次运行本地 embedding 模型时需要下载模型文件，之后会走本机缓存。

注意：Qdrant local mode 适合单进程桌面/本地运行；如果多个 Python 进程同时访问同一个 `.cache/qdrant` 目录，Qdrant 会加锁。此时 Agent 会自动退到 Qdrant `:memory:` 继续使用 hybrid 检索。需要多进程或多人并发部署时，建议改用独立 Qdrant server，并把 `RAG_QDRANT_LOCATION` 配成服务 URL。
