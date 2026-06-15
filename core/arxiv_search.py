"""
arXiv API 检索封装模块
支持重试、错误分类和结构化错误返回
"""
import time
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Optional
from dataclasses import dataclass
from enum import Enum

import config

# arXiv Atom XML 命名空间
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"

# 上次请求的时间戳，用于速率限制
_last_request_time = 0.0


class SearchErrorType(Enum):
    """检索错误类型"""
    NETWORK = "network"           # 网络连接错误
    TIMEOUT = "timeout"           # 超时
    HTTP_ERROR = "http_error"     # HTTP 错误码
    RATE_LIMIT = "rate_limit"     # API 限流
    PARSE_ERROR = "parse_error"   # XML 解析错误
    EMPTY_RESULT = "empty_result" # 结果为空
    QUERY_SYNTAX = "query_syntax" # 检索式语法错误


@dataclass
class SearchError:
    """检索错误的结构化描述"""
    error_type: SearchErrorType
    message: str
    http_status: int = 0
    raw_response: str = ""
    recoverable: bool = True  # 是否可以通过修改检索式恢复

    def __str__(self):
        return f"[{self.error_type.value}] {self.message}"


@dataclass
class SearchResult:
    """检索结果封装"""
    success: bool
    papers: list[dict]
    error: Optional[SearchError] = None
    query_used: str = ""


def search(
    query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
    sort_order: str = "descending",
    start: int = 0,
    max_retries: int = 2,
    retry_delay: float = 5.0,
) -> SearchResult:
    """
    执行 arXiv API 检索，带自动重试。

    Args:
        query: arXiv 检索式
        max_results: 最大返回数量
        sort_by: 排序方式
        sort_order: 排序顺序
        start: 起始位置
        max_retries: 最大重试次数（针对网络/超时错误）
        retry_delay: 重试间隔秒数

    Returns:
        SearchResult 对象，包含结果或错误信息
    """
    global _last_request_time

    last_error = None

    for attempt in range(max_retries + 1):
        try:
            # 遵守速率限制
            elapsed = time.time() - _last_request_time
            if elapsed < config.ARXIV_RATE_LIMIT_SECONDS:
                time.sleep(config.ARXIV_RATE_LIMIT_SECONDS - elapsed)

            params = {
                "search_query": query,
                "start": start,
                "max_results": max_results,
                "sortBy": sort_by,
                "sortOrder": sort_order,
            }

            headers = {
                "User-Agent": "ArxivAgent/1.0 (contact: info@arxivagent.org)"
            }
            response = requests.get(
                config.ARXIV_API_URL,
                params=params,
                headers=headers,
                timeout=getattr(config, "SEARCH_PROVIDER_TIMEOUT_SECONDS", 30),
            )
            _last_request_time = time.time()

            # arXiv 限流有时返回 429，有时返回 200 + 纯文本 "Rate exceeded."
            if _is_rate_limited(response):
                wait_seconds = _retry_after_seconds(response, retry_delay * (attempt + 1))
                error = SearchError(
                    error_type=SearchErrorType.RATE_LIMIT,
                    message=f"arXiv API 触发限流，建议等待 {wait_seconds:.0f} 秒后重试",
                    http_status=response.status_code,
                    raw_response=response.text[:500],
                    recoverable=False,
                )
                if attempt < max_retries:
                    last_error = error
                    time.sleep(max(wait_seconds, config.ARXIV_RATE_LIMIT_SECONDS))
                    continue
                return SearchResult(success=False, papers=[], error=error, query_used=query)

            # HTTP 错误
            if response.status_code != 200:
                error = SearchError(
                    error_type=SearchErrorType.HTTP_ERROR,
                    message=f"arXiv API 返回 HTTP {response.status_code}",
                    http_status=response.status_code,
                    raw_response=response.text[:500],
                    recoverable=response.status_code >= 500,  # 5xx 可重试
                )
                if response.status_code >= 500 and attempt < max_retries:
                    last_error = error
                    time.sleep(retry_delay * (attempt + 1))
                    continue
                # 4xx 错误可能是检索式语法问题
                if 400 <= response.status_code < 500:
                    error.error_type = SearchErrorType.QUERY_SYNTAX
                    error.message = f"arXiv API 返回 HTTP {response.status_code}，可能是检索式语法错误"
                    error.recoverable = True
                return SearchResult(success=False, papers=[], error=error, query_used=query)

            # 检查响应中是否有错误信息
            response_text = response.text
            if _check_api_error(response_text):
                error_msg = _extract_api_error(response_text)
                return SearchResult(
                    success=False, papers=[],
                    error=SearchError(
                        error_type=SearchErrorType.QUERY_SYNTAX,
                        message=f"arXiv API 报告错误: {error_msg}",
                        raw_response=response_text[:500],
                        recoverable=True,
                    ),
                    query_used=query,
                )

            # 解析结果
            try:
                papers = _parse_atom_response(response_text)
            except ET.ParseError as e:
                return SearchResult(
                    success=False, papers=[],
                    error=SearchError(
                        error_type=SearchErrorType.PARSE_ERROR,
                        message=f"XML 解析失败: {e}",
                        raw_response=response_text[:500],
                        recoverable=False,
                    ),
                    query_used=query,
                )

            # 检查结果是否为空
            if not papers:
                return SearchResult(
                    success=True, papers=[],
                    error=SearchError(
                        error_type=SearchErrorType.EMPTY_RESULT,
                        message="检索结果为空，检索式可能过于具体或存在语法问题",
                        recoverable=True,
                    ),
                    query_used=query,
                )

            return SearchResult(success=True, papers=papers, query_used=query)

        except requests.exceptions.Timeout:
            last_error = SearchError(
                error_type=SearchErrorType.TIMEOUT,
                message=f"arXiv API 请求超时（第 {attempt + 1} 次尝试）",
                recoverable=attempt < max_retries,
            )
            if attempt < max_retries:
                time.sleep(retry_delay * (attempt + 1))
                continue

        except requests.exceptions.ConnectionError as e:
            if last_error and last_error.error_type == SearchErrorType.RATE_LIMIT:
                last_error.message = (
                    f"{last_error.message}；后续连接被服务器关闭，仍按限流处理"
                )
                last_error.recoverable = False
            else:
                last_error = SearchError(
                    error_type=SearchErrorType.NETWORK,
                    message=f"网络连接错误: {e}",
                    recoverable=attempt < max_retries,
                )
            if attempt < max_retries:
                time.sleep(retry_delay * (attempt + 1))
                continue

        except Exception as e:
            return SearchResult(
                success=False, papers=[],
                error=SearchError(
                    error_type=SearchErrorType.NETWORK,
                    message=f"未预期的错误: {e}",
                    recoverable=False,
                ),
                query_used=query,
            )

    # 所有重试都失败了
    return SearchResult(
        success=False, papers=[],
        error=last_error or SearchError(
            error_type=SearchErrorType.NETWORK,
            message="所有重试均已失败",
            recoverable=False,
        ),
        query_used=query,
    )


