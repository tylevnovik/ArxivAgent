"""
轻量级可插拔 RAG 检索器模块
定义了检索接口类，便于未来平滑切换到向量数据库/Embedding 检索。
"""
import atexit
import hashlib
import math
import os
import re
import warnings
from collections import Counter
from abc import ABC, abstractmethod

import config


class BaseRetriever(ABC):
    """
    检索器抽象基类 (Base class for all retrievers)
    未来可以通过继承此类，轻松引入向量化模型 (如 OpenAI Embeddings) 与向量库 (如 Chroma/Qdrant)
    """
    
    @abstractmethod
    def build_index(self, chunks: list[dict]):
        """根据输入的分块列表构建索引"""
        pass
        
    @abstractmethod
    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """
        检索最相关的分块，返回包含得分的分块字典列表
        每个分块字典应包含:
        {
            "paper_title": str,
            "arxiv_id": str,
            "chunk_index": int,
            "text": str,
            "score": float
        }
        """
        pass


def tokenize(text: str) -> list[str]:
    """
    中英文混合简单分词器
    英文按单词切分，中文按单字切分，转换为小写
    """
    if not text:
        return []
    text = text.lower()
    # 匹配英文单词或单个中文字符
    words = re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]', text)
    return [w for w in words if w.strip()]


class TFIDFRetriever(BaseRetriever):
    """
    纯 Python 实现的轻量级 TF-IDF 余弦相似度检索器。
    无需安装外部数据库或二进制依赖，开箱即用。
    """
    
    def __init__(self):
        self.chunks = []
        self.doc_tokens = []
        self.vocab = set()
        self.idf = {}
        self.doc_vectors = []

    def build_index(self, chunks: list[dict]):
        """构建 TF-IDF 索引"""
        self.chunks = chunks
        self.doc_tokens = [tokenize(c["text"]) for c in chunks]
        
        num_docs = len(chunks)
        if num_docs == 0:
            return
            
        # 统计包含每个词的文档数
        doc_counts = Counter()
        for tokens in self.doc_tokens:
            unique_tokens = set(tokens)
            for token in unique_tokens:
                doc_counts[token] += 1
                
        # 计算 IDF (带平滑)
        self.idf = {}
        for token, count in doc_counts.items():
            self.idf[token] = math.log(1.0 + (num_docs / (count + 0.5)))
            
        # 构建文档 TF-IDF 权重向量
        self.doc_vectors = []
        for tokens in self.doc_tokens:
            tf = Counter(tokens)
            vector = {}
            for token, count in tf.items():
                vector[token] = count * self.idf.get(token, 0.0)
            self.doc_vectors.append(vector)

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """基于余弦相似度检索相关分块"""
        if not self.chunks or not self.doc_vectors:
            return []
            
        query_tokens = tokenize(query)
        if not query_tokens:
            # 兜底返回前 top_k 个分块
            return self.chunks[:top_k]
            
        # 构建查询向量
        query_tf = Counter(query_tokens)
        query_vector = {}
        for token, count in query_tf.items():
            query_vector[token] = count * self.idf.get(token, 0.0)
            
        query_norm = math.sqrt(sum(v ** 2 for v in query_vector.values()))
        
        scores = []
        for idx, doc_vector in enumerate(self.doc_vectors):
            # 计算点积
            dot_product = sum(query_vector.get(token, 0.0) * val for token, val in doc_vector.items())
            doc_norm = math.sqrt(sum(v ** 2 for v in doc_vector.values()))
            
            # 计算余弦相似度
            if query_norm > 0 and doc_norm > 0:
                similarity = dot_product / (query_norm * doc_norm)
            else:
                similarity = 0.0
                
            scores.append((idx, similarity))
            
        # 降序排列
        scores.sort(key=lambda x: x[1], reverse=True)
        
        # 封装结果
        results = []
        for idx, score in scores[:top_k]:
            # 过滤掉完全不相关的分块 (score == 0)
            if score <= 0.0:
                continue
            chunk = self.chunks[idx].copy()
            chunk["score"] = score
            results.append(chunk)
            
        return results


