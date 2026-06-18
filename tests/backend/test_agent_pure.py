"""
agent.py 纯函数单元测试。

_select_relevant_papers 把 LLM 审核结果映射回原始论文列表，
_coerce_* 系列把不可信的 LLM JSON 值规整为安全数值。
这些是 agent 主循环里最容易出回归 bug 的地方，独立锁定行为契约。
"""
import pytest

from core.agent import (
    _coerce_bool,
    _coerce_float,
    _coerce_int,
    _normalize_title,
    _select_relevant_papers,
)


# ===================== _coerce_int =====================

class TestCoerceInt:
    def test_plain_int(self):
        assert _coerce_int(5, 10) == 5

    def test_string_int(self):
        assert _coerce_int("7", 10) == 7

    def test_float_string(self):
        assert _coerce_int("3.9", 10) == 3

    def test_invalid_falls_back(self):
        assert _coerce_int("abc", 10) == 10
        assert _coerce_int(None, 10) == 10

    def test_min_clamp(self):
        assert _coerce_int(-5, 10, min_value=1) == 1

    def test_max_clamp(self):
        assert _coerce_int(100, 10, max_value=20) == 20

    def test_strips_whitespace(self):
        assert _coerce_int("  42 ", 10) == 42


# ===================== _coerce_float =====================

class TestCoerceFloat:
    def test_plain_float(self):
        assert _coerce_float(0.85, 1.0) == 0.85

    def test_string_float(self):
        assert _coerce_float("0.5", 1.0) == 0.5

    def test_percent_string(self):
        # "85%" 应转成 0.85（>1 且以 % 结尾）
        assert _coerce_float("85%", 1.0) == 0.85

    def test_small_percent_not_divided(self):
        # 0.5% 不应被除以 100（值 <= 1）
        assert _coerce_float("0.5%", 1.0) == 0.5

    def test_invalid_falls_back(self):
        assert _coerce_float("abc", 0.5) == 0.5

    def test_clamp(self):
        assert _coerce_float(1.5, 1.0, min_value=0.0, max_value=1.0) == 1.0
        assert _coerce_float(-0.5, 1.0, min_value=0.0, max_value=1.0) == 0.0


# ===================== _coerce_bool =====================

class TestCoerceBool:
    @pytest.mark.parametrize("val", [True, "true", "1", "yes", "y", "是", "需要", 1, 1.0])
    def test_truthy(self, val):
        assert _coerce_bool(val) is True

    @pytest.mark.parametrize("val", [False, "false", "0", "no", "n", "否", "不需要", 0, 0.0])
    def test_falsy(self, val):
        assert _coerce_bool(val) is False

    def test_unknown_falls_back(self):
        assert _coerce_bool("maybe", False) is False
        assert _coerce_bool("maybe", True) is True

    def test_none_falls_back(self):
        assert _coerce_bool(None, False) is False


# ===================== _normalize_title =====================

def test_normalize_title():
    assert _normalize_title("  Graph   Neural ") == "graph neural"
    assert _normalize_title(None) == ""
    assert _normalize_title(123) == "123"


# ===================== _select_relevant_papers =====================

PAPERS = [
    {"title": "Alpha Paper", "arxiv_id": "1"},
    {"title": "Beta Paper", "arxiv_id": "2"},
    {"title": "Gamma Paper", "arxiv_id": "3"},
]


class TestSelectRelevantPapers:
    def test_index_based_1based(self):
        # index: 1 → 第 1 篇（1-based）
        result = _select_relevant_papers([{"index": 1}], PAPERS)
        assert len(result) == 1
        assert result[0]["title"] == "Alpha Paper"

    def test_index_based_0based(self):
        # index: 0 → 第 1 篇（0 也视为第 1 篇）
        result = _select_relevant_papers([{"index": 0}], PAPERS)
        assert result[0]["title"] == "Alpha Paper"

    def test_index_out_of_range_skipped(self):
        result = _select_relevant_papers([{"index": 99}], PAPERS)
        assert result == []

    def test_title_match(self):
        result = _select_relevant_papers([{"title": "beta paper"}], PAPERS)
        assert len(result) == 1
        assert result[0]["title"] == "Beta Paper"

    def test_dedup_repeated_selections(self):
        # 同一篇被选两次只出现一次
        result = _select_relevant_papers([{"index": 1}, {"index": 1}], PAPERS)
        assert len(result) == 1

    def test_none_reviewed_returns_all(self):
        # reviewed_papers=None → 保留全部（保守策略）
        assert _select_relevant_papers(None, PAPERS) == PAPERS

    def test_non_list_reviewed_returns_empty(self):
        # reviewed_papers 非 None 且非 list → 空（解析异常时更安全）
        assert _select_relevant_papers("invalid", PAPERS) == []

    def test_empty_reviewed_returns_empty(self):
        assert _select_relevant_papers([], PAPERS) == []

    def test_skips_non_dict_items(self):
        result = _select_relevant_papers([{"index": 2}, "junk", 42], PAPERS)
        assert len(result) == 1
        assert result[0]["title"] == "Beta Paper"

    def test_string_index_parsed(self):
        # index 以字符串形式给出（LLM 常见）
        result = _select_relevant_papers([{"index": "3"}], PAPERS)
        assert result[0]["title"] == "Gamma Paper"
