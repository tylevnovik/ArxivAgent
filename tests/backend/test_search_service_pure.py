"""
search_service.py 纯函数单元测试。

这些函数（年份提取、去重排序、arXiv 语法简化）不涉及网络，是回归 bug 高发区。
单独测它们可以在不 mock 任何 provider 的情况下锁定行为契约。
"""
from core.search_service import (
    _append_filter,
    _extract_year_bounds,
    _paper_identity,
    _paper_score,
    _provider_query,
    _rank_and_dedupe,
    _simplify_arxiv_query,
    _title_query_from_arxiv,
    _tokens,
)


# ===================== _extract_year_bounds =====================

class TestExtractYearBounds:
    def test_submitted_date_range_12digit(self):
        # arXiv 标准 12 位（YYYYMMDDHHMM），prompt 教 LLM 输出的格式
        lo, hi = _extract_year_bounds(
            "test", 'ti:rag submittedDate:[202001010000 TO 202212310000]'
        )
        assert lo == 2020 and hi == 2022

    def test_submitted_date_range_8digit(self):
        # 健壮性：兼容 8 位（YYYYMMDD）
        lo, hi = _extract_year_bounds("test", 'submittedDate:[20200101 TO 20221231]')
        assert lo == 2020 and hi == 2022

    def test_submitted_date_range_14digit(self):
        # 健壮性：兼容 14 位（YYYYMMDDHHMMSS）
        lo, hi = _extract_year_bounds(
            "test", 'submittedDate:[20200101000000 TO 20221231235959]'
        )
        assert lo == 2020 and hi == 2022

    def test_since_marker(self):
        lo, hi = _extract_year_bounds("rag", "rag 2020 之后")
        assert lo == 2020 and hi is None

    def test_before_marker(self):
        lo, hi = _extract_year_bounds("rag", "rag before 2019")
        assert lo is None and hi == 2019

    def test_no_year(self):
        assert _extract_year_bounds("rag survey", "retrieval augmented generation") == (None, None)

    def test_multiple_years_with_since(self):
        # "2020 之后" 且文本里出现多个年份 → 取最小
        lo, hi = _extract_year_bounds("2021 和 2020 之后的研究")
        assert lo == 2020 and hi is None


# ===================== _simplify_arxiv_query =====================

class TestSimplifyArxivQuery:
    def test_strips_field_prefixes(self):
        # ti:/abs:/au: 前缀被去掉，AND 被去掉，但词序与引号保留
        result = _simplify_arxiv_query('ti:"retrieval" AND abs:"generation"')
        assert "retrieval" in result and "generation" in result
        assert "ti:" not in result and "abs:" not in result
        assert "AND" not in result

    def test_strips_submitted_date(self):
        result = _simplify_arxiv_query("rag submittedDate:[20200101000000 TO 20221231235959]")
        assert "submittedDate" not in result
        assert "rag" in result

    def test_strips_operators(self):
        result = _simplify_arxiv_query("(retrieval OR generation) ANDNOT survey")
        assert "OR" not in result and "ANDNOT" not in result
        assert "retrieval" in result and "generation" in result

    def test_empty(self):
        assert _simplify_arxiv_query("") == ""


# ===================== _title_query_from_arxiv =====================

class TestTitleQueryFromArxiv:
    def test_quoted_title(self):
        assert _title_query_from_arxiv('ti:"graph neural networks"') == "graph neural networks"

    def test_bare_term(self):
        assert _title_query_from_arxiv("ti:transformer") == "transformer"

    def test_stops_at_boolean(self):
        # AND/OR 不应作为标题词
        result = _title_query_from_arxiv("ti:rag ti:survey AND ti:other")
        assert "AND" not in result.split()


# ===================== _provider_query =====================