class FastEmbedEmbeddingProvider:
    """本地 FastEmbed 向量化提供器，不依赖外部 API Key。"""

    def __init__(self, model_name: str | None = None):
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise RuntimeError("缺少 fastembed，请安装 qdrant-client[fastembed]") from e

        self.model_name = model_name or config.RAG_EMBEDDING_MODEL
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"The model .* now uses mean pooling.*",
                category=UserWarning,
            )
            self.model = TextEmbedding(model_name=self.model_name)
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            probe = self.embed_query("dimension probe")
            self._dimension = len(probe)
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [_to_float_list(v) for v in self.model.embed(texts)]

    def embed_query(self, query: str) -> list[float]:
        return _to_float_list(next(self.model.query_embed(query or "")))


class BM25SRetriever:
    """BM25S 关键词检索器，和向量检索并行用于混合召回。"""

    def __init__(self):
        self.chunks: list[dict] = []
        self.retriever = None

    def build_index(self, chunks: list[dict]):
        try:
            import bm25s
        except ImportError as e:
            raise RuntimeError("缺少 bm25s，请安装 bm25s") from e

        self.chunks = chunks
        corpus = [chunk.get("text", "") for chunk in chunks]
        corpus_tokens = bm25s.tokenize(corpus, show_progress=False)
        self.retriever = bm25s.BM25(corpus=chunks)
        self.retriever.index(corpus_tokens, show_progress=False)

    def retrieve(self, query: str, top_k: int = 20) -> list[dict]:
        if not self.retriever or not self.chunks:
            return []

        import bm25s

        query_tokens = bm25s.tokenize(query or "", show_progress=False)
        k = min(top_k, len(self.chunks))
        if k <= 0:
            return []

        results, scores = self.retriever.retrieve(query_tokens, k=k, show_progress=False)
        rows = []
        for rank in range(results.shape[1]):
            chunk = dict(results[0, rank])
            score = float(scores[0, rank])
            if score <= 0:
                continue
            chunk["score"] = score
            chunk["bm25_score"] = score
            chunk["bm25_rank"] = rank + 1
            rows.append(chunk)
        return rows


class QdrantDenseRetriever:
    """Qdrant dense vector retriever，支持本地 path / :memory: / 远程 URL。"""

    def __init__(
        self,
        embedding_provider: FastEmbedEmbeddingProvider,
        collection_prefix: str | None = None,
        location: str | None = None,
    ):
        try:
            from qdrant_client import models  # noqa: F401
        except ImportError as e:
            raise RuntimeError("缺少 qdrant-client，请安装 qdrant-client[fastembed]") from e

        self.embedding_provider = embedding_provider
        self.collection_prefix = collection_prefix or config.RAG_QDRANT_COLLECTION_PREFIX
        self.location = location or config.RAG_QDRANT_LOCATION
        self.collection_name = ""
        self.client, self.actual_location = _get_qdrant_client(self.location)
        self.chunks: list[dict] = []

    def build_index(self, chunks: list[dict]):
        from qdrant_client import models

        self.chunks = chunks
        self.collection_name = _collection_name(self.collection_prefix, chunks)
        vectors = self.embedding_provider.embed_documents([c.get("text", "") for c in chunks])
        vector_size = self.embedding_provider.dimension

        if _collection_exists(self.client, self.collection_name):
            self.client.delete_collection(self.collection_name)

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=idx,
                    vector=vector,
                    payload=chunk,
                )
                for idx, (chunk, vector) in enumerate(zip(chunks, vectors))
            ],
        )

    def retrieve(self, query: str, top_k: int = 20) -> list[dict]:
        if not self.collection_name:
            return []

        query_vector = self.embedding_provider.embed_query(query or "")
        limit = max(1, top_k)
        points = _query_qdrant_points(
            self.client,
            self.collection_name,
            query_vector,
            limit,
        )

        rows = []
        for rank, point in enumerate(points, start=1):
            payload = dict(point.payload or {})
            score = float(point.score)
            payload["score"] = score
            payload["dense_score"] = score
            payload["dense_rank"] = rank
            rows.append(payload)
        return rows