def _check_api_error(xml_text: str) -> bool:
    """检查 arXiv API 响应中是否包含错误"""
    # arXiv 在某些错误情况下会在 feed 中返回错误条目
    try:
        root = ET.fromstring(xml_text)
        # 查找错误条目（没有 entry 但有 opensearch totalResults = 0 不算错误）
        entries = root.findall(f"{{{ATOM_NS}}}entry")
        if len(entries) == 1:
            entry = entries[0]
            title = entry.find(f"{{{ATOM_NS}}}title")
            if title is not None and title.text and "Error" in title.text:
                return True
        return False
    except ET.ParseError:
        return True


def _is_rate_limited(response: requests.Response) -> bool:
    """判断 arXiv 是否返回了限流响应。"""
    if response.status_code == 429:
        return True
    content_type = response.headers.get("Content-Type", "")
    body_preview = response.text[:100].strip().lower()
    return "xml" not in content_type.lower() and "rate exceeded" in body_preview


def _retry_after_seconds(response: requests.Response, fallback: float) -> float:
    """解析 Retry-After；没有可靠值时使用递增退避。"""
    header = response.headers.get("Retry-After", "").strip()
    if not header:
        return fallback
    try:
        return max(float(header), fallback)
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(header)
        return max(retry_at.timestamp() - time.time(), fallback)
    except (TypeError, ValueError, OSError):
        return fallback


