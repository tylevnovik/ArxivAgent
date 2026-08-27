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


# ===================== 追问的全文 RAG（重建索引 + 正文回答） =====================

def _install_followup_mocks(monkeypatch, queries_seen, build_retriever):
    """装好 parse/review/report + 追问意图的 LLM mock、PDF mock、retriever mock。"""
    import core.llm as llm
    import json as _json
    from core import pdf_parser
    from core.agent import ArxivAgent

    class FakeRetriever:
        chunks = [{"text": "x"}]
        index_summary = "fake"

        def retrieve(self, query, top_k=6):
            queries_seen.append(query)
            return [{
                "paper_title": "Evidence Paper",
                "arxiv_id": "2401.00001",
                "chunk_index": 2,
                "text": "支持论断的正文片段。",
                "retrieval_sources": ["dense"],
                "score": 0.5,
            }]

    monkeypatch.setattr(ArxivAgent, "_build_rag_retriever", build_retriever(FakeRetriever))
    monkeypatch.setattr(
        pdf_parser,
        "process_paper_pdf",
        lambda title, pdf_link: [{"paper_title": title, "arxiv_id": "2405.00001",
                                  "chunk_index": 0, "text": "placeholder chunk"}],
    )

    parse = _json.dumps({
        "arxiv_query": "retrieval augmented generation",
        "keywords": ["retrieval"],
        "strategy": "broad", "sort_by": "relevance", "max_results": 3,
    })
    review = _json.dumps({
        "review_summary": "ok", "relevant_papers": [{"index": 0, "reason": "r"}],
        "overall_quality": 0.9, "should_refine": False, "refine_reason": "",
        "refine_suggestions": [],
    })
    followup_intent = _json.dumps({
        "intent": "discuss_results",
        "analysis": "用户在讨论结果",
        "needs_search": False,
        "search_query": "",
        "response": "INLINE_RESPONSE_SHOULD_NOT_BE_USED",
    })
    captured = {"followup_prompts": []}

    def _stream(messages, api_key=None, base_url=None, model=None, cancel_event=None):
        uc = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
        if "请直接用中文回复" in uc:
            # 追问回答阶段（应携带 RAG 正文切片）
            captured["followup_prompts"].append(uc)
            for tok in "基于正文的回答".split():
                yield tok + " "
            return
        if "你的任务" in uc:
            # 首轮也会做意图识别：chat() 已把本轮用户消息写进历史，
            # 首轮历史里不会有"[助手]"；有助手回复的才是追问意图调用。
            if "[助手]" not in uc:
                text = parse
            else:
                text = followup_intent
        elif "报告要求" in uc or "最终检索报告" in uc:
            for tok in "# Mock Report".split():
                yield tok + " "
            return
        elif "审核" in uc:
            text = review
        else:
            text = parse
        for tok in text.split():
            yield tok + " "

    monkeypatch.setattr(llm, "stream_chat", _stream)
    return captured


def test_discuss_followup_rebuilds_retriever_and_uses_rag(client, monkeypatch):
    """discuss_results 追问：新 agent 重建索引，回答带正文依据（不用内联 response）。"""
    queries_seen: list[str] = []
    captured = _install_followup_mocks(
        monkeypatch, queries_seen,
        build_retriever=lambda cls: (lambda self, chunks: (cls(), "fake")),
    )

    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    # 第一条：完整检索（产出 final_papers 与正文索引）
    with client.stream(
        "POST", f"/api/threads/{t['id']}/messages",
        json={"query": "RAG survey", "api_key": "sk-test"},
    ) as resp:
        events1 = _drain_ndjson(resp)
    assert "done" in [e["type"] for e in events1]

    # 第二条：追问（每条消息都是新 agent，retriever 必须被重建）
    with client.stream(
        "POST", f"/api/threads/{t['id']}/messages",
        json={"query": "第3篇论文讲了什么", "api_key": "sk-test"},
    ) as resp:
        events2 = _drain_ndjson(resp)

    types = [e["type"] for e in events2]
    assert "chat" in types, f"缺少 chat 事件: {types}"
    assert "done" in types

    # retrieve 被追问文本调用过（多查询合并的一部分）
    assert any("第3篇论文讲了什么" in q for q in queries_seen), queries_seen

    # 回答 prompt 携带正文切片标记，且输出不是意图步骤的内联 response
    assert captured["followup_prompts"], "追问没有走到带 RAG 的流式回答"
    assert "【正文:" in captured["followup_prompts"][0]
    chat_text = "".join(e["message"] for e in events2 if e["type"] == "chat")
    assert "基于正文的回答" in chat_text
    assert "INLINE_RESPONSE_SHOULD_NOT_BE_USED" not in chat_text


