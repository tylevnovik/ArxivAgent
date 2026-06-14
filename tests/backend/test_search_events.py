"""检索事件流：验证 NDJSON 事件序列、结构化 papers、报告。"""
import json


def _drain_ndjson(response):
    """把 StreamingResponse 的所有行解析成事件列表。"""
    events = []
    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        events.append(json.loads(line))
    return events


def test_message_stream_event_sequence(client, mock_full_search):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST",
        f"/api/threads/{t['id']}/messages",
        json={"query": "retrieval augmented generation", "api_key": "sk-test"},
    ) as resp:
        assert resp.status_code == 200
        events = _drain_ndjson(resp)

    types = [e["type"] for e in events]
    # 必须有终态
    assert "done" in types, f"缺少 done 终态: {types}"
    # 第一条是 intent（启动提示）
    assert types[0] == "intent"
    # 期间应出现 thinking（parse/review 流）和 searching
    assert "searching" in types or "searching_done" in types
    # 报告应被发出
    assert "report" in types


def test_done_event_carries_structured_papers(client, mock_full_search):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST",
        f"/api/threads/{t['id']}/messages",
        json={"query": "RAG survey", "api_key": "sk-test"},
    ) as resp:
        events = _drain_ndjson(resp)

    done_events = [e for e in events if e["type"] == "done"]
    assert done_events, "缺少 done 事件"
    payload = done_events[-1].get("payload") or {}
    assert payload.get("kind") == "search"
    papers = payload.get("papers") or []
    assert len(papers) >= 1
    p = papers[0]
    # 结构化字段（不再是 markdown）
    assert p["title"]
    assert isinstance(p["authors"], list)
    assert p["link"].startswith("http")
    # evidence 数组必须存在（即便 mock 无 retriever 时为空）
    assert "evidence" in payload
    assert isinstance(payload["evidence"], list)


def test_thread_detail_includes_evidence(client, mock_full_search):
    """线程详情的 evidence 字段必须存在（契约稳定性）。"""
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST",
        f"/api/threads/{t['id']}/messages",
        json={"query": "RAG", "api_key": "sk-test"},
    ):
        pass
    d = client.get(f"/api/threads/{t['id']}").json()
    assert "evidence" in d
    assert isinstance(d["evidence"], list)


def test_done_event_carries_structured_evidence(client, mock_search_providers, monkeypatch):
    """
    端到端验证 evidence 链路：用一个带 retriever 的 agent 让 _step_report
    把 retrieved chunks 保存进 memory，最终 done 事件应携带结构化 evidence。
    """
    import app as appmod
    from core.agent import ArxivAgent

    # 让 _build_rag_retriever 返回一个 retriever，retrieve() 返回固定切片
    class FakeRetriever:
        chunks = [{"text": "x"}]
        index_summary = "fake"

        def retrieve(self, query, top_k=6):
            return [
                {
                    "paper_title": "Evidence Paper",
                    "arxiv_id": "2401.00001",
                    "chunk_index": 2,
                    "text": "支持论断的正文片段。" * 3,
                    "retrieval_sources": ["dense", "bm25"],
                    "dense_score": 0.8,
                    "bm25_score": 2.5,
                    "hybrid_score": 0.0001,
                    "score": 0.0001,
                }
            ]

    monkeypatch.setattr(
        ArxivAgent, "_build_rag_retriever", lambda self, chunks: (FakeRetriever(), "fake")
    )
    # PDF 下载在测试环境会失败；mock 成返回占位 chunk，确保进入 RAG 建库分支
    from core import pdf_parser
    monkeypatch.setattr(
        pdf_parser,
        "process_paper_pdf",
        lambda title, pdf_link: [{"paper_title": title, "arxiv_id": "2405.00001",
                                  "chunk_index": 0, "text": "placeholder chunk"}],
    )
    # 复用 mock_full_search 的 LLM 响应
    import core.llm as llm
    import json as _json
    parse = _json.dumps({
        "arxiv_query": "rag", "keywords": ["rag"], "categories": ["cs.CL"],
        "strategy": "s", "sort_by": "relevance", "max_results": 3,
    })
    review = _json.dumps({
        "review_summary": "ok", "relevant_papers": [{"index": 0, "reason": "r"}],
        "overall_quality": 0.9, "should_refine": False, "refine_reason": "",
        "refine_suggestions": [],
    })

    def _stream(messages, api_key=None, base_url=None, model=None, cancel_event=None):
        uc = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
        if "最终检索报告" in uc or "报告要求" in uc:
            for tok in "# Evidence Report".split():
                yield tok + " "
            return
        elif "审核" in uc:
            text = review
        else:
            text = parse
        for tok in text.split():
            yield tok + " "

    monkeypatch.setattr(llm, "stream_chat", _stream)

    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST",
        f"/api/threads/{t['id']}/messages",
        json={"query": "RAG evidence", "api_key": "sk-test"},
    ) as resp:
        events = _drain_ndjson(resp)

    done = [e for e in events if e["type"] == "done"][-1]
    evidence = (done.get("payload") or {}).get("evidence") or []
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["paper_title"] == "Evidence Paper"
    assert ev["arxiv_id"] == "2401.00001"
    assert ev["chunk_index"] in (2, "2")
    assert ev["text"]
    assert "dense" in ev["retrieval_sources"]

    # 线程详情也应持久化 evidence
    d = client.get(f"/api/threads/{t['id']}").json()
    assert len(d["evidence"]) == 1
    assert d["evidence"][0]["paper_title"] == "Evidence Paper"


def test_thread_detail_after_search(client, mock_full_search):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST",
        f"/api/threads/{t['id']}/messages",
        json={"query": "RAG", "api_key": "sk-test"},
    ):
        pass  # 消费完整条流

    d = client.get(f"/api/threads/{t['id']}").json()
    assert d["status"] in ("done", "error")
    # 报告应已落盘
    assert "Mock Final Report" in d["report"]


def test_thread_papers_endpoint(client, mock_full_search):
    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST",
        f"/api/threads/{t['id']}/messages",
        json={"query": "RAG", "api_key": "sk-test"},
    ):
        pass

    r = client.get(f"/api/threads/{t['id']}/papers")
    assert r.status_code == 200
    papers = r.json()["papers"]
    assert len(papers) >= 1
