"""
对话与检索记忆管理模块
"""
import json
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class SearchRound:
    """一轮检索的记录"""
    round_number: int
    query: str              # 主检索式（arXiv 语法，同时作为多源检索的结构化线索）
    strategy: str           # 检索策略说明
    results: list[dict]     # 原始检索结果
    review: dict            # LLM 审核结果
    relevant_papers: list[dict]  # 筛选出的相关论文
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def serialize(self) -> dict:
        """无损序列化（保留 results / relevant_papers / 完整 review），用于线程持久化。"""
        return {
            "round_number": self.round_number,
            "query": self.query,
            "strategy": self.strategy,
            "results": self.results,
            "review": self.review,
            "relevant_papers": self.relevant_papers,
            "timestamp": self.timestamp,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "SearchRound":
        """从 serialize() 的产物重建。缺失字段用安全默认值补齐。"""
        return cls(
            round_number=int(data.get("round_number", 0) or 0),
            query=str(data.get("query", "") or ""),
            strategy=str(data.get("strategy", "") or ""),
            results=list(data.get("results", []) or []),
            review=dict(data.get("review", {}) or {}),
            relevant_papers=list(data.get("relevant_papers", []) or []),
            timestamp=str(data.get("timestamp", "") or ""),
        )


class Memory:
    """Agent 记忆管理"""
    
    def __init__(self):
        self.user_query: str = ""           # 用户原始需求
        self.search_rounds: list[SearchRound] = []  # 所有检索轮次
        self.conversation: list[dict] = []  # 对话历史 (role, content)
        self.final_papers: list[dict] = []  # 最终推荐论文
        self.final_report: str = ""         # 最终报告
        self.evidence_chunks: list[dict] = []  # 报告引用的正文证据切片（RAG 命中）
    
    def set_user_query(self, query: str):
        """设置用户原始需求"""
        self.user_query = query
    
    def add_conversation(self, role: str, content: str):
        """添加对话记录"""
        self.conversation.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
    
    def add_search_round(self, query: str, strategy: str, results: list[dict],
                         review: dict, relevant_papers: list[dict]):
        """添加一轮检索记录"""
        round_num = len(self.search_rounds) + 1
        search_round = SearchRound(
            round_number=round_num,
            query=query,
            strategy=strategy,
            results=results,
            review=review,
            relevant_papers=relevant_papers,
        )
        self.search_rounds.append(search_round)
        return search_round
    
    def get_search_memory_text(self) -> str:
        """获取检索记忆的文本摘要，供 LLM 参考"""
        if not self.search_rounds:
            return "（尚无历史检索记录）"
        
        lines = []
        for sr in self.search_rounds:
            lines.append(f"=== 第 {sr.round_number} 轮检索 ===")
            lines.append(f"检索式: {sr.query}")
            lines.append(f"策略: {sr.strategy}")
            lines.append(f"返回结果数: {len(sr.results)}")
            lines.append(f"筛选后相关论文数: {len(sr.relevant_papers)}")
            if sr.review:
                lines.append(f"审核摘要: {sr.review.get('review_summary', 'N/A')}")
                lines.append(f"质量评分: {sr.review.get('overall_quality', 'N/A')}")
                if sr.review.get('refine_suggestions'):
                    lines.append(f"优化建议: {', '.join(sr.review['refine_suggestions'])}")
            lines.append("")
        
        return "\n".join(lines)
    
    def get_all_relevant_papers(self) -> list[dict]:
        """获取所有轮次中筛选出的相关论文（去重）"""
        seen_ids = set()
        papers = []
        for sr in self.search_rounds:
            for paper in sr.relevant_papers:
                pid = paper.get("arxiv_id") or paper.get("link") or paper.get("title")
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    papers.append(paper)
        return papers
    
    def set_final_results(self, papers: list[dict], report: str, evidence: list[dict] = None):
        """设置最终结果（论文、报告、可选的证据切片）。"""
        self.final_papers = papers
        self.final_report = report
        if evidence is not None:
            self.evidence_chunks = evidence
    
    def summary_dict(self) -> dict:
        """轻量摘要（仅计数 + 摘要文本），供调试/日志使用，不可用于持久化。"""
        return {
            "user_query": self.user_query,
            "conversation": self.conversation,
            "search_rounds": [
                {
                    "round_number": sr.round_number,
                    "query": sr.query,
                    "strategy": sr.strategy,
                    "results_count": len(sr.results),
                    "relevant_papers_count": len(sr.relevant_papers),
                    "review_summary": sr.review.get("review_summary", ""),
                    "overall_quality": sr.review.get("overall_quality", 0),
                    "timestamp": sr.timestamp,
                }
                for sr in self.search_rounds
            ],
            "final_papers": self.final_papers,
            "final_report": self.final_report,
        }

    # 向后兼容别名：旧代码可能仍调用 to_dict()。
    to_dict = summary_dict

    def serialize(self) -> dict:
        """无损序列化整个 Memory，用于线程 JSON 持久化（可被 deserialize 还原）。"""
        return {
            "user_query": self.user_query,
            "conversation": self.conversation,
            "search_rounds": [sr.serialize() for sr in self.search_rounds],
            "final_papers": self.final_papers,
            "final_report": self.final_report,
            "evidence_chunks": self.evidence_chunks,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "Memory":
        """从 serialize() 的产物完整重建 Memory。"""
        mem = cls()
        if not data:
            return mem
        mem.user_query = str(data.get("user_query", "") or "")
        mem.conversation = list(data.get("conversation", []) or [])
        mem.search_rounds = [
            SearchRound.deserialize(sr)
            for sr in (data.get("search_rounds", []) or [])
        ]
        mem.final_papers = list(data.get("final_papers", []) or [])
        mem.final_report = str(data.get("final_report", "") or "")
        mem.evidence_chunks = list(data.get("evidence_chunks", []) or [])
        return mem

    def reset(self):
        """重置记忆"""
        self.__init__()