class TestProviderQuery:
    def test_combines_natural_and_simplified(self):
        result = _provider_query("retrieval augmented", 'ti:"rag"')
        assert "retrieval" in result and "rag" in result

    def test_natural_only(self):
        result = _provider_query("transformer survey", "")
        assert result == "transformer survey"

    def test_truncates_long_query(self):
        long = "word " * 200
        result = _provider_query(long, "")
        assert len(result) <= 300

    def test_empty_inputs(self):
        assert _provider_query("", "") == ""


# ===================== _rank_and_dedupe =====================

def _paper(title="P", doi="", arxiv_id="", abstract="", citation_count=0, source="arxiv", pdf_link=""):
    return {
        "title": title,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "abstract": abstract,
        "citation_count": citation_count,
        "source": source,
        "pdf_link": pdf_link,
    }


class TestRankAndDedupe:
    def test_dedup_by_doi(self):
        papers = [
            _paper(title="A", doi="10.1/a"),
            _paper(title="A duplicate", doi="10.1/A"),  # 大小写不同视为同一
        ]
        result = _rank_and_dedupe(papers, "query", 10)
        assert len(result) == 1

    def test_dedup_by_arxiv_id(self):
        papers = [
            _paper(title="B1", arxiv_id="2401.00001"),
            _paper(title="B2", arxiv_id="2401.00001"),
        ]
        assert len(_rank_and_dedupe(papers, "q", 10)) == 1

    def test_dedup_by_normalized_title(self):
        # 无 doi/arxiv_id 时按归一化标题去重
        papers = [
            _paper(title="Graph  Neural  Networks!"),
            _paper(title="graph neural networks"),
        ]
        assert len(_rank_and_dedupe(papers, "q", 10)) == 1

    def test_title_overlap_ranks_higher(self):
        papers = [
            _paper(title="irrelevant topic"),  # query 词不命中
            _paper(title="retrieval augmented generation survey"),  # query 词命中
        ]
        result = _rank_and_dedupe(papers, "retrieval augmented generation", 10)
        assert result[0]["title"].startswith("retrieval")

    def test_max_results_truncation(self):
        papers = [_paper(title=f"P{i}") for i in range(10)]
        assert len(_rank_and_dedupe(papers, "q", 3)) == 3

    def test_citation_boost(self):
        # 标题都不命中 query，引用数高的应排前
        papers = [
            _paper(title="same", citation_count=0),
            _paper(title="same high cited", citation_count=1000),
        ]
        result = _rank_and_dedupe(papers, "nonexistentterm", 10)
        assert result[0]["citation_count"] == 1000

    def test_preserves_input_not_mutated(self):
        papers = [_paper(title="P", doi="10.1/x")]
        _rank_and_dedupe(papers, "q", 10)
        # 原始 dict 不应被加上 score 字段
        assert "score" not in papers[0]


# ===================== _paper_identity / _tokens / _append_filter =====================

class TestPaperIdentity:
    def test_doi_priority(self):
        p = _paper(title="T", doi="10.1/x", arxiv_id="2401.1")
        assert _paper_identity(p) == "doi:10.1/x"

    def test_arxiv_id_fallback(self):
        assert _paper_identity(_paper(title="T", arxiv_id="2401.1")) == "arxiv:2401.1"

    def test_title_fallback(self):
        assert _paper_identity(_paper(title="Some Title!")) == "title:some title"

    def test_empty_identity(self):
        assert _paper_identity(_paper(title="")) == ""


def test_tokens_filters_short():
    # search_service._tokens 只匹配 [a-zA-Z0-9]+，长度 <= 2 的被丢弃，中文不匹配
    # （中文分词用 rag.py 的 tokenize，不是这里）
    result = _tokens("a ab abc 12 123 中文")
    assert "a" not in result and "ab" not in result
    assert "abc" in result and "123" in result
    assert "中文" not in result  # 中文不在 _tokens 处理范围


def test_append_filter():
    assert _append_filter("", "new") == "new"
    assert _append_filter("existing", "new") == "existing,new"
