"""
检索边界场景。

codex 后端测试要求覆盖"空结果"：当所有检索源都返回 0 篇论文时，
流程应正常走到 done 终态（空 papers + 报告），而不是崩溃或卡在 error。
"""
import json


def _drain_ndjson(response):
    events = []
    for line in response.iter_lines():
        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        events.append(json.loads(line))
    return events


def test_empty_search_result_returns_done_not_error(
    isolated_data_dir, mock_full_search, mock_empty_search, monkeypatch
):
    """
    检索源返回空：终态必须是 done（而非 error），papers 为空数组，线程 status=done。
    mock_full_search 提供 LLM（parse/review/report）链路；
    mock_empty_search 覆盖检索源返回 0 篇。
    """
    import importlib
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    with TestClient(appmod.app) as client:
        t = client.post("/api/threads", json={"title": None}).json()["thread"]
        with client.stream(
            "POST",
            f"/api/threads/{t['id']}/messages",
            json={"query": "极其冷门不存在的主题", "api_key": "sk-test"},
        ) as resp:
            assert resp.status_code == 200
            events = _drain_ndjson(resp)

    types = [e["type"] for e in events]
    # 关键断言：终态是 done，不是 error（无结果不是失败）
    assert "done" in types, f"空结果场景缺少 done 终态: {types}"
    assert "error" not in types, f"空结果不应产生 error 事件: {types}"

    done = [e for e in events if e["type"] == "done"][-1]
    payload = done.get("payload") or {}
    # papers 必须是空数组（结构化），不能缺失
    assert payload.get("papers") == [], f"空结果场景 papers 应为空: {payload.get('papers')}"

    # 线程持久化：status=done，无 last_error
    d = client.get(f"/api/threads/{t['id']}").json()
    assert d["status"] == "done"
    assert d["last_error"] is None
    assert d["papers"] == []


def test_empty_search_report_still_generated(
    isolated_data_dir, mock_full_search, mock_empty_search
):
    """即便 0 篇论文，报告阶段仍应产出报告文本（降级为仅依据摘要，即无）。"""
    import importlib
    import app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient

    with TestClient(appmod.app) as client:
        t = client.post("/api/threads", json={"title": None}).json()["thread"]
        with client.stream(
            "POST",
            f"/api/threads/{t['id']}/messages",
            json={"query": "empty topic", "api_key": "sk-test"},
        ) as resp:
            events = _drain_ndjson(resp)

    # report 事件应出现（流式报告 token）
    assert any(e["type"] == "report" for e in events), "空结果场景未生成报告事件"


def test_llm_string_scalars_and_one_based_index_do_not_crash(
    client, mock_search_providers, monkeypatch
):
    """
    真实模型常把 JSON 数字/布尔/论文序号输出成字符串，且会按“论文 1”
    返回 1-based index。流程必须容错，不应进入 error 终态。
    """
    import core.llm as llm

    parse_response = json.dumps({
        "arxiv_query": "retrieval augmented generation survey",
        "keywords": ["retrieval augmented generation", "survey"],
        "categories": ["cs.CL"],
        "strategy": "broad",
        "sort_by": "relevance",
        "max_results": "5",
    })
    review_response = json.dumps({
        "review_summary": "ok",
        "relevant_papers": [{"index": "1", "title": "Mock Paper on RAG", "reason": "direct"}],
        "overall_quality": "0.82",
        "should_refine": "false",
        "refine_reason": "",
        "refine_suggestions": [],
    })
    report_text = "# String Scalar Report\n\nok"

    def _stream(messages, api_key=None, base_url=None, model=None, cancel_event=None):
        user_content = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
        if "最终检索报告" in user_content or "报告要求" in user_content:
            text = report_text
        elif "审核" in user_content:
            text = review_response
        else:
            text = parse_response
        for token in text.split():
            yield token + " "

    monkeypatch.setattr(llm, "stream_chat", _stream)

    t = client.post("/api/threads", json={"title": None}).json()["thread"]
    with client.stream(
        "POST",
        f"/api/threads/{t['id']}/messages",
        json={"query": "2026 RAG survey", "api_key": "sk-test"},
    ) as resp:
        events = _drain_ndjson(resp)

    types = [e["type"] for e in events]
    assert "error" not in types
    done = [e for e in events if e["type"] == "done"][-1]
    papers = (done.get("payload") or {}).get("papers") or []
    assert papers
    assert papers[0]["title"] == "Mock Paper on RAG"
