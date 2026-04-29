"""
ArXiv 论文检索 Agent 配置管理
"""
import os

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# arXiv API 配置
ARXIV_API_URL = "http://export.arxiv.org/api/query"
ARXIV_RATE_LIMIT_SECONDS = 3  # arXiv 要求至少 3 秒间隔

# Agent 配置
MAX_SEARCH_ROUNDS = 3         # 最大检索迭代轮次
MAX_RESULTS_PER_ROUND = 10    # 每轮最大返回结果数
MAX_ERROR_RECOVERY = 2        # 检索错误恢复最大尝试次数

# 提示词模板目录
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# 导出目录
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)
