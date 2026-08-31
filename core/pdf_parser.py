"""
PDF 下载与解析模块
"""
import os
import re
import hashlib
import tempfile
import threading
import requests
import pypdf
import config

# 使用标准的 User-Agent 避免 arXiv 屏蔽
HEADERS = {
    "User-Agent": "ArxivAgent/1.0 (contact: info@arxivagent.org)"
}

# 单篇 PDF 下载大小上限（字节）。超过则中止下载，避免恶意/错误链接撑爆磁盘。
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB

_DOWNLOAD_LOCK_GUARD = threading.Lock()
_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}


def _download_lock(arxiv_id: str) -> threading.Lock:
    with _DOWNLOAD_LOCK_GUARD:
        return _DOWNLOAD_LOCKS.setdefault(arxiv_id, threading.Lock())


def get_arxiv_id(pdf_link: str) -> str:
    """从 pdf_link 中提取 arXiv ID，提取失败则返回 md5 hash 字符串"""
    if not pdf_link:
        return "unknown"
    # 匹配形如 /abs/2401.12345v1 或 /pdf/2401.12345v1.pdf 的 ID
    match = re.search(r'/(?:abs|pdf)/([a-zA-Z0-9.-]+)', pdf_link)
    if match:
        arxiv_id = match.group(1)
        # 去掉可能存在的 .pdf 后缀
        if arxiv_id.endswith(".pdf"):
            arxiv_id = arxiv_id[:-4]
        return arxiv_id
    
    # 兜底方案使用 md5
    return hashlib.md5(pdf_link.encode("utf-8")).hexdigest()


def download_pdf(pdf_link: str, arxiv_id: str) -> str:
    """
    下载 PDF 文件并保存到本地缓存目录。
    如果已存在，则直接返回本地路径。
    返回保存的本地文件绝对路径。

    流式下载并限制单文件大小（MAX_PDF_BYTES），避免恶意/超大链接撑爆磁盘。
    """
    filename = f"{arxiv_id}.pdf"
    filepath = os.path.join(config.PDF_CACHE_DIR, filename)

    with _download_lock(arxiv_id):
        if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
            return filepath

        tmp = ""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=f".{arxiv_id}.", suffix=".pdf.tmp", dir=config.PDF_CACHE_DIR
            )
            # stream=True 逐块下载，避免一次性把整个 PDF 读进内存。
            with os.fdopen(fd, "wb") as f:
                with requests.get(pdf_link, headers=HEADERS, timeout=30, stream=True) as response:
                    response.raise_for_status()
                    written = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > MAX_PDF_BYTES:
                            raise RuntimeError(
                                f"PDF 超过大小上限 {MAX_PDF_BYTES // (1024 * 1024)}MB "
                                f"({pdf_link})"
                            )
                        f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, filepath)
            tmp = ""
            return filepath
        except Exception as e:
            raise RuntimeError(f"下载 PDF 失败 ({pdf_link}): {e}")
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def extract_text_from_pdf(pdf_path: str) -> str:
    """使用 pypdf 从本地 PDF 文件提取完整文本"""
    return "\n\n".join(text for _, text in _extract_pages_from_pdf(pdf_path) if text)


def _extract_pages_from_pdf(pdf_path: str) -> list[tuple[int, str]]:
    """按页提取文本，页码从 1 开始，供证据回溯使用。"""
    try:
        reader = pypdf.PdfReader(pdf_path)
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            t = page.extract_text()
            if t:
                pages.append((page_number, t))
        return pages
    except Exception as e:
        raise RuntimeError(f"解析 PDF 失败 ({pdf_path}): {e}")


def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[str]:
    """按段落边界贪心切分：尽量不切断段落，并用块尾段落带入下一块实现重叠。"""
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        # 单段落超过预算：先落盘现有块，再对超长段落硬切
        if len(para) > chunk_size:
            if current:
                chunks.append("\n\n".join(current))
                current, current_len = [], 0
            step = max(1, chunk_size - chunk_overlap)
            for start in range(0, len(para), step):
                chunks.append(para[start:start + chunk_size])
            continue

        if current and current_len + len(para) + 2 > chunk_size:
            chunks.append("\n\n".join(current))
            # 重叠：把上一块尾部段落带入下一块（总长不超过 overlap 预算）
            overlap_paras: list[str] = []
            overlap_len = 0
            for prev in reversed(current):
                if overlap_len + len(prev) > chunk_overlap:
                    break
                overlap_paras.insert(0, prev)
                overlap_len += len(prev)
            current, current_len = overlap_paras, overlap_len

        current.append(para)
        current_len += len(para) + 2

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def process_paper_pdf(title: str, pdf_link: str) -> list[dict]:
    """
    处理单篇论文的完整工作流：下载 -> 提取文本 -> 切片
    返回分块字典列表
    """
    if not pdf_link:
        return []
        
    arxiv_id = get_arxiv_id(pdf_link)
    try:
        pdf_path = download_pdf(pdf_link, arxiv_id)
        pages = _extract_pages_from_pdf(pdf_path)
        chunks = []
        source_url = _source_url_from_pdf(pdf_link, arxiv_id)
        for page_number, page_text in pages:
            for text in chunk_text(page_text):
                chunks.append({
                    "paper_title": title,
                    "arxiv_id": arxiv_id,
                    "chunk_index": len(chunks),
                    "page_number": page_number,
                    "page_end": page_number,
                    "section_title": _detect_section_title(text),
                    "source_url": source_url,
                    "pdf_url": pdf_link,
                    "text": text.strip(),
                })
        return chunks
    except Exception as e:
        print(f"[WARN] Failed to process paper PDF: {title} ({pdf_link}) - {e}")
        return []


def _detect_section_title(text: str) -> str:
    """从分块开头保守识别章节标题；识别不到时返回空字符串。"""
    for raw_line in str(text or "").splitlines()[:8]:
        line = re.sub(r"\s+", " ", raw_line).strip(" -\t")
        if not 2 <= len(line) <= 120 or line.endswith((".", "。", ":", "：")):
            continue
        if re.match(r"^(?:\d+(?:\.\d+)*[.)]?|[IVX]+)\s+\S+", line, re.IGNORECASE):
            return line
        if line.isupper() and len(line.split()) <= 12:
            return line
        if re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9 ()（）\-]{2,80}", line):
            return line
    return ""


def _source_url_from_pdf(pdf_link: str, arxiv_id: str) -> str:
    match = re.search(r"(https?://[^/]+)/(?:pdf|abs)/", pdf_link or "", re.IGNORECASE)
    if match and arxiv_id and not arxiv_id == "unknown":
        return f"{match.group(1)}/abs/{arxiv_id}"
    return pdf_link or ""