class FastEmbedReranker:
    """可选 cross-encoder reranker。默认关闭，因为模型下载和推理成本更高。"""

    def __init__(self, model_name: str | None = None):
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError as e:
            raise RuntimeError("缺少 FastEmbed reranker 支持") from e

        self.model_name = model_name or config.RAG_RERANKER_MODEL
        self.model = TextCrossEncoder(model_name=self.model_name)

    def rerank(self, query: str, chunks: list[dict]) -> list[dict]:
        if not chunks:
            return []
        scores = list(self.model.rerank(query or "", [c.get("text", "") for c in chunks]))
        reranked = []
        for chunk, score in zip(chunks, scores):
            updated = chunk.copy()
            updated["rerank_score"] = float(score)
            reranked.append(updated)
        reranked.sort(key=lambda item: item.get("rerank_score", 0.0), reverse=True)
        return reranked


class HybridRetriever(BaseRetriever):
    """
    Qdrant dense vector + BM25S sparse keyword + RRF 融合检索器。
    默认全部本地运行，跨平台；如果依赖缺失，由调用方降级到 TF-IDF。
    """

    def __init__(
        self,
        dense_candidates: int | None = None,
        bm25_candidates: int | None = None,
        rrf_k: int | None = None,
        dense_weight: float | None = None,
        bm25_weight: float | None = None,
        enable_reranker: bool | None = None,
    ):
        self.dense_candidates = dense_candidates or config.RAG_DENSE_CANDIDATES
        self.bm25_candidates = bm25_candidates or config.RAG_BM25_CANDIDATES
        self.rrf_k = rrf_k or config.RAG_RRF_K
        self.dense_weight = dense_weight if dense_weight is not None else config.RAG_DENSE_WEIGHT
        self.bm25_weight = bm25_weight if bm25_weight is not None else config.RAG_BM25_WEIGHT
        self.enable_reranker = config.RAG_ENABLE_RERANKER if enable_reranker is None else enable_reranker
        self.chunks: list[dict] = []
        self.embedding_provider: FastEmbedEmbeddingProvider | None = None
        self.dense_retriever: QdrantDenseRetriever | None = None
        self.bm25_retriever: BM25SRetriever | None = None
        self.reranker: FastEmbedReranker | None = None
        self.index_summary = ""

    def build_index(self, chunks: list[dict]):
        self.chunks = [_normalize_chunk(chunk, idx) for idx, chunk in enumerate(chunks)]
        if not self.chunks:
            return

        self.embedding_provider = FastEmbedEmbeddingProvider()
        self.dense_retriever = QdrantDenseRetriever(self.embedding_provider)
        self.dense_retriever.build_index(self.chunks)

        self.bm25_retriever = BM25SRetriever()
        self.bm25_retriever.build_index(self.chunks)

        if self.enable_reranker:
            self.reranker = FastEmbedReranker()

        self.index_summary = (
            f"HybridRetriever(Qdrant + BM25S + RRF, chunks={len(self.chunks)}, "
            f"embedding={self.embedding_provider.model_name}, "
            f"qdrant={self.dense_retriever.actual_location if self.dense_retriever else config.RAG_QDRANT_LOCATION}, "
            f"reranker={bool(self.reranker)})"
        )

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        if not self.chunks:
            return []

        dense_results = (
            self.dense_retriever.retrieve(query, self.dense_candidates)
            if self.dense_retriever else []
        )
        bm25_results = (
            self.bm25_retriever.retrieve(query, self.bm25_candidates)
            if self.bm25_retriever else []
        )
        fused = self._rrf_fuse(dense_results, bm25_results)

        if self.reranker:
            fused = self.reranker.rerank(query, fused[: max(top_k * 3, top_k)])

        return fused[:top_k]

    def _rrf_fuse(self, dense_results: list[dict], bm25_results: list[dict]) -> list[dict]:
        records: dict[str, dict] = {}
        scores: dict[str, float] = Counter()

        def add(results: list[dict], source: str, weight: float):
            for rank, result in enumerate(results, start=1):
                chunk_id = result.get("chunk_id") or _chunk_id(result, rank)
                if chunk_id not in records:
                    records[chunk_id] = result.copy()
                    records[chunk_id]["retrieval_sources"] = []
                records[chunk_id].update({k: v for k, v in result.items() if k.endswith("_score") or k.endswith("_rank")})
                if source not in records[chunk_id]["retrieval_sources"]:
                    records[chunk_id]["retrieval_sources"].append(source)
                scores[chunk_id] += weight / (self.rrf_k + rank)

        add(dense_results, "dense", self.dense_weight)
        add(bm25_results, "bm25", self.bm25_weight)

        fused = []
        for chunk_id, score in scores.items():
            item = records[chunk_id].copy()
            item["score"] = float(score)
            item["hybrid_score"] = float(score)
            fused.append(item)

        fused.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return fused


