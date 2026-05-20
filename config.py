"""
ArXiv 论文检索 Agent 配置管理
"""
import os
from dotenv import load_dotenv

# 加载本地 .env 文件
load_dotenv()

# 应用版本
APP_VERSION = "0.1.0"

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

# 导出目录
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

# 检索缓存目录
SEARCH_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "search")
os.makedirs(SEARCH_CACHE_DIR, exist_ok=True)

# PDF 缓存目录
PDF_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_cache")
os.makedirs(PDF_CACHE_DIR, exist_ok=True)