def test_discuss_followup_falls_back_to_inline_when_rebuild_fails(client, monkeypatch):
    """索引重建失败时，追问退回意图步骤的内联回复（不报错）。"""
    queries_seen: list[str] = []

    def _broken(FakeRetriever):
        def _raise(self, chunks):
            raise RuntimeError("build failed")
        return _raise

    captured = _install_followup_mocks(monkeypatch, queries_seen, build_retriever=_broken)

    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST", f"/api/threads/{t['id']}/messages",
        json={"query": "RAG survey", "api_key": "sk-test"},
    ):
        pass

    with client.stream(
        "POST", f"/api/threads/{t['id']}/messages",
        json={"query": "第3篇论文讲了什么", "api_key": "sk-test"},
    ) as resp:
        events = _drain_ndjson(resp)

    types = [e["type"] for e in events]
    assert "error" not in types, f"重建失败不应报错: {events}"
    assert "chat" in types and "done" in types
    chat_text = "".join(e["message"] for e in events if e["type"] == "chat")
    assert "INLINE_RESPONSE_SHOULD_NOT_BE_USED" in chat_text
    # 重建失败 → 没有走带 RAG 的回答
    assert captured["followup_prompts"] == []


def test_pdf_phase_emits_per_paper_progress_events(client, monkeypatch):
    """多篇论文的正文解析应有逐篇进度事件（含 2/2 的完成进度）。"""
    import core.llm as llm
    import json as _json
    from core import pdf_parser
    from core.agent import ArxivAgent
    from core.search_service import SearchService, SearchResult

    paper1 = {
        "title": "First Mock Paper", "authors": ["A"], "abstract": "x",
        "categories": ["cs.CL"], "published": "2024-05-01", "updated": "2024-05-02",
        "link": "https://arxiv.org/abs/2405.00001",
        "pdf_link": "https://arxiv.org/pdf/2405.00001",
        "arxiv_id": "2405.00001", "source": "arxiv", "source_id": "2405.00001",
        "doi": "", "citation_count": 1, "score": 0.9,
    }
    paper2 = dict(paper1)
    paper2["title"] = "Second Mock Paper"
    paper2["arxiv_id"] = "2405.00002"
    paper2["link"] = "https://arxiv.org/abs/2405.00002"
    paper2["pdf_link"] = "https://arxiv.org/pdf/2405.00002"

    def _two_papers(self, *, arxiv_query, natural_query, max_results,
                    sort_by="relevance", cancel_event=None):
        return SearchResult(success=True, papers=[dict(paper1), paper2],
                            query_used=arxiv_query)

    monkeypatch.setattr(SearchService, "search", _two_papers)
    monkeypatch.setattr(
        pdf_parser, "process_paper_pdf",
        lambda title, pdf_link: [{"paper_title": title, "arxiv_id": "x",
                                  "chunk_index": 0, "text": "chunk"}],
    )

    class _FakeNoChunks:
        chunks = []

        def retrieve(self, query, top_k=6):
            return []

    monkeypatch.setattr(
        ArxivAgent, "_build_rag_retriever", lambda self, chunks: (_FakeNoChunks(), "fake")
    )

    parse = _json.dumps({"arxiv_query": "rag", "keywords": ["rag"],
                         "strategy": "s", "sort_by": "relevance", "max_results": 5})
    review = _json.dumps({
        "review_summary": "ok",
        "relevant_papers": [{"index": 1, "reason": "r"}, {"index": 2, "reason": "r"}],
        "overall_quality": 0.9, "should_refine": False, "refine_reason": "",
        "refine_suggestions": [],
    })

    def _stream(messages, api_key=None, base_url=None, model=None, cancel_event=None):
        uc = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
        if "报告要求" in uc or "最终检索报告" in uc:
            for tok in "# Report".split():
                yield tok + " "
            return
        text = review if "审核" in uc else parse
        for tok in text.split():
            yield tok + " "

    monkeypatch.setattr(llm, "stream_chat", _stream)

    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST", f"/api/threads/{t['id']}/messages",
        json={"query": "RAG", "api_key": "sk-test"},
    ) as resp:
        events = _drain_ndjson(resp)

    progress = [e["message"] for e in events
                if e["type"] == "intent" and "正文解析进度" in e.get("message", "")]
    assert len(progress) == 2, progress
    assert any("2/2" in m for m in progress), progress
