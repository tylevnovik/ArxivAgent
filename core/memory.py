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


class Memory:
    """Agent 记忆管理"""
    
    def __init__(self):
        self.user_query: str = ""           # 用户原始需求
        self.search_rounds: list[SearchRound] = []  # 所有检索轮次
        self.conversation: list[dict] = []  # 对话历史 (role, content)
        self.final_papers: list[dict] = []  # 最终推荐论文
        self.final_report: str = ""         # 最终报告
    
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
    
    def set_final_results(self, papers: list[dict], report: str):
        """设置最终结果"""
        self.final_papers = papers
        self.final_report = report
    
    def to_dict(self) -> dict:
        """序列化为字典"""
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
    
    def reset(self):
        """重置记忆"""
        self.__init__()
