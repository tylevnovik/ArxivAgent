"""
ArXiv 论文检索 Agent 配置管理
"""
import os

import platformdirs
from dotenv import load_dotenv

# 加载本地 .env 文件
load_dotenv()

# 应用版本
APP_VERSION = "0.4.0"

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"

# arXiv API 配置
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_RATE_LIMIT_SECONDS = 3  # arXiv 要求至少 3 秒间隔

# 多源检索配置
SEARCH_PROVIDERS = [
    p.strip().lower()
    for p in os.environ.get("SEARCH_PROVIDERS", "arxiv,openalex,crossref").split(",")
    if p.strip()
]
SEARCH_PROVIDER_TIMEOUT_SECONDS = float(os.environ.get("SEARCH_PROVIDER_TIMEOUT_SECONDS", "15"))
SEARCH_CACHE_TTL_SECONDS = int(os.environ.get("SEARCH_CACHE_TTL_SECONDS", str(24 * 60 * 60)))
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "")
CROSSREF_MAILTO = os.environ.get("CROSSREF_MAILTO", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# Agent 配置
MAX_SEARCH_ROUNDS = 3         # 最大检索迭代轮次
MAX_RESULTS_PER_ROUND = 10    # 每轮最大返回结果数
MAX_ERROR_RECOVERY = 2        # 检索错误恢复最大尝试次数

# 提示词模板目录
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# 运行期数据目录
#
# 默认放用户数据目录（~/.share/arxivagent、~/Library/Application Support/arxivagent、
# %APPDATA%/arxivagent），避免运行期文件污染源码树、且打包后写到只读目录。
# 可用环境变量 ARXIV_AGENT_DATA_DIR 覆盖（Electron 桌面端就是这么做的，指向 userData）。
DATA_DIR = os.path.abspath(
    os.environ.get(
        "ARXIV_AGENT_DATA_DIR",
        platformdirs.user_data_dir("arxivagent", "arxivagent"),
    )
)
os.makedirs(DATA_DIR, exist_ok=True)

# 导出目录
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

# 检索缓存目录
SEARCH_CACHE_DIR = os.path.join(DATA_DIR, ".cache", "search")
os.makedirs(SEARCH_CACHE_DIR, exist_ok=True)

# PDF 缓存目录
PDF_CACHE_DIR = os.path.join(DATA_DIR, "pdf_cache")
os.makedirs(PDF_CACHE_DIR, exist_ok=True)

# 线程持久化目录（每个线程一个 JSON）
THREADS_DIR = os.path.join(DATA_DIR, "threads")
os.makedirs(THREADS_DIR, exist_ok=True)

# RAG 检索配置
# 默认使用本地 FastEmbed + Qdrant local mode + BM25S 混合检索；依赖不可用时自动降级为 TF-IDF。
RAG_RETRIEVER_TYPE = os.environ.get("RAG_RETRIEVER_TYPE", "hybrid").strip().lower()
RAG_EMBEDDING_MODEL = os.environ.get(
    "RAG_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
RAG_QDRANT_LOCATION = os.environ.get(
    "RAG_QDRANT_LOCATION",
    os.path.join(DATA_DIR, ".cache", "qdrant"),
)
RAG_QDRANT_COLLECTION_PREFIX = os.environ.get("RAG_QDRANT_COLLECTION_PREFIX", "arxiv_agent_rag")
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "6"))
RAG_DENSE_CANDIDATES = int(os.environ.get("RAG_DENSE_CANDIDATES", "20"))
RAG_BM25_CANDIDATES = int(os.environ.get("RAG_BM25_CANDIDATES", "20"))
RAG_RRF_K = int(os.environ.get("RAG_RRF_K", "60"))
RAG_DENSE_WEIGHT = float(os.environ.get("RAG_DENSE_WEIGHT", "1.0"))
RAG_BM25_WEIGHT = float(os.environ.get("RAG_BM25_WEIGHT", "1.0"))
RAG_ENABLE_RERANKER = os.environ.get("RAG_ENABLE_RERANKER", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
RAG_RERANKER_MODEL = os.environ.get("RAG_RERANKER_MODEL", "BAAI/bge-reranker-base")
