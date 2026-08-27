"""RAG 模块纯函数测试：不依赖 Qdrant / fastembed / 网络。"""
from core.pdf_parser import chunk_text
from core.rag import _collection_name, _prune_stale_collections, merge_retrievals


# ===================== merge_retrievals =====================

class TestMergeRetrievals:
    def test_empty_inputs(self):
        assert merge_retrievals([], 5) == []
        assert merge_retrievals([[], []], 5) == []
        assert merge_retrievals([None, []], 5) == []

    def test_dedupe_by_chunk_id_keeps_highest_score(self):
        a = [{"chunk_id": "c1", "text": "t", "score": 0.4}]
        b = [
            {"chunk_id": "c1", "text": "t", "score": 0.9},
            {"chunk_id": "c2", "text": "u", "score": 0.5},
        ]
        merged = merge_retrievals([a, b], 5)
        assert [m["chunk_id"] for m in merged] == ["c1", "c2"]
        assert merged[0]["score"] == 0.9

    def test_dedupe_fallback_for_results_without_chunk_id(self):
        # TF-IDF 结果没有 chunk_id，用 (paper_title, chunk_index) 去重
        a = [{"paper_title": "P", "chunk_index": 0, "score": 0.3}]
        b = [{"paper_title": "P", "chunk_index": 0, "score": 0.7}]
        merged = merge_retrievals([a, b], 5)
        assert len(merged) == 1
        assert merged[0]["score"] == 0.7

    def test_top_k_truncation_sorted_desc(self):
        results = [[{"chunk_id": f"c{i}", "score": i * 0.1} for i in range(10)]]
        merged = merge_retrievals(results, 3)
        assert [m["chunk_id"] for m in merged] == ["c9", "c8", "c7"]

    def test_missing_score_treated_as_zero(self):
        a = [{"chunk_id": "x"}]
        b = [{"chunk_id": "x", "score": 0.1}]
        merged = merge_retrievals([a, b], 5)
        assert merged[0]["score"] == 0.1


# ===================== _prune_stale_collections =====================

class _FakeCollection:
    def __init__(self, name):
        self.name = name


class _FakeQdrantClient:
    def __init__(self, names, fail_names=()):
        self.names = list(names)
        self.fail_names = set(fail_names)

    def get_collections(self):
        class _R:
            pass
        r = _R()
        r.collections = [_FakeCollection(n) for n in self.names]
        return r

    def delete_collection(self, name):
        if name in self.fail_names:
            raise RuntimeError("boom")
        self.names.remove(name)


class TestPruneStaleCollections:
    def test_only_same_prefix_and_not_in_keep(self):
        client = _FakeQdrantClient(
            ["arxiv_agent_rag_aaa", "arxiv_agent_rag_bbb", "other_ccc"]
        )
        deleted = _prune_stale_collections(client, "arxiv_agent_rag_", {"arxiv_agent_rag_aaa"})
        assert deleted == ["arxiv_agent_rag_bbb"]
        assert "other_ccc" in client.names
        assert "arxiv_agent_rag_aaa" in client.names

    def test_delete_failure_is_tolerated(self):
        client = _FakeQdrantClient(["p_1", "p_2"], fail_names={"p_1"})
        deleted = _prune_stale_collections(client, "p_", set())
        assert deleted == ["p_2"]

    def test_listing_failure_is_tolerated(self):
        class _Broken:
            def get_collections(self):
                raise RuntimeError("nope")
        assert _prune_stale_collections(_Broken(), "p_", set()) == []


# ===================== _collection_name =====================

class TestCollectionName:
    def test_stable_for_same_input(self):
        chunks = [{"chunk_id": "a"}, {"chunk_id": "b"}]
        assert _collection_name("pfx", chunks, "m") == _collection_name("pfx", chunks, "m")

    def test_changes_with_model_tag(self):
        chunks = [{"chunk_id": "a"}]
        assert _collection_name("pfx", chunks, "model-a") != _collection_name("pfx", chunks, "model-b")

    def test_changes_with_chunks(self):
        assert _collection_name("pfx", [{"chunk_id": "a"}], "m") != _collection_name(
            "pfx", [{"chunk_id": "b"}], "m"
        )

    def test_prefix(self):
        assert _collection_name("pfx", [{"chunk_id": "a"}], "m").startswith("pfx_")


# ===================== chunk_text（段落感知） =====================

class TestChunkText:
    def test_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   \n\n  ") == []

    def test_small_text_single_chunk(self):
        assert chunk_text("hello world") == ["hello world"]

    def test_paragraphs_kept_whole(self):
        p1 = ("alpha paragraph. " * 18).strip()
        p2 = ("beta paragraph. " * 18).strip()
        p3 = ("gamma paragraph. " * 18).strip()
        chunks = chunk_text(f"{p1}\n\n{p2}\n\n{p3}", chunk_size=600, chunk_overlap=100)
        assert len(chunks) >= 2
        joined = "\n\n".join(chunks)
        # 每个段落完整出现，不在中间被切断
        for p in (p1, p2, p3):
            assert p in joined

    def test_overlap_carries_tail_paragraph(self):
        p1 = "one " * 60      # ~240
        p2 = "two " * 60
        p1, p2 = p1.strip(), p2.strip()
        chunks = chunk_text(f"{p1}\n\n{p2}", chunk_size=260, chunk_overlap=300)
        assert len(chunks) == 2
        # 第二块应包含第一块的尾部段落（重叠）
        assert p1 in chunks[1]

    def test_huge_paragraph_hard_split(self):
        text = "x" * 2500
        chunks = chunk_text(text, chunk_size=1000, chunk_overlap=200)
        assert len(chunks) >= 3
        assert all(len(c) <= 1000 for c in chunks)
        # 内容无丢失：所有字符都被覆盖（以首字符序列抽查）
        assert chunks[0].startswith("x" * 1000)
