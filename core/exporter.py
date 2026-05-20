"""
导出功能模块
支持 Markdown、JSON、CSV 格式导出
"""
import csv
import io
import json
import os
from datetime import datetime

import config
from core.memory import Memory


def export_conversation_md(memory: Memory) -> str:
    """导出对话历史为 Markdown 格式"""
    lines = [
        f"# 多源论文检索对话记录",
        f"",
        f"**用户需求**: {memory.user_query}",
        f"**导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
        "---",
        "",
    ]
    
    for msg in memory.conversation:
        role_label = "🧑 用户" if msg["role"] == "user" else "🤖 助手"
        timestamp = msg.get("timestamp", "")[:19].replace("T", " ")
        lines.append(f"### {role_label} ({timestamp})")
        lines.append("")
        lines.append(msg["content"])
        lines.append("")
        lines.append("---")
        lines.append("")
    
    return "\n".join(lines)


def export_search_results_md(memory: Memory) -> str:
    """导出检索结果为 Markdown 格式"""
    lines = [
        f"# 多源论文检索结果报告",
        f"",
        f"**用户需求**: {memory.user_query}",
        f"**检索轮次**: {len(memory.search_rounds)}",
        f"**导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
    ]
    
    for sr in memory.search_rounds:
        lines.append(f"## 第 {sr.round_number} 轮检索")
        lines.append(f"")
        lines.append(f"**检索式**: `{sr.query}`")
        lines.append(f"**策略**: {sr.strategy}")
        lines.append(f"**返回结果数**: {len(sr.results)}")
        lines.append(f"**质量评分**: {sr.review.get('overall_quality', 'N/A')}")
        lines.append(f"")
        
        if sr.relevant_papers:
            lines.append("### 相关论文")
            lines.append("")
            for i, paper in enumerate(sr.relevant_papers):
                lines.append(f"#### {i + 1}. {paper['title']}")
                lines.append(f"- **来源**: {paper.get('source', 'arxiv')}")
                lines.append(f"- **作者**: {', '.join(paper.get('authors', [])[:5])}")
                lines.append(f"- **类别**: {', '.join(paper.get('categories', []))}")
                lines.append(f"- **发表日期**: {paper.get('published', '')[:10]}")
                if paper.get("doi"):
                    lines.append(f"- **DOI**: {paper['doi']}")
                if paper.get("citation_count"):
                    lines.append(f"- **引用数**: {paper['citation_count']}")
                lines.append(f"- **链接**: [{paper.get('link', '')}]({paper.get('link', '')})")
                if paper.get('abstract'):
                    lines.append(f"- **摘要**: {paper['abstract'][:200]}...")
                lines.append("")
        
        lines.append("---")
        lines.append("")
    
    return "\n".join(lines)


def export_search_results_csv(memory: Memory) -> str:
    """导出检索结果为 CSV 格式"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        "轮次", "来源", "标题", "作者", "类别", "发表日期", "DOI", "引用数", "摘要", "链接", "PDF链接"
    ])
    
    for sr in memory.search_rounds:
        for paper in sr.relevant_papers:
            writer.writerow([
                sr.round_number,
                paper.get("source", "arxiv"),
                paper.get("title", ""),
                "; ".join(paper.get("authors", [])),
                "; ".join(paper.get("categories", [])),
                paper.get("published", "")[:10],
                paper.get("doi", ""),
                paper.get("citation_count", ""),
                paper.get("abstract", "")[:200],
                paper.get("link", ""),
                paper.get("pdf_link", ""),
            ])
    
    return output.getvalue()


def export_search_results_json(memory: Memory) -> str:
    """导出检索结果为 JSON 格式"""
    data = {
        "user_query": memory.user_query,
        "export_time": datetime.now().isoformat(),
        "search_rounds": [],
    }
    
    for sr in memory.search_rounds:
        data["search_rounds"].append({
            "round_number": sr.round_number,
            "query": sr.query,
            "strategy": sr.strategy,
            "review_summary": sr.review.get("review_summary", ""),
            "overall_quality": sr.review.get("overall_quality", 0),
            "relevant_papers": sr.relevant_papers,
        })
    
    return json.dumps(data, ensure_ascii=False, indent=2)


def export_final_report(memory: Memory) -> str:
    """导出最终报告"""
    if memory.final_report:
        return memory.final_report
    
    # 如果没有预生成的报告，构建简单报告
    lines = [
        f"# 多源论文检索最终报告",
        f"",
        f"**用户需求**: {memory.user_query}",
        f"**检索轮次**: {len(memory.search_rounds)}",
        f"**推荐论文数**: {len(memory.final_papers)}",
        f"**导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
        "## 推荐论文列表",
        "",
    ]
    
    for i, paper in enumerate(memory.final_papers):
        lines.append(f"### {i + 1}. {paper['title']}")
        lines.append(f"- **来源**: {paper.get('source', 'arxiv')}")
        lines.append(f"- **作者**: {', '.join(paper.get('authors', [])[:5])}")
        lines.append(f"- **类别**: {', '.join(paper.get('categories', []))}")
        lines.append(f"- **发表日期**: {paper.get('published', '')[:10]}")
        if paper.get("doi"):
            lines.append(f"- **DOI**: {paper['doi']}")
        if paper.get("citation_count"):
            lines.append(f"- **引用数**: {paper['citation_count']}")
        lines.append(f"- **链接**: [{paper.get('link', '')}]({paper.get('link', '')})")
        if paper.get("abstract"):
            lines.append(f"- **摘要**: {paper['abstract'][:300]}")
        lines.append("")
    
    return "\n".join(lines)


def save_export(content: str, filename: str) -> str:
    """保存导出内容到文件，返回文件路径"""
    filepath = os.path.join(config.EXPORT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath
