"""
真实后端 E2E harness（T7）。

启动**真实** FastAPI app（路由、worker、持久化、HMAC 认证链全部保留），
仅替换外部边界，避免真实付费模型 / 网络检索源 / PDF 下载 / embedding 模型下载：

- core.llm.stream_chat        → 脚本化 parse/review/report 输出
- SearchService.search        → 固定论文列表
- pdf_parser.process_paper_pdf→ 固定正文分块（含页码/章节元数据）
- core.rag._get_embedding_provider → 确定性伪向量（32 维），RAG 全链路真实
- RAG_QDRANT_LOCATION         → :memory:（进程内，测试间自然隔离）

数据目录：ARXIV_AGENT_E2E_DATA_DIR（Playwright 测试传入的全新临时目录）。
用法（由 desktop/tests/e2e/backend-flow.spec.ts 经 Electron 主进程 spawn）：
  python backend_harness.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

# 仓库根（config.py / core/ 所在目录）加入 sys.path：脚本可能从任意 cwd 启动
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

# 数据目录必须在 import config 之前就位
DATA_DIR = os.environ.get("ARXIV_AGENT_E2E_DATA_DIR")
if not DATA_DIR:
    print("[harness] missing ARXIV_AGENT_E2E_DATA_DIR", flush=True)
    sys.exit(2)
os.environ["ARXIV_AGENT_DATA_DIR"] = os.path.abspath(DATA_DIR)
os.environ.setdefault("ARXIV_AGENT_HOST", "127.0.0.1")
os.environ.setdefault("ARXIV_AGENT_PORT", "7860")

import config  # noqa: E402

config.RAG_QDRANT_LOCATION = ":memory:"
config.RAG_ENABLE_RERANKER = False

import core.llm as llm  # noqa: E402
from core.search_service import SearchService, SearchResult  # noqa: E402
from core import pdf_parser  # noqa: E402
from core import rag as rag_mod  # noqa: E402

# ===================== LLM 边界 =====================

PARSE_RESPONSE = json.dumps(
    {
        "arxiv_query": "transformer attention mechanism",
        "keywords": ["transformer", "attention"],
        "categories": ["cs.CL"],
        "strategy": "broad keyword search",
        "sort_by": "relevance",
        "max_results": 5,
    },
    ensure_ascii=False,
)
REVIEW_RESPONSE = json.dumps(
    {
        "review_summary": "All mock papers are relevant.",
        "relevant_papers": [{"index": 0, "reason": "directly on topic"}],
        "overall_quality": 0.9,
        "should_refine": False,
        "refine_reason": "",
        "refine_suggestions": [],
    },
    ensure_ascii=False,
)
REPORT_TEXT = (
    "# E2E 检索报告\n\n"
    "Transformer 架构以注意力机制为核心【正文: Attention Is All You Need | 分块 0】。"
)


def fake_stream_chat(messages, api_key=None, base_url=None, model=None, cancel_event=None):
    user_content = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
    if "最终检索报告" in user_content or "报告要求" in user_content or "最终报告" in user_content:
        text = REPORT_TEXT
    elif "审核" in user_content or "result_review" in user_content:
        text = REVIEW_RESPONSE
    else:
        text = PARSE_RESPONSE
    for token in text.split(" "):
        if cancel_event is not None and cancel_event.is_set():
            from core.llm import CancelledError

            raise CancelledError("harness cancel")
        yield token + " "


llm.stream_chat = fake_stream_chat

# ===================== 检索源边界 =====================

MOCK_PAPER = {
    "title": "Attention Is All You Need",
    "authors": ["Vaswani", "Shakeer"],
    "abstract": "We propose a new architecture based solely on attention mechanisms.",
    "categories": ["cs.CL"],
    "published": "2017-06-12",
    "updated": "2017-06-12",
    "link": "https://arxiv.org/abs/1706.03762",
    "pdf_link": "https://arxiv.org/pdf/1706.03762",
    "arxiv_id": "1706.03762",
    "source": "arxiv",
    "source_id": "1706.03762",
    "doi": "",
    "citation_count": 100000,
    "score": 0.99,
    "reason": "foundational",
}


def fake_search(self, *, arxiv_query, natural_query, max_results, sort_by="relevance", cancel_event=None):
    return SearchResult(success=True, papers=[dict(MOCK_PAPER)], query_used=arxiv_query)


SearchService.search = fake_search

# ===================== PDF 边界 =====================

CHUNK_TEXT = (
    "The dominant sequence transduction models are based on complex recurrent or "
    "convolutional neural networks. We propose a new simple network architecture, "
    "the Transformer, based solely on attention mechanisms. "
) * 6


def fake_process_paper_pdf(title: str, pdf_link: str) -> list[dict]:
    arxiv_id = "1706.03762"
    return [
        {
            "paper_title": title,
            "arxiv_id": arxiv_id,
            "chunk_index": 0,
            "page_number": 2,
            "page_end": 2,
            "section_title": "1. Introduction",
            "source_url": "https://arxiv.org/abs/1706.03762",
            "pdf_url": pdf_link,
            "text": CHUNK_TEXT,
        }
    ]


pdf_parser.process_paper_pdf = fake_process_paper_pdf

# ===================== Embedding 边界（确定性伪向量） =====================


class FakeEmbeddingProvider:
    model_name = "fake-e2e-embedding-v1"
    dimension = 32

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [b / 255.0 for b in digest[: self.dimension]]
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vec(query or "")


rag_mod._get_embedding_provider = lambda model_name=None: FakeEmbeddingProvider()

# ===================== 启动真实 app =====================

import app as appmod  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("ARXIV_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("ARXIV_AGENT_PORT", "7860"))
    print(f"[harness] starting real backend on {host}:{port} data_dir={config.DATA_DIR}", flush=True)
    uvicorn.run(appmod.app, host=host, port=port)
