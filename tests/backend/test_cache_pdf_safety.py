import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

def test_search_cache_is_atomic_quarantines_bad_files_and_prunes(isolated_data_dir, monkeypatch):
    import config
    from core.search_service import SearchService

    service = SearchService(providers=["arxiv"])
    monkeypatch.setattr(config, "SEARCH_CACHE_MAX_FILES", 2)
    monkeypatch.setattr(config, "SEARCH_CACHE_MAX_BYTES", 1024 * 1024)

    service._write_cache("one", [{"title": "one"}])
    service._write_cache("two", [{"title": "two"}])
    service._write_cache("three", [{"title": "three"}])

    cache_files = [name for name in os.listdir(config.SEARCH_CACHE_DIR) if name.endswith(".json")]
    assert len(cache_files) <= 2
    assert not any(name.endswith(".tmp") for name in os.listdir(config.SEARCH_CACHE_DIR))

    broken = service._cache_path("broken")
    with open(broken, "w", encoding="utf-8") as f:
        f.write("{" )
    assert service._read_cache("broken") is None
    assert not os.path.exists(broken)
    assert any(name.startswith("broken.json.") and name.endswith(".bad")
               for name in os.listdir(config.SEARCH_CACHE_DIR))


def test_pdf_download_is_serialized_and_preserves_previous_file(isolated_data_dir, monkeypatch):
    import config
    import core.pdf_parser as pdf_parser

    payload = b"pdf-data-" + (b"x" * 2000)
    calls = 0
    calls_lock = threading.Lock()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            yield payload

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return Response()

    monkeypatch.setattr(pdf_parser.requests, "get", fake_get)
    with ThreadPoolExecutor(max_workers=6) as pool:
        paths = list(pool.map(
            lambda _: pdf_parser.download_pdf("https://arxiv.org/pdf/2401.00001.pdf", "2401.00001"),
            range(6),
        ))

    assert len(set(paths)) == 1
    assert calls == 1
    assert open(paths[0], "rb").read() == payload
    assert not any(name.endswith(".tmp") for name in os.listdir(config.PDF_CACHE_DIR))

    previous = b"previous-partial-pdf"
    with open(paths[0], "wb") as f:
        f.write(previous)

    def failing_get(*_args, **_kwargs):
        raise OSError("network down")

    monkeypatch.setattr(pdf_parser.requests, "get", failing_get)
    with pytest.raises(RuntimeError, match="下载 PDF 失败"):
        pdf_parser.download_pdf("https://arxiv.org/pdf/2401.00001.pdf", "2401.00001")
    assert open(paths[0], "rb").read() == previous


def test_process_pdf_keeps_page_section_and_source_metadata(isolated_data_dir, monkeypatch):
    import core.pdf_parser as pdf_parser

    monkeypatch.setattr(pdf_parser, "download_pdf", lambda *_: "ignored.pdf")
    monkeypatch.setattr(
        pdf_parser,
        "_extract_pages_from_pdf",
        lambda *_: [(3, "INTRODUCTION\n\nThis is evidence from page three.")],
    )

    chunks = pdf_parser.process_paper_pdf(
        "A paper", "https://arxiv.org/pdf/2401.00001v2.pdf"
    )

    assert len(chunks) == 1
    assert chunks[0]["page_number"] == 3
    assert chunks[0]["page_end"] == 3
    assert chunks[0]["section_title"] == "INTRODUCTION"
    assert chunks[0]["source_url"] == "https://arxiv.org/abs/2401.00001v2"
    assert chunks[0]["pdf_url"].endswith("2401.00001v2.pdf")


def test_search_reports_partial_provider_failures(isolated_data_dir):
    from core.arxiv_search import SearchError, SearchErrorType
    from core.search_service import ProviderOutcome, SearchService

    class GoodProvider:
        name = "good"

        def search(self, **_kwargs):
            return ProviderOutcome(
                source=self.name,
                papers=[{"title": "one", "source": self.name, "source_id": "1"}],
            )

    class BrokenProvider:
        name = "broken"

        def search(self, **_kwargs):
            return ProviderOutcome(
                source=self.name,
                papers=[],
                error=SearchError(SearchErrorType.TIMEOUT, "provider timeout"),
            )

    service = SearchService(providers=["arxiv"])
    service.providers = [GoodProvider(), BrokenProvider()]
    result = service.search(
        arxiv_query="one", natural_query="one", max_results=5, sort_by="relevance"
    )

    assert result.success is True
    assert [item["source"] for item in result.provider_statuses] == ["good", "broken"]
    assert result.provider_statuses[0]["ok"] is True
    assert result.provider_statuses[1]["ok"] is False
    assert result.provider_statuses[1]["error_type"] == "timeout"