_QDRANT_CLIENTS = {}


def _close_qdrant_clients():
    for client in list(_QDRANT_CLIENTS.values()):
        try:
            client.close()
        except Exception:
            pass
    _QDRANT_CLIENTS.clear()


atexit.register(_close_qdrant_clients)


def _get_qdrant_client(location: str):
    from qdrant_client import QdrantClient

    normalized = (location or ":memory:").strip()
    cache_key = normalized
    if cache_key in _QDRANT_CLIENTS:
        return _QDRANT_CLIENTS[cache_key], normalized

    if normalized in {":memory:", "memory", "in-memory"}:
        client = QdrantClient(":memory:")
        actual_location = ":memory:"
    elif normalized.startswith(("http://", "https://")):
        client = QdrantClient(url=normalized)
        actual_location = normalized
    else:
        os.makedirs(normalized, exist_ok=True)
        try:
            client = QdrantClient(path=normalized)
            actual_location = normalized
        except RuntimeError as e:
            if "already accessed by another instance" not in str(e):
                raise
            client = QdrantClient(":memory:")
            actual_location = f":memory: (fallback; locked path: {normalized})"
            cache_key = actual_location

    _QDRANT_CLIENTS[cache_key] = client
    return client, actual_location


def _collection_exists(client, collection_name: str) -> bool:
    if hasattr(client, "collection_exists"):
        return bool(client.collection_exists(collection_name))
    return collection_name in [c.name for c in client.get_collections().collections]


def _query_qdrant_points(client, collection_name: str, query_vector: list[float], limit: int):
    if hasattr(client, "query_points"):
        response = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            with_payload=True,
            limit=limit,
        )
        return response.points
    return client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        with_payload=True,
        limit=limit,
    )


def _collection_name(prefix: str, chunks: list[dict]) -> str:
    digest = hashlib.sha1()
    for chunk in chunks:
        digest.update((chunk.get("chunk_id", "") + "\n").encode("utf-8"))
    return f"{prefix}_{digest.hexdigest()[:16]}"


def _normalize_chunk(chunk: dict, idx: int) -> dict:
    normalized = chunk.copy()
    normalized.setdefault("chunk_index", idx)
    normalized.setdefault("text", "")
    normalized["chunk_id"] = normalized.get("chunk_id") or _chunk_id(normalized, idx)
    return normalized


def _chunk_id(chunk: dict, idx: int) -> str:
    raw = "|".join([
        str(chunk.get("arxiv_id", "")),
        str(chunk.get("doi", "")),
        str(chunk.get("paper_title", "")),
        str(chunk.get("chunk_index", idx)),
        str(chunk.get("text", ""))[:500],
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _to_float_list(vector) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(v) for v in vector]
