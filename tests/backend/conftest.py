"""
后端测试共享 fixture。

关键策略：
- 用 monkeypatch 替换 core.llm.stream_chat 和各 provider.search，避免真实网络/LLM 调用。
- 用 tmp_path 覆盖 config.DATA_DIR / THREADS_DIR / EXPORT_DIR，保证测试隔离。
- 重置 core.threads.thread_manager 单例状态。
"""
import json
import os
import shutil
from pathlib import Path

import pytest


# ===================== 数据目录隔离 =====================

@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """把 config 的所有运行期目录指向临时目录，并 reload 受影响的模块状态。"""
    import config
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "THREADS_DIR", str(data_dir / "threads"), raising=False)
    (data_dir / "threads").mkdir(exist_ok=True)
    export_dir = data_dir / "exports"
    export_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "EXPORT_DIR", str(export_dir), raising=False)
    monkeypatch.setattr(config, "SEARCH_CACHE_DIR", str(data_dir / ".cache" / "search"), raising=False)
    monkeypatch.setattr(config, "PDF_CACHE_DIR", str(data_dir / "pdf_cache"), raising=False)
    yield str(data_dir)


@pytest.fixture(autouse=True)
def reset_thread_manager():
    """每个测试前清空运行期任务索引，避免跨测试污染。"""
    from core.threads import thread_manager
    thread_manager._tasks.clear()
    yield
    thread_manager._tasks.clear()


# ===================== Mock 数据 =====================

MOCK_PAPER = {
    "title": "Mock Paper on RAG",
    "authors": ["Alice Author", "Bob Builder"],
    "abstract": "This is a mock abstract about retrieval augmented generation.",
    "categories": ["cs.CL", "cs.AI"],
    "published": "2024-05-01",
    "updated": "2024-05-02",
    "link": "https://arxiv.org/abs/2405.00001",
    "pdf_link": "https://arxiv.org/pdf/2405.00001",
    "arxiv_id": "2405.00001",
    "source": "arxiv",
    "source_id": "2405.00001",
    "doi": "",
    "citation_count": 42,
    "score": 0.9,
}


# ===================== Mock LLM =====================

def make_mock_stream_chat(responses_by_prompt):
    """
    构造一个假的 stream_chat：根据 messages 内容匹配预设响应，逐 token yield。
    responses_by_prompt: dict，key 是出现在 user content 里的子串，value 是完整响应文本。
    """
    def _mock(messages, api_key=None, base_url=None, model=None, cancel_event=None):
        # 找到 user content
        user_content = ""
        for m in messages:
            if m.get("role") == "user":
                user_content += m.get("content", "")
        # 匹配预设
        for needle, text in responses_by_prompt.items():
            if needle in user_content:
                for token in text.split():
                    yield token + " "
                return
        # 默认：返回一段 JSON-ish 文本
        for token in '{"arxiv_query":"mock","keywords":["mock"],"strategy":"s","sort_by":"relevance","max_results":5}'.split():
            yield token + " "
    return _mock


@pytest.fixture
def mock_llm_parse(monkeypatch):
    """mock query_parse 阶段的 LLM 输出（返回结构化检索式）。"""
    parse_response = json.dumps({
        "arxiv_query": "retrieval augmented generation",
        "keywords": ["retrieval", "augmented", "generation"],
        "categories": ["cs.CL"],
        "strategy": "broad keyword search",
        "sort_by": "relevance",
        "max_results": 5,
    })
    import core.llm as llm
    monkeypatch.setattr(llm, "stream_chat", make_mock_stream_chat({
        # query_parse 提示词里有"理解需求"语义；用 user_query 本身匹配
        "检索": parse_response,
        "RAG": parse_response,
        "retrieval": parse_response,
    }))
    return parse_response


@pytest.fixture
def mock_full_search(monkeypatch):
    """
    mock 整条检索链路：parse → review → report。
    review 让 should_refine=False 直接进入报告阶段。
    """
    parse_response = json.dumps({
        "arxiv_query": "retrieval augmented generation",
        "keywords": ["retrieval"],
        "categories": ["cs.CL"],
        "strategy": "broad",
        "sort_by": "relevance",
        "max_results": 5,
    })
    review_response = json.dumps({
        "review_summary": "All mock papers are relevant.",
        "relevant_papers": [{"index": 0, "reason": "directly on topic"}],
        "overall_quality": 0.9,
        "should_refine": False,
        "refine_reason": "",
        "refine_suggestions": [],
    })
    report_text = "# Mock Final Report\n\nBased on the mock search, here is the summary."

    def _stream(messages, api_key=None, base_url=None, model=None, cancel_event=None):
        user_content = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
        # 优先匹配最具体的标记，避免 review/summary 互相误命中
        if "最终检索报告" in user_content or "报告要求" in user_content or "最终报告" in user_content:
            # 报告阶段：直接 yield 纯文本（非 JSON）
            for token in report_text.split():
                yield token + " "
            return
        elif "审核" in user_content or "result_review" in user_content:
            text = review_response
        else:
            text = parse_response
        for token in text.split():
            yield token + " "

    import core.llm as llm
    monkeypatch.setattr(llm, "stream_chat", _stream)
    return {"parse": parse_response, "review": review_response, "report": report_text}


# ===================== Mock 检索源 =====================

@pytest.fixture
def mock_search_providers(monkeypatch):
    """让 SearchService.search 直接返回固定论文，绕过真实 HTTP。"""
    from core.search_service import SearchService, SearchResult

    def _fake_search(self, *, arxiv_query, natural_query, max_results, sort_by="relevance", cancel_event=None):
        return SearchResult(
            success=True,
            papers=[dict(MOCK_PAPER)],
            query_used=arxiv_query,
        )

    monkeypatch.setattr(SearchService, "search", _fake_search)
    return MOCK_PAPER


@pytest.fixture
def mock_empty_search(monkeypatch):
    """
    模拟检索源返回 0 篇论文（success=True 但 papers 为空，
    带 EMPTY_RESULT 可恢复错误，与真实"所有源都没结果"路径一致）。
    用于验证"无结果"是正常终态，不应崩溃或报错。
    """
    from core.search_service import SearchService, SearchResult
    from core.arxiv_search import SearchError, SearchErrorType

    def _empty_search(self, *, arxiv_query, natural_query, max_results, sort_by="relevance", cancel_event=None):
        return SearchResult(
            success=True,
            papers=[],
            query_used=arxiv_query,
            error=SearchError(
                SearchErrorType.EMPTY_RESULT,
                "所有已启用检索源均未返回结果",
                recoverable=True,
            ),
        )

    monkeypatch.setattr(SearchService, "search", _empty_search)
    return []


# ===================== TestClient =====================

@pytest.fixture
def client(isolated_data_dir, mock_search_providers):
    """FastAPI TestClient。每个测试独立数据目录 + mock 检索。"""
    # 延迟 import app，确保 config 已被 monkeypatch
    import importlib
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    # reload 后 thread_manager 单例是新的；重置 fixture 仍会清它
    with TestClient(appmod.app) as c:
        yield c
