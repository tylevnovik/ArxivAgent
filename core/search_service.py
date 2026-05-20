"""
多源论文检索服务。

这个模块把“去哪里搜、怎么限流、怎么缓存、怎么去重排序”从 Agent 主循环中拆出来。
Provider 之间保持同一种 paper dict 结构，方便现有 UI、导出和 LLM 审核逻辑继续复用。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import requests

import config
from core import arxiv_search
from core.arxiv_search import SearchError, SearchErrorType, SearchResult


@dataclass
class ProviderOutcome:
    """单个检索源的返回结果。"""
    source: str
    papers: list[dict]
    error: Optional[SearchError] = None
    cached: bool = False


class SearchProvider(ABC):
    """论文检索源接口。"""

    name: str

    @abstractmethod
    def search(
        self,
        *,
        arxiv_query: str,
        natural_query: str,
        max_results: int,
        sort_by: str,
    ) -> ProviderOutcome:
        pass


class ArxivProvider(SearchProvider):
    """原生 arXiv API provider。"""

    name = "arxiv"

    def search(
        self,
        *,
        arxiv_query: str,
        natural_query: str,
        max_results: int,
        sort_by: str,
    ) -> ProviderOutcome:
        result = arxiv_search.search(
            query=arxiv_query,
            max_results=max_results,
            sort_by=sort_by,
            max_retries=0,
        )
        papers = [_with_source(p, self.name) for p in result.papers]
        return ProviderOutcome(self.name, papers, result.error)


class OpenAlexProvider(SearchProvider):
    """OpenAlex works API provider。"""

    name = "openalex"
    base_url = "https://api.openalex.org/works"

    def __init__(self, mailto: str = ""):
        self.mailto = mailto.strip()

    def search(
        self,
        *,
        arxiv_query: str,
        natural_query: str,
        max_results: int,
        sort_by: str,
    ) -> ProviderOutcome:
        query = _provider_query(natural_query, arxiv_query)
        params = {
            "search": query,
            "per-page": max_results,
        }
        if sort_by in ("submittedDate", "lastUpdatedDate"):
            params["sort"] = "publication_date:desc"

        year_min, year_max = _extract_year_bounds(natural_query, arxiv_query)
        filters = []
        if year_min:
            filters.append(f"from_publication_date:{year_min}-01-01")
        if year_max:
            filters.append(f"to_publication_date:{year_max}-12-31")
        if filters:
            params["filter"] = ",".join(filters)

        mailto = self.mailto or getattr(config, "OPENALEX_MAILTO", "")
        if mailto:
            params["mailto"] = mailto

        try:
            response = requests.get(
                self.base_url,
                params=params,
                headers=_json_headers(),
                timeout=config.SEARCH_PROVIDER_TIMEOUT_SECONDS,
            )
            if response.status_code == 429:
                return ProviderOutcome(
                    self.name,
                    [],
                    SearchError(
                        SearchErrorType.RATE_LIMIT,
                        "OpenAlex API 触发限流",
                        http_status=response.status_code,
                        raw_response=response.text[:500],
                        recoverable=False,
                    ),
                )
            if response.status_code != 200:
                return ProviderOutcome(
                    self.name,
                    [],
                    SearchError(
                        SearchErrorType.HTTP_ERROR,
                        f"OpenAlex API 返回 HTTP {response.status_code}",
                        http_status=response.status_code,
                        raw_response=response.text[:500],
                        recoverable=response.status_code >= 500,
                    ),
                )

            items = response.json().get("results", [])
            title_query = _title_query_from_arxiv(arxiv_query)
            if title_query:
                title_params = params.copy()
                title_params.pop("search", None)
                title_params["filter"] = _append_filter(
                    title_params.get("filter", ""),
                    f"title.search:{title_query}",
                )
                title_response = requests.get(
                    self.base_url,
                    params=title_params,
                    headers=_json_headers(),
                    timeout=config.SEARCH_PROVIDER_TIMEOUT_SECONDS,
                )
                if title_response.status_code == 200:
                    items = title_response.json().get("results", []) + items
            return ProviderOutcome(
                self.name,
                [_normalize_openalex_work(item) for item in items if item.get("display_name")],
            )
        except requests.exceptions.Timeout:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(SearchErrorType.TIMEOUT, "OpenAlex API 请求超时", recoverable=True),
            )
        except requests.exceptions.ConnectionError as e:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(SearchErrorType.NETWORK, f"OpenAlex 网络连接错误: {e}", recoverable=True),
            )
        except (ValueError, KeyError, TypeError) as e:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(SearchErrorType.PARSE_ERROR, f"OpenAlex 响应解析失败: {e}", recoverable=False),
            )


class CrossrefProvider(SearchProvider):
    """Crossref works API provider。"""

    name = "crossref"
    base_url = "https://api.crossref.org/works"

    def __init__(self, mailto: str = ""):
        self.mailto = mailto.strip()

    def search(
        self,
        *,
        arxiv_query: str,
        natural_query: str,
        max_results: int,
        sort_by: str,
    ) -> ProviderOutcome:
        query = _provider_query(natural_query, arxiv_query)
        params = {
            "query.bibliographic": query,
            "rows": max_results,
        }
        if sort_by in ("submittedDate", "lastUpdatedDate"):
            params["sort"] = "published"
            params["order"] = "desc"

        year_min, year_max = _extract_year_bounds(natural_query, arxiv_query)
        filters = []
        if year_min:
            filters.append(f"from-pub-date:{year_min}-01-01")
        if year_max:
            filters.append(f"until-pub-date:{year_max}-12-31")
        if filters:
            params["filter"] = ",".join(filters)

        headers = _json_headers()
        mailto = self.mailto or getattr(config, "CROSSREF_MAILTO", "")
        if mailto:
            headers["User-Agent"] = f"ArxivAgent/1.0 (mailto:{mailto})"

        try:
            response = requests.get(
                self.base_url,
                params=params,
                headers=headers,
                timeout=config.SEARCH_PROVIDER_TIMEOUT_SECONDS,
            )
            if response.status_code == 429:
                return ProviderOutcome(
                    self.name,
                    [],
                    SearchError(
                        SearchErrorType.RATE_LIMIT,
                        "Crossref API 触发限流",
                        http_status=response.status_code,
                        raw_response=response.text[:500],
                        recoverable=False,
                    ),
                )
            if response.status_code != 200:
                return ProviderOutcome(
                    self.name,
                    [],
                    SearchError(
                        SearchErrorType.HTTP_ERROR,
                        f"Crossref API 返回 HTTP {response.status_code}",
                        http_status=response.status_code,
                        raw_response=response.text[:500],
                        recoverable=response.status_code >= 500,
                    ),
                )

            items = response.json().get("message", {}).get("items", [])
            return ProviderOutcome(
                self.name,
                [_normalize_crossref_work(item) for item in items if item.get("title")],
            )
        except requests.exceptions.Timeout:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(SearchErrorType.TIMEOUT, "Crossref API 请求超时", recoverable=True),
            )
        except requests.exceptions.ConnectionError as e:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(SearchErrorType.NETWORK, f"Crossref 网络连接错误: {e}", recoverable=True),
            )
        except (ValueError, KeyError, TypeError) as e:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(SearchErrorType.PARSE_ERROR, f"Crossref 响应解析失败: {e}", recoverable=False),
            )


class SemanticScholarProvider(SearchProvider):
    """Semantic Scholar Academic Graph paper search provider。"""

    name = "semantic_scholar"
    base_url = "https://api.semanticscholar.org/graph/v1/paper/search"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key.strip()

    def search(
        self,
        *,
        arxiv_query: str,
        natural_query: str,
        max_results: int,
        sort_by: str,
    ) -> ProviderOutcome:
        query = _provider_query(natural_query, arxiv_query)
        params = {
            "query": query,
            "limit": max_results,
            "fields": (
                "paperId,externalIds,title,authors,abstract,year,publicationDate,"
                "url,openAccessPdf,citationCount,fieldsOfStudy"
            ),
        }
        if sort_by in ("submittedDate", "lastUpdatedDate"):
            params["sort"] = "publicationDate:desc"

        headers = _json_headers()
        api_key = self.api_key or config.SEMANTIC_SCHOLAR_API_KEY
        if api_key:
            headers["x-api-key"] = api_key

        try:
            response = requests.get(
                self.base_url,
                params=params,
                headers=headers,
                timeout=config.SEARCH_PROVIDER_TIMEOUT_SECONDS,
            )
            if response.status_code == 429:
                return ProviderOutcome(
                    self.name,
                    [],
                    SearchError(
                        SearchErrorType.RATE_LIMIT,
                        "Semantic Scholar API 触发限流",
                        http_status=response.status_code,
                        raw_response=response.text[:500],
                        recoverable=False,
                    ),
                )
            if response.status_code != 200:
                return ProviderOutcome(
                    self.name,
                    [],
                    SearchError(
                        SearchErrorType.HTTP_ERROR,
                        f"Semantic Scholar API 返回 HTTP {response.status_code}",
                        http_status=response.status_code,
                        raw_response=response.text[:500],
                        recoverable=response.status_code >= 500,
                    ),
                )

            items = response.json().get("data", [])
            return ProviderOutcome(
                self.name,
                [_normalize_semantic_scholar_paper(item) for item in items if item.get("title")],
            )
        except requests.exceptions.Timeout:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(SearchErrorType.TIMEOUT, "Semantic Scholar API 请求超时", recoverable=True),
            )
        except requests.exceptions.ConnectionError as e:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(SearchErrorType.NETWORK, f"Semantic Scholar 网络连接错误: {e}", recoverable=True),
            )
        except (ValueError, KeyError, TypeError) as e:
            return ProviderOutcome(
                self.name,
                [],
                SearchError(
                    SearchErrorType.PARSE_ERROR,
                    f"Semantic Scholar 响应解析失败: {e}",
                    recoverable=False,
                ),
            )


class SearchService:
    """多源检索、缓存、去重和轻量排序。"""

    def __init__(
        self,
        providers: Optional[list[str]] = None,
        provider_settings: Optional[dict[str, str]] = None,
    ):
        provider_names = providers if providers else config.SEARCH_PROVIDERS
        self.provider_settings = provider_settings or {}
        self.providers = [
            _make_provider(name, self.provider_settings)
            for name in provider_names
            if str(name).strip()
        ]
        if not self.providers:
            self.providers = [_make_provider("arxiv", self.provider_settings)]

    def search(
        self,
        *,
        arxiv_query: str,
        natural_query: str,
        max_results: int,
        sort_by: str = "relevance",
    ) -> SearchResult:
        cache_key = self._cache_key(arxiv_query, natural_query, max_results, sort_by)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return SearchResult(success=True, papers=cached, query_used=arxiv_query)

        outcomes: list[ProviderOutcome] = []
        papers: list[dict] = []
        provider_limit = max(max_results * 3, 10)

        for provider in self.providers:
            outcome = provider.search(
                arxiv_query=arxiv_query,
                natural_query=natural_query,
                max_results=provider_limit,
                sort_by=sort_by,
            )
            outcomes.append(outcome)
            papers.extend(outcome.papers)

        ranked = _rank_and_dedupe(papers, natural_query, max_results)
        if ranked:
            self._write_cache(cache_key, ranked)
            return SearchResult(success=True, papers=ranked, query_used=arxiv_query)

        errors = [outcome.error for outcome in outcomes if outcome.error]
        if errors:
            return SearchResult(
                success=False,
                papers=[],
                error=_merge_errors(errors),
                query_used=arxiv_query,
            )

        return SearchResult(
            success=True,
            papers=[],
            error=SearchError(
                SearchErrorType.EMPTY_RESULT,
                "所有已启用检索源均未返回结果",
                recoverable=True,
            ),
            query_used=arxiv_query,
        )

    def provider_labels(self) -> str:
        return ", ".join(provider.name for provider in self.providers)

    def _cache_key(
        self,
        arxiv_query: str,
        natural_query: str,
        max_results: int,
        sort_by: str,
    ) -> str:
        payload = {
            "cache_version": 7,
            "providers": [provider.name for provider in self.providers],
            "semantic_scholar_api_enabled": bool(
                self.provider_settings.get("semantic_scholar_api_key")
                or config.SEMANTIC_SCHOLAR_API_KEY
            ),
            "arxiv_query": arxiv_query,
            "natural_query": natural_query,
            "max_results": max_results,
            "sort_by": sort_by,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_path(self, cache_key: str) -> str:
        return os.path.join(config.SEARCH_CACHE_DIR, f"{cache_key}.json")

    def _read_cache(self, cache_key: str) -> Optional[list[dict]]:
        if config.SEARCH_CACHE_TTL_SECONDS <= 0:
            return None
        path = self._cache_path(cache_key)
        if not os.path.exists(path):
            return None
        age = time.time() - os.path.getmtime(path)
        if age > config.SEARCH_CACHE_TTL_SECONDS:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            papers = data.get("papers", [])
            for paper in papers:
                paper["from_cache"] = True
            return papers
        except (OSError, ValueError, TypeError):
            return None

    def _write_cache(self, cache_key: str, papers: list[dict]):
        if config.SEARCH_CACHE_TTL_SECONDS <= 0:
            return
        payload = {
            "created_at": datetime.now().isoformat(),
            "papers": papers,
        }
        try:
            with open(self._cache_path(cache_key), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


def _make_provider(name: str, provider_settings: Optional[dict[str, str]] = None) -> SearchProvider:
    provider_settings = provider_settings or {}
    normalized = name.strip().lower()
    if normalized == "arxiv":
        return ArxivProvider()
    if normalized == "openalex":
        return OpenAlexProvider(mailto=provider_settings.get("openalex_mailto", ""))
    if normalized == "crossref":
        return CrossrefProvider(mailto=provider_settings.get("crossref_mailto", ""))
    if normalized in ("semantic_scholar", "semanticscholar", "s2"):
        return SemanticScholarProvider(
            api_key=provider_settings.get("semantic_scholar_api_key", "")
        )
    raise ValueError(f"未知检索源: {name}")


def _json_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "ArxivAgent/1.0 (contact: info@arxivagent.local)",
    }


def _with_source(paper: dict, source: str) -> dict:
    normalized = paper.copy()
    normalized.setdefault("source", source)
    normalized.setdefault("source_id", normalized.get("arxiv_id", ""))
    normalized.setdefault("doi", "")
    normalized.setdefault("citation_count", 0)
    normalized.setdefault("score", 0.0)
    return normalized


def _provider_query(natural_query: str, arxiv_query: str) -> str:
    natural = natural_query.strip()
    simplified = _simplify_arxiv_query(arxiv_query)
    if natural and simplified:
        query = f"{natural} {simplified}"
    else:
        query = natural or simplified
    query = re.sub(r"\s+", " ", query)
    return query[:300]


def _simplify_arxiv_query(query: str) -> str:
    text = re.sub(r"\b(?:ti|abs|au|cat|all):", " ", query)
    text = re.sub(r"submittedDate:\[[^\]]+\]", " ", text)
    text = re.sub(r"\b(?:AND|OR|ANDNOT|TO)\b", " ", text)
    text = re.sub(r"[()\"\\[\\]:]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _title_query_from_arxiv(query: str) -> str:
    terms = re.findall(r'\bti:"([^"]+)"|\bti:([^\s)]+)', query)
    values = [quoted or bare for quoted, bare in terms]
    values = [v for v in values if v and v.lower() not in {"and", "or"}]
    return " ".join(values)[:120]


def _append_filter(existing: str, extra: str) -> str:
    return f"{existing},{extra}" if existing else extra


def _extract_year_bounds(*texts: str) -> tuple[Optional[int], Optional[int]]:
    joined = " ".join(texts)
    submitted = re.search(
        r"submittedDate:\[(\d{4})\d{8}\s+TO\s+(\d{4})\d{8}\]",
        joined,
        re.IGNORECASE,
    )
    if submitted:
        return int(submitted.group(1)), int(submitted.group(2))

    candidates = [int(y) for y in re.findall(r"\b(20\d{2})\b", joined)]
    if not candidates:
        return None, None
    if "submittedDate:[" in joined:
        return min(candidates), max(candidates)
    if re.search(r"(之后|以来|以后|since|after|from|>=)", joined, re.IGNORECASE):
        return min(candidates), None
    if re.search(r"(之前|以前|before|until|<=)", joined, re.IGNORECASE):
        return None, max(candidates)
    return None, None


def _normalize_openalex_work(item: dict[str, Any]) -> dict:
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in item.get("authorships", [])
        if a.get("author", {}).get("display_name")
    ]
    best_oa = item.get("best_oa_location") or {}
    primary_location = item.get("primary_location") or {}
    ids = item.get("ids") or {}
    concepts = item.get("concepts") or []
    categories = [c.get("display_name", "") for c in concepts[:3] if c.get("display_name")]
    return {
        "title": _clean_text(item.get("display_name", "")),
        "authors": authors,
        "abstract": _abstract_from_inverted_index(item.get("abstract_inverted_index")),
        "categories": categories,
        "published": item.get("publication_date", "") or "",
        "updated": item.get("updated_date", "") or "",
        "link": (
            primary_location.get("landing_page_url")
            or ids.get("doi")
            or item.get("id", "")
        ),
        "pdf_link": best_oa.get("pdf_url") or primary_location.get("pdf_url") or "",
        "arxiv_id": _extract_arxiv_id_from_ids(ids),
        "source": "openalex",
        "source_id": item.get("id", ""),
        "doi": _strip_doi_prefix(ids.get("doi", "")),
        "citation_count": item.get("cited_by_count", 0) or 0,
    }


def _normalize_crossref_work(item: dict[str, Any]) -> dict:
    title = " ".join(item.get("title") or [])
    authors = []
    for author in item.get("author", []) or []:
        name = " ".join(
            part for part in [author.get("given", ""), author.get("family", "")]
            if part
        )
        if name:
            authors.append(name)
    published = _date_parts_to_iso(
        item.get("published-print")
        or item.get("published-online")
        or item.get("published")
        or item.get("created")
    )
    link = item.get("URL") or ""
    doi = item.get("DOI", "")
    if doi and not link:
        link = f"https://doi.org/{doi}"
    return {
        "title": _clean_text(title),
        "authors": authors,
        "abstract": _clean_abstract(item.get("abstract", "")),
        "categories": item.get("subject", [])[:3] if item.get("subject") else [],
        "published": published,
        "updated": _date_parts_to_iso(item.get("indexed")),
        "link": link,
        "pdf_link": _crossref_pdf_link(item),
        "arxiv_id": "",
        "source": "crossref",
        "source_id": doi or item.get("URL", ""),
        "doi": doi,
        "citation_count": item.get("is-referenced-by-count", 0) or 0,
    }


def _normalize_semantic_scholar_paper(item: dict[str, Any]) -> dict:
    external_ids = item.get("externalIds") or {}
    pdf = item.get("openAccessPdf") or {}
    return {
        "title": _clean_text(item.get("title", "")),
        "authors": [
            author.get("name", "")
            for author in item.get("authors", []) or []
            if author.get("name")
        ],
        "abstract": _clean_text(item.get("abstract", "")),
        "categories": item.get("fieldsOfStudy", [])[:3] if item.get("fieldsOfStudy") else [],
        "published": item.get("publicationDate") or str(item.get("year") or ""),
        "updated": "",
        "link": item.get("url", ""),
        "pdf_link": pdf.get("url", "") if isinstance(pdf, dict) else "",
        "arxiv_id": external_ids.get("ArXiv", ""),
        "source": "semantic_scholar",
        "source_id": item.get("paperId", ""),
        "doi": external_ids.get("DOI", ""),
        "citation_count": item.get("citationCount", 0) or 0,
    }


def _abstract_from_inverted_index(index: Optional[dict[str, list[int]]]) -> str:
    if not index:
        return ""
    positioned = []
    for word, positions in index.items():
        positioned.extend((pos, word) for pos in positions)
    positioned.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positioned)


def _clean_abstract(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return _clean_text(text)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _date_parts_to_iso(date_obj: Optional[dict]) -> str:
    if not date_obj:
        return ""
    parts = date_obj.get("date-parts") or []
    if not parts or not parts[0]:
        return date_obj.get("date-time", "")
    year = parts[0][0]
    month = parts[0][1] if len(parts[0]) > 1 else 1
    day = parts[0][2] if len(parts[0]) > 2 else 1
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return str(year)


def _crossref_pdf_link(item: dict[str, Any]) -> str:
    for link in item.get("link", []) or []:
        if link.get("content-type") == "application/pdf" and link.get("URL"):
            return link["URL"]
    return ""


def _extract_arxiv_id_from_ids(ids: dict[str, str]) -> str:
    for key in ("arxiv", "mag"):
        if ids.get(key):
            return ids[key]
    return ""


def _strip_doi_prefix(doi: str) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi or "", flags=re.IGNORECASE)


def _rank_and_dedupe(papers: list[dict], query: str, max_results: int) -> list[dict]:
    seen = set()
    deduped = []
    query_terms = set(_tokens(query))
    for paper in papers:
        identity = _paper_identity(paper)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        paper = paper.copy()
        paper["score"] = _paper_score(paper, query_terms)
        deduped.append(paper)
    deduped.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return deduped[:max_results]


def _paper_identity(paper: dict) -> str:
    doi = _strip_doi_prefix(paper.get("doi", "")).lower()
    if doi:
        return f"doi:{doi}"
    arxiv_id = paper.get("arxiv_id", "").lower()
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    title = re.sub(r"[^a-z0-9]+", " ", paper.get("title", "").lower()).strip()
    return f"title:{title}" if title else ""


def _paper_score(paper: dict, query_terms: set[str]) -> float:
    title_terms = set(_tokens(paper.get("title", "")))
    abstract_terms = set(_tokens(paper.get("abstract", "")))
    title_overlap = len(query_terms & title_terms)
    abstract_overlap = len(query_terms & abstract_terms)
    citation_boost = math.log1p(float(paper.get("citation_count", 0) or 0)) / 5
    source_boost = {"arxiv": 0.25, "openalex": 0.15, "crossref": 0.05}.get(paper.get("source"), 0)
    pdf_boost = 0.1 if paper.get("pdf_link") else 0
    return (title_overlap * 2.0) + (abstract_overlap * 0.3) + citation_boost + source_boost + pdf_boost


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[a-zA-Z0-9]+", text or "")
        if len(token) > 2
    ]


def _merge_errors(errors: list[SearchError]) -> SearchError:
    priority = [
        SearchErrorType.RATE_LIMIT,
        SearchErrorType.NETWORK,
        SearchErrorType.TIMEOUT,
        SearchErrorType.HTTP_ERROR,
        SearchErrorType.QUERY_SYNTAX,
        SearchErrorType.PARSE_ERROR,
    ]
    chosen = next(
        (err for err_type in priority for err in errors if err.error_type == err_type),
        errors[0],
    )
    details = "；".join(str(err) for err in errors)
    return SearchError(
        chosen.error_type,
        f"所有检索源均失败: {details}",
        http_status=chosen.http_status,
        raw_response=chosen.raw_response,
        recoverable=any(err.recoverable for err in errors),
    )
