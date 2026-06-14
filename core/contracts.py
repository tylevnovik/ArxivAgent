"""
对外产品契约（Pydantic 模型）。

这里定义的是 API 边界上稳定的数据形状，是前后端的"产品契约层"。
内部 core.agent.AgentEvent / core.memory.Memory 不直接对外暴露，
所有端点响应都先映射成本模块里的模型再序列化。

设计目标：
- 前端不再靠解析 markdown 或猜 status_text 文案推断状态。
- 错误一律走 ErrorResponse + ErrorCode 枚举，避免文案漂移。
- 论文以结构化字段返回，前端停掉 emoji markdown 反解析。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ===================== 论文 =====================

class Paper(BaseModel):
    """结构化论文。字段对齐 search_service 实际产出的 paper dict 全部 key。"""

    title: str = ""
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    categories: list[str] = Field(default_factory=list)
    published: str = ""
    updated: str = ""
    link: str = ""
    pdf_link: str = ""
    arxiv_id: str = ""
    source: str = "arxiv"
    source_id: str = ""
    doi: str = ""
    citation_count: int = 0
    score: float = 0.0
    # 何时被本轮检索选中 / 为什么推荐，由审核或排序阶段填充（可空）
    reason: str = ""

    model_config = {"extra": "allow"}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Paper":
        if not data:
            return cls()
        return cls(
            title=str(data.get("title", "") or ""),
            authors=list(data.get("authors", []) or []),
            abstract=str(data.get("abstract", "") or ""),
            categories=list(data.get("categories", []) or []),
            published=str(data.get("published", "") or ""),
            updated=str(data.get("updated", "") or ""),
            link=str(data.get("link", "") or ""),
            pdf_link=str(data.get("pdf_link", "") or ""),
            arxiv_id=str(data.get("arxiv_id", "") or ""),
            source=str(data.get("source", "arxiv") or "arxiv"),
            source_id=str(data.get("source_id", "") or ""),
            doi=str(data.get("doi", "") or ""),
            citation_count=int(data.get("citation_count", 0) or 0),
            score=float(data.get("score", 0.0) or 0.0),
            reason=str(data.get("reason", "") or ""),
        )


# ===================== 证据切片 =====================

class EvidenceChunk(BaseModel):
    """
    报告引用证据：一个命中的 RAG 正文切片。
    前端据此把报告里的 【正文: 标题 | 分块 N】 标记渲染成可点击 chip。
    """

    paper_title: str = ""
    arxiv_id: str = ""
    chunk_index: str = "N/A"
    text: str = ""
    retrieval_sources: list[str] = Field(default_factory=list)
    # 各类检索分数（dense/bm25/hybrid/rerank，可空则 0）
    dense_score: float = 0.0
    bm25_score: float = 0.0
    hybrid_score: float = 0.0
    rerank_score: float = 0.0
    score: float = 0.0

    model_config = {"extra": "allow"}

    @field_validator("chunk_index", mode="before")
    @classmethod
    def _coerce_chunk_index(cls, v):
        # 数据源（pdf_parser/rag）可能是 int 或 str；统一成 str。
        return str(v) if v is not None else "N/A"

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "EvidenceChunk":
        if not data:
            return cls()
        return cls(
            paper_title=str(data.get("paper_title", "") or ""),
            arxiv_id=str(data.get("arxiv_id", "") or ""),
            chunk_index=str(data.get("chunk_index", "N/A") or "N/A"),
            # 展示时截断超长正文，避免单条事件过大
            text=str(data.get("text", "") or "")[:500],
            retrieval_sources=list(data.get("retrieval_sources", []) or []),
            dense_score=float(data.get("dense_score", 0.0) or 0.0),
            bm25_score=float(data.get("bm25_score", 0.0) or 0.0),
            hybrid_score=float(data.get("hybrid_score", 0.0) or 0.0),
            rerank_score=float(data.get("rerank_score", 0.0) or 0.0),
            score=float(data.get("score", 0.0) or 0.0),
        )


# ===================== 线程 =====================

# 线程 / 任务的可观测状态。前端据此渲染气泡终态与侧栏徽标。
ThreadStatus = Literal["idle", "running", "done", "error", "cancelled"]


class ThreadMeta(BaseModel):
    """线程摘要，用于列表展示。不含消息正文。"""

    id: str
    title: str
    status: ThreadStatus = "idle"
    created_at: str
    updated_at: str
    papers_count: int = 0
    has_report: bool = False
    message_count: int = 0
    last_error: Optional[str] = None


class ChatMessage(BaseModel):
    """线程内一条对话消息。"""

    id: Optional[str] = None
    persisted_index: Optional[int] = None
    role: Literal["user", "assistant"]
    content: str
    timestamp: str = ""
    # 渲染提示：这条助手消息是思考步骤（折叠展示）/ 报告 / 普通回复
    kind: Literal["text", "thinking", "report", "status", "error"] = "text"


class ThreadDetail(BaseModel):
    """线程详情：元信息 + 消息 + 结构化论文 + 报告 + 证据切片。"""

    id: str
    title: str
    status: ThreadStatus = "idle"
    created_at: str
    updated_at: str
    messages: list[ChatMessage] = Field(default_factory=list)
    papers: list[Paper] = Field(default_factory=list)
    report: str = ""
    evidence: list[EvidenceChunk] = Field(default_factory=list)
    last_error: Optional[str] = None


class ThreadCreateRequest(BaseModel):
    title: Optional[str] = None


class ThreadCreateResponse(BaseModel):
    ok: bool = True
    thread: ThreadMeta


class ThreadListResponse(BaseModel):
    ok: bool = True
    threads: list[ThreadMeta]


class ThreadPatchRequest(BaseModel):
    title: str


class MessagePatchRequest(BaseModel):
    content: str


# ===================== 事件 =====================

# 稳定的事件类型枚举。前端 reducer 严格按此 dispatch。
# 与 core.agent.EventType (内部) 的映射在 app.py._envelope_from_agent_event 完成。
EventType = Literal[
    "intent",       # 意图识别 / 步骤提示
    "thinking",     # LLM 思考流式 token（累积展示）
    "searching",    # 开始一轮检索
    "searching_done",  # 一轮检索结束（可带 papers）
    "reviewing",    # 审核结果
    "refining",     # 策略优化
    "report",       # 最终报告流式 token（累积展示）
    "chat",         # 多轮对话回复流式 token（累积展示）
    "papers",       # 结构化论文数组（最终推荐，可重复发送替换）
    "done",         # 任务正常完成（终态）
    "error",        # 任务出错（终态）
    "cancelled",    # 任务被取消（终态）
]


class AgentEventEnvelope(BaseModel):
    """NDJSON 流里每一行的事件信封。"""

    type: EventType
    message: str = ""
    payload: Optional[dict[str, Any]] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    # 方便前端区分轮次（可空）
    round: Optional[int] = None


class MessageRequest(BaseModel):
    """向线程提交一条用户消息触发检索/对话。"""

    query: str
    # 运行期配置；为空则用线程/进程已有配置
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    max_search_rounds: Optional[int] = None
    max_results_per_round: Optional[int] = None
    providers: Optional[list[str]] = None
    openalex_mailto: Optional[str] = None
    crossref_mailto: Optional[str] = None
    semantic_scholar_api_key: Optional[str] = None


# ===================== 错误 =====================

class ErrorCode(str, Enum):
    """稳定错误码。前端按 code 决定如何提示与重试。"""

    NO_API_KEY = "no_api_key"
    INVALID_PROVIDER = "invalid_provider"
    LLM_ERROR = "llm_error"
    SEARCH_ERROR = "search_error"
    EXPORT_EMPTY = "export_empty"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    CANCELLED = "cancelled"
    THREAD_BUSY = "thread_busy"
    VALIDATION = "validation"
    INTERNAL = "internal"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    recoverable: bool = True


class ErrorResponse(BaseModel):
    ok: bool = False
    error: ErrorDetail


# ===================== 配置健康检查 =====================

class ProviderHealth(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class ConfigHealth(BaseModel):
    """设置页"测试连接"的返回。不暴露 key 本身。"""

    ok: bool = True
    api_key_configured: bool = False
    api_key_source: Literal["request", "env", "none"] = "none"
    provider: str = ""
    endpoint: str = ""
    model: str = ""
    data_dir: str = ""
    llm_reachable: Optional[bool] = None
    llm_detail: str = ""
    providers: list[ProviderHealth] = Field(default_factory=list)
    encryption_available: Optional[bool] = None  # 由前端 safeStorage 状态填，后端留空


class ConfigHealthRequest(BaseModel):
    """测试连接的入参（用表单当前值，不依赖已保存配置）。"""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    providers: Optional[list[str]] = None
    openalex_mailto: Optional[str] = None
    crossref_mailto: Optional[str] = None
    semantic_scholar_api_key: Optional[str] = None
    ping_llm: bool = True


# ===================== 导出 =====================

class ExportRequest(BaseModel):
    type: Literal["chat", "md", "csv", "json", "report"]
    thread_id: Optional[str] = None  # 新契约：按线程导出
    # 旧契约兼容：直接传 history（仅 /api/export 旧端点用）
    history: Optional[list[dict]] = None
    session_id: Optional[str] = None


class ExportResponse(BaseModel):
    ok: bool = True
    filename: Optional[str] = None
    status: str = ""


class CancelResponse(BaseModel):
    ok: bool = True
    status: str = "cancel requested"


class HealthResponse(BaseModel):
    ok: bool = True
    version: str = ""