def _extract_api_error(xml_text: str) -> str:
    """从 arXiv API 错误响应中提取错误信息"""
    try:
        root = ET.fromstring(xml_text)
        entries = root.findall(f"{{{ATOM_NS}}}entry")
        if entries:
            summary = entries[0].find(f"{{{ATOM_NS}}}summary")
            if summary is not None and summary.text:
                return summary.text.strip()
        return "未知 API 错误"
    except ET.ParseError:
        return f"无法解析错误响应: {xml_text[:200]}"


def _parse_atom_response(xml_text: str) -> list[dict]:
    """解析 arXiv Atom XML 响应"""
    root = ET.fromstring(xml_text)
    papers = []

    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        # 跳过错误条目
        title_el = entry.find(f"{{{ATOM_NS}}}title")
        if title_el is not None and title_el.text and "Error" in title_el.text:
            continue
        paper = _parse_entry(entry)
        if paper:
            papers.append(paper)

    return papers


def _parse_entry(entry) -> Optional[dict]:
    """解析单个论文条目"""
    try:
        # 标题
        title_el = entry.find(f"{{{ATOM_NS}}}title")
        title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""

        # 摘要
        summary_el = entry.find(f"{{{ATOM_NS}}}summary")
        abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None and summary_el.text else ""

        # 作者
        authors = []
        for author_el in entry.findall(f"{{{ATOM_NS}}}author"):
            name_el = author_el.find(f"{{{ATOM_NS}}}name")
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        # 类别
        categories = []
        for cat_el in entry.findall(f"{{{ARXIV_NS}}}primary_category"):
            term = cat_el.get("term")
            if term:
                categories.append(term)
        for cat_el in entry.findall(f"{{{ATOM_NS}}}category"):
            term = cat_el.get("term")
            if term and term not in categories:
                categories.append(term)

        # 发表日期
        published_el = entry.find(f"{{{ATOM_NS}}}published")
        published = published_el.text.strip() if published_el is not None and published_el.text else ""

        # 更新日期
        updated_el = entry.find(f"{{{ATOM_NS}}}updated")
        updated = updated_el.text.strip() if updated_el is not None and updated_el.text else ""

        # 链接
        link = ""
        pdf_link = ""
        for link_el in entry.findall(f"{{{ATOM_NS}}}link"):
            href = link_el.get("href", "")
            link_type = link_el.get("type", "")
            link_title = link_el.get("title", "")
            if link_title == "pdf":
                pdf_link = href
            elif link_type == "text/html" or (not link and "abs" in href):
                link = href

        # arXiv ID
        id_el = entry.find(f"{{{ATOM_NS}}}id")
        arxiv_id = id_el.text.strip() if id_el is not None and id_el.text else ""

        if not title:
            return None

        return {
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "categories": categories,
            "published": published,
            "updated": updated,
            "link": link or arxiv_id,
            "pdf_link": pdf_link,
            "arxiv_id": arxiv_id,
        }
    except Exception:  # noqa: BLE001 - 单条 entry 解析失败不应中断整批解析
        return None


def format_papers_for_llm(papers: list[dict]) -> str:
    """将论文列表格式化为供 LLM 阅读的文本"""
    if not papers:
        return "（无检索结果）"

    lines = []
    for i, paper in enumerate(papers):
        lines.append(f"--- 论文 {i + 1} ---")
        lines.append(f"索引: {i}")
        authors = paper.get("authors", [])
        categories = paper.get("categories", [])
        lines.append(f"标题: {paper.get('title', '无标题')}")
        lines.append(f"来源: {paper.get('source', 'arxiv')}")
        if paper.get("doi"):
            lines.append(f"DOI: {paper['doi']}")
        if paper.get("citation_count"):
            lines.append(f"引用数: {paper['citation_count']}")
        lines.append(f"作者: {', '.join(authors[:5])}" +
                     (f" 等共{len(authors)}人" if len(authors) > 5 else ""))
        lines.append(f"类别: {', '.join(categories[:3])}")
        lines.append(f"发表日期: {paper.get('published', '')[:10]}")
        lines.append(f"摘要: {paper.get('abstract', '')[:300]}...")
        lines.append(f"链接: {paper.get('link', '')}")
        lines.append("")

    return "\n".join(lines)
