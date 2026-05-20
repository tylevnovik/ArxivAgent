"""
轻量级可插拔 RAG 检索器模块
定义了检索接口类，便于未来平滑切换到向量数据库/Embedding 检索。
"""
import math
import re
from collections import Counter
from abc import ABC, abstractmethod


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
