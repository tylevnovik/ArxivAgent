"""
PDF 下载与解析模块
"""
import os
import re
import hashlib
import requests
import pypdf
import config

# 使用标准的 User-Agent 避免 arXiv 屏蔽
HEADERS = {
    "User-Agent": "ArxivAgent/1.0 (contact: info@arxivagent.org)"
}


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
    """
    filename = f"{arxiv_id}.pdf"
    filepath = os.path.join(config.PDF_CACHE_DIR, filename)
    
    if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
        return filepath
        
    try:
        response = requests.get(pdf_link, headers=HEADERS, timeout=30)
        response.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(response.content)
        return filepath
    except Exception as e:
        raise RuntimeError(f"下载 PDF 失败 ({pdf_link}): {e}")


def extract_text_from_pdf(pdf_path: str) -> str:
    """使用 pypdf 从本地 PDF 文件提取完整文本"""
    try:
        reader = pypdf.PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
        return "\n\n".join(text_parts)
    except Exception as e:
        raise RuntimeError(f"解析 PDF 失败 ({pdf_path}): {e}")


def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[str]:
    """将文本切分为固定大小的分块，并带有一定重叠"""
    if not text:
        return []
    
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - chunk_overlap
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
        full_text = extract_text_from_pdf(pdf_path)
        text_chunks = chunk_text(full_text)
        
        chunks = []
        for i, text in enumerate(text_chunks):
            chunks.append({
                "paper_title": title,
                "arxiv_id": arxiv_id,
                "chunk_index": i,
                "text": text.strip()
            })
        return chunks
    except Exception as e:
        print(f"⚠️ 处理论文 PDF 失败: {title} ({pdf_link}) - 错误: {e}")
        return []
