"""正文分块内容身份（T4）。

agent 进程级检索器缓存键（core.agent._retriever_cache_key）与 Qdrant
集合身份（core.rag._collection_name / _chunk_id）共用这里的指纹算法：

- 哈希完整正文：旧实现截断到 500 字，之后的内容变化会复用旧缓存与
  旧集合，证据可能整体过时；
- 结构化、确定序列化（json.dumps + sort_keys），替代无转义的 "|" 拼接，
  字段值里出现分隔符不再产生指纹碰撞；
- 身份算法带版本号：算法演进时旧指纹整体失效，避免新旧混用；
- 证据元数据（页码/章节/来源 URL）参与指纹：仅元数据变化时，返回给
  前端的 evidence 必须使用新元数据。

不包含任何密钥类字段；相同输入保证稳定复用。
"""
from __future__ import annotations

import hashlib
import json

# 指纹算法版本：+1 使所有旧指纹（含旧集合身份）自然失效重建。
IDENTITY_VERSION = 2

# 参与内容身份的字段。text 永远完整参与（不截断）。
_IDENTITY_FIELDS = (
    "arxiv_id",
    "doi",
    "paper_title",
    "chunk_index",
    "text",
    "page_number",
    "page_end",
    "section_title",
    "source_url",
    "pdf_url",
)


def chunk_content_fingerprint(chunk: dict) -> str:
    """单个分块的稳定内容指纹：版本号 + 结构化字段的确定性 JSON 哈希。"""
    payload = {field: chunk.get(field) for field in _IDENTITY_FIELDS}
    serialized = json.dumps(
        {"v": IDENTITY_VERSION, "chunk": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def chunks_identity(chunks: list[dict]) -> str:
    """一组分块的聚合指纹（顺序敏感：索引构建与分块顺序相关）。"""
    digest = hashlib.sha256()
    digest.update(f"v{IDENTITY_VERSION}\n".encode("utf-8"))
    for chunk in chunks:
        digest.update(chunk_content_fingerprint(chunk).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:16]
