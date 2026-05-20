"""
多源论文检索 Agent - FastAPI Service
支持流式输出、实时检索状态展示、多格式导出
"""
import json
import os
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse

from core.agent import ArxivAgent, EventType, AgentEvent
from core.memory import Memory
from core import exporter
import config

# ===================== 全局状态 =====================
# 本项目已实现会话状态隔离 (Session Isolation)

# ===================== 核心交互逻辑 =====================

def run_search(user_query: str, api_key: str, chat_history: list, agent: ArxivAgent,
               base_url: str = None, model: str = None,
               max_search_rounds: int = None, max_results_per_round: int = None,
               providers: list[str] = None, provider_settings: dict[str, str] = None):
    """
    运行 Agent 对话/检索流程（generator，支持流式输出和多轮对话，会话级隔离）
    Yields: (chat_history, status_text, papers_md, final_md, agent)
    """
    if not user_query.strip():
        yield chat_history, "⚠️ 请输入检索需求", "", "", agent
        return

    chat_history = chat_history or []
    effective_key = api_key.strip() if api_key.strip() else config.DEEPSEEK_API_KEY
    if not effective_key:
        quick_intent = ArxivAgent.quick_intent(
            user_query,
            has_context=bool(agent and agent.has_context),
            previous_query=agent.memory.user_query if agent else "",
        )
        if quick_intent and not quick_intent.get("needs_search", True):
            chat_history = _append_user_message(chat_history, user_query)
            response_text = quick_intent.get("response", "")
            chat_history.append({"role": "assistant", "content": response_text})
            if agent:
                agent.memory.add_conversation("user", user_query)
                agent.memory.add_conversation("assistant", response_text)
            yield chat_history, "✅ 回复完成", "", "", agent
            return
        yield chat_history, "⚠️ 请提供 API Key", "", "", agent
        return

    # 会话级实例化
    if agent is None or not isinstance(agent, ArxivAgent):
        agent = ArxivAgent(
            api_key=effective_key,
            base_url=base_url,
            model=model,
            max_search_rounds=max_search_rounds,
            max_results_per_round=max_results_per_round,
            providers=providers,
            provider_settings=provider_settings,
        )
    else:
        agent.update_config(
            api_key=effective_key,
            base_url=base_url,
            model=model,
            max_search_rounds=max_search_rounds,
            max_results_per_round=max_results_per_round,
            providers=providers,
            provider_settings=provider_settings,
        )

    # 添加用户消息到 UI 对话
    chat_history = _append_user_message(chat_history, user_query)

    # 用于累积 assistant 消息
    step_buffer = ""           # 当前步骤标题
    thinking_buffer = ""       # LLM 流式内容
    report_buffer = ""         # 报告流式内容
    chat_response_buffer = ""  # 多轮对话回复流式内容
    status_text = ""
    papers_md = ""
    final_md = ""

    # 使用 agent.chat() 支持多轮对话
    for event in agent.chat(user_query):
        et = event.event_type

        if et == EventType.STEP_START:
            # 新步骤开始 — 将之前的思考内容刷新到对话
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""

            step_buffer = event.step_name or ""
            status_text = event.content
            if event.content:
                chat_history.append({"role": "assistant",
                                     "content": event.content})
            yield chat_history, status_text, papers_md, final_md, agent

        elif et == EventType.THINKING:
            thinking_buffer += event.content
            # 每累积一些内容就更新展示
            display = _format_thinking(step_buffer, thinking_buffer)
            # 更新最后一条或追加
            if (chat_history and chat_history[-1]["role"] == "assistant"
                    and chat_history[-1].get("_thinking")):
                chat_history[-1]["content"] = display
            else:
                chat_history.append({"role": "assistant",
                                     "content": display,
                                     "_thinking": True})
            yield chat_history, status_text, papers_md, final_md, agent

        elif et == EventType.SEARCH_START:
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""
            status_text = event.content
            chat_history.append({"role": "assistant", "content": event.content})
            yield chat_history, status_text, papers_md, final_md, agent

        elif et == EventType.SEARCH_DONE:
            status_text = event.content
            papers = event.data.get("papers", []) if event.data else []
            papers_md = _format_papers_table(papers, event.round_num)
            chat_history.append({"role": "assistant", "content": event.content})
            yield chat_history, status_text, papers_md, final_md, agent

        elif et == EventType.REVIEW:
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""
            relevant = event.data.get("relevant_papers", []) if event.data else []
            review = event.data.get("review", {}) if event.data else {}
            summary = review.get("review_summary", "")
            quality = review.get("overall_quality", "N/A")
            should_refine = review.get("should_refine", False)

            review_msg = (
                f"📋 **审核结果** (质量: {quality})\n\n"
                f"{summary}\n\n"
                f"筛选出 **{len(relevant)}** 篇相关论文。"
            )
            if should_refine:
                reason = review.get("refine_reason", "")
                review_msg += f"\n\n🔄 建议优化: {reason}"

            chat_history.append({"role": "assistant", "content": review_msg})
            status_text = f"审核完成 | 质量: {quality}"
            yield chat_history, status_text, papers_md, final_md, agent

        elif et == EventType.REFINE:
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""
            refine_data = event.data.get("refine", {}) if event.data else {}
            new_query = refine_data.get("arxiv_query", "")
            changes = refine_data.get("changes_made", [])
            changes_str = "\n".join(f"  - {c}" for c in changes) if changes else ""

            refine_msg = f"🔄 **策略优化完成**\n\n新检索式: `{new_query}`"
            if changes_str:
                refine_msg += f"\n\n调整:\n{changes_str}"

            chat_history.append({"role": "assistant", "content": refine_msg})
            yield chat_history, "策略已优化，准备下一轮检索...", papers_md, final_md, agent

        elif et == EventType.REPORT:
            report_buffer += event.content
            final_md = report_buffer
            # 流式更新报告到对话
            if (chat_history and chat_history[-1]["role"] == "assistant"
                    and chat_history[-1].get("_report")):
                chat_history[-1]["content"] = f"📊 **最终报告**\n\n{report_buffer}"
            else:
                chat_history.append({"role": "assistant",
                                     "content": f"📊 **最终报告**\n\n{report_buffer}",
                                     "_report": True})
            yield chat_history, "正在生成报告...", papers_md, final_md, agent

        elif et == EventType.CHAT_RESPONSE:
            # 多轮对话的流式回复
            chat_response_buffer += event.content
            if (chat_history and chat_history[-1]["role"] == "assistant"
                    and chat_history[-1].get("_chat_reply")):
                chat_history[-1]["content"] = chat_response_buffer
            else:
                chat_history.append({"role": "assistant",
                                     "content": chat_response_buffer,
                                     "_chat_reply": True})
            status_text = "💬 正在回复..."
            yield chat_history, status_text, papers_md, final_md, agent

        elif et == EventType.DONE:
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""
            done_data = event.data or {}
            if done_data.get("type") == "chat":
                status_text = "✅ 回复完成"
            else:
                final_papers = done_data.get("final_papers", [])
                final_md = done_data.get("report", final_md)
                status_text = f"✅ 检索完成！共推荐 {len(final_papers)} 篇论文。"
            yield chat_history, status_text, papers_md, final_md, agent

        elif et == EventType.ERROR:
            chat_history.append({"role": "assistant", "content": event.content})
            status_text = "❌ 出错"
            yield chat_history, status_text, papers_md, final_md, agent


def _append_user_message(chat_history: list, user_query: str) -> list:
    """追加当前用户消息，同时兼容旧前端已先追加的历史。"""
    chat_history = list(chat_history or [])
    if not (
        chat_history
        and chat_history[-1].get("role") == "user"
        and str(chat_history[-1].get("content", "")).strip() == user_query.strip()
    ):
        chat_history.append({"role": "user", "content": user_query})
    return chat_history


def _flush_thinking(chat_history, step_name, thinking_text):
    """将思考内容整理后追加到对话"""
    # 清理原始 JSON（只展示摘要）
    summary = _extract_thinking_summary(thinking_text)
    if summary:
        # 替换掉 _thinking 标记的条目
        if (chat_history and chat_history[-1]["role"] == "assistant"
                and chat_history[-1].get("_thinking")):
            chat_history[-1] = {"role": "assistant",
                                "content": f"💭 **{step_name or 'Agent 思考'}**\n\n{summary}"}
        else:
            chat_history.append({"role": "assistant",
                                 "content": f"💭 **{step_name or 'Agent 思考'}**\n\n{summary}"})


def _format_thinking(step_name, text):
    """格式化实时思考内容"""
    # 截取末尾显示，避免太长
    display_text = text[-800:] if len(text) > 800 else text
    if len(text) > 800:
        display_text = "...\n" + display_text
    return f"💭 **{step_name or 'Agent 思考中...'}**\n\n```\n{display_text}\n```"


def _extract_thinking_summary(text):
    """从 LLM 思考输出中提取关键信息摘要"""
    try:
        data = json.loads(text[text.find('{'):text.rfind('}') + 1])
        parts = []
        if "understanding" in data:
            parts.append(f"**理解**: {data['understanding']}")
        if "arxiv_query" in data:
            parts.append(f"**检索式**: `{data['arxiv_query']}`")
        if "strategy" in data:
            parts.append(f"**策略**: {data['strategy']}")
        if "optimization_analysis" in data:
            parts.append(f"**优化分析**: {data['optimization_analysis']}")
        if "review_summary" in data:
            parts.append(f"**审核**: {data['review_summary']}")
        if "overall_quality" in data:
            parts.append(f"**质量**: {data['overall_quality']}")
        if parts:
            return "\n\n".join(parts)
    except (json.JSONDecodeError, ValueError):
        pass
    # JSON 解析失败则截取前 300 字
    return text[:300] + ("..." if len(text) > 300 else "")


def _format_papers_table(papers: list[dict], round_num: int = 0) -> str:
    """将论文列表格式化为 Markdown 展示"""
    if not papers:
        return f"### 第 {round_num} 轮检索结果\n\n暂无结果"

    lines = [f"### 第 {round_num} 轮检索结果（共 {len(papers)} 篇）\n"]
    for i, p in enumerate(papers):
        title = p.get("title", "无标题")
        authors = ", ".join(p.get("authors", [])[:3])
        if len(p.get("authors", [])) > 3:
            authors += " 等"
        date = p.get("published", "")[:10]
        link = p.get("link", "")
        cats = ", ".join(p.get("categories", [])[:2])
        abstract = p.get("abstract", "")[:150]
        source = p.get("source", "arxiv")
        citation_count = p.get("citation_count", 0)
        meta = f"📅 {date} | 👤 {authors} | 🏷️ {cats} | 🔎 {source}"
        if citation_count:
            meta += f" | 引用 {citation_count}"

        lines.append(f"**{i+1}. [{title}]({link})**")
        lines.append(f"   {meta}")
        lines.append(f"   > {abstract}...")
        lines.append("")

    return "\n".join(lines)


# ===================== 导出功能 =====================

def export_conversation(chat_history):
    """导出完整的UI主对话记录(包括中间思路和最终报告)"""
    if not chat_history:
        return None, "⚠️ 暂无对话记录可导出"
        
    lines = [
        "# ArXiv Agent 完整交互记录",
        f"**导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        ""
    ]
    
    for msg in chat_history:
        role_label = "🧑 用户" if msg.get("role") == "user" else "🤖 助手"
        lines.append(f"### {role_label}")
        lines.append("")
        
        content_val = msg.get("content", "")
        if isinstance(content_val, list) or isinstance(content_val, tuple):
            text_parts = []
            for part in content_val:
                if isinstance(part, dict) and "text" in part:
                    text_parts.append(part.get("text", ""))
                else:
                    text_parts.append(str(part))
            lines.append("".join(text_parts))
        else:
            lines.append(str(content_val))
            
        lines.append("")
        lines.append("---")
        lines.append("")
        
    content = "\n".join(lines)
    filepath = exporter.save_export(
        content, f"完整对话记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    return filepath, f"✅ 对话已导出: {filepath}"


def export_results_md(agent: ArxivAgent):
    """导出检索结果 Markdown"""
    if not agent or not agent.memory.search_rounds:
        return None, "⚠️ 暂无检索结果可导出"
    content = exporter.export_search_results_md(agent.memory)
    filepath = exporter.save_export(
        content, f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    return filepath, f"✅ 检索结果已导出: {filepath}"


def export_results_csv(agent: ArxivAgent):
    """导出检索结果 CSV"""
    if not agent or not agent.memory.search_rounds:
        return None, "⚠️ 暂无检索结果可导出"
    content = exporter.export_search_results_csv(agent.memory)
    filepath = exporter.save_export(
        content, f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    return filepath, f"✅ CSV 已导出: {filepath}"


def export_results_json(agent: ArxivAgent):
    """导出检索结果 JSON"""
    if not agent or not agent.memory.search_rounds:
        return None, "⚠️ 暂无检索结果可导出"
    content = exporter.export_search_results_json(agent.memory)
    filepath = exporter.save_export(
        content, f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    return filepath, f"✅ JSON 已导出: {filepath}"


def export_report(agent: ArxivAgent):
    """导出最终报告"""
    if not agent:
        return None, "⚠️ 暂无报告可导出"
    content = exporter.export_final_report(agent.memory)
    if not content or content.strip() == "":
        return None, "⚠️ 暂无报告可导出"
    filepath = exporter.save_export(
        content, f"最终报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    return filepath, f"✅ 报告已导出: {filepath}"


# ===================== FastAPI Web App Setup =====================

app = FastAPI(title=f"多源论文检索 Agent v{config.APP_VERSION}")
agents_db = {}

@app.get("/", response_class=HTMLResponse)
def read_index():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h3>Error: index.html not found</h3>", status_code=404)

@app.post("/api/search")
async def api_search(request: Request):
    data = await request.json()
    user_query = data.get("query", "")
    api_key = data.get("api_key", "")
    base_url = data.get("base_url", "")
    model = data.get("model", "")
    
    max_search_rounds = data.get("max_search_rounds")
    if max_search_rounds is not None:
        try:
            max_search_rounds = int(max_search_rounds)
        except ValueError:
            max_search_rounds = None
            
    max_results_per_round = data.get("max_results_per_round")
    if max_results_per_round is not None:
        try:
            max_results_per_round = int(max_results_per_round)
        except ValueError:
            max_results_per_round = None
            
    providers = data.get("providers")
    provider_settings = {
        "openalex_mailto": str(data.get("openalex_mailto", "") or "").strip(),
        "crossref_mailto": str(data.get("crossref_mailto", "") or "").strip(),
        "semantic_scholar_api_key": str(data.get("semantic_scholar_api_key", "") or "").strip(),
    }
    
    history = data.get("history", [])
    session_id = data.get("session_id", "default")
    
    agent = agents_db.get(session_id)
    
    async def ndjson_generator():
        nonlocal agent
        try:
            for chat_history, status_text, papers_md, final_md, updated_agent in run_search(
                user_query, api_key, history, agent,
                base_url=base_url, model=model,
                max_search_rounds=max_search_rounds,
                max_results_per_round=max_results_per_round,
                providers=providers,
                provider_settings=provider_settings,
            ):
                agent = updated_agent
                agents_db[session_id] = agent
                yield json.dumps({
                    "chat_history": chat_history,
                    "status_text": status_text,
                    "papers_md": papers_md,
                    "final_md": final_md
                }, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({
                "status_text": f"❌ 出错: {str(e)}"
            }, ensure_ascii=False) + "\n"
            
    return StreamingResponse(ndjson_generator(), media_type="application/x-ndjson")

@app.post("/api/export")
async def api_export(request: Request):
    data = await request.json()
    export_type = data.get("type")
    history = data.get("history", [])
    session_id = data.get("session_id", "default")
    
    agent = agents_db.get(session_id)
    
    filepath = None
    status = ""
    
    try:
        if export_type == "chat":
            filepath, status = export_conversation(history)
        elif export_type == "md":
            filepath, status = export_results_md(agent)
        elif export_type == "csv":
            filepath, status = export_results_csv(agent)
        elif export_type == "json":
            filepath, status = export_results_json(agent)
        elif export_type == "report":
            filepath, status = export_report(agent)
        else:
            return {"success": False, "status": "未知导出类型"}
            
        if filepath and os.path.exists(filepath):
            return {
                "success": True, 
                "filename": os.path.basename(filepath), 
                "status": status
            }
        return {"success": False, "status": status or "未生成导出 file"}
    except Exception as e:
        return {"success": False, "status": f"导出出错: {str(e)}"}

@app.get("/api/download")
def api_download(file: str):
    filename = os.path.basename(file)
    safe_path = os.path.join(os.path.dirname(__file__), "exports", filename)
    if os.path.exists(safe_path):
        return FileResponse(safe_path, filename=filename)
    return HTMLResponse(content="<h3>文件不存在</h3>", status_code=404)

@app.post("/api/clear")
async def api_clear(request: Request):
    data = await request.json()
    session_id = data.get("session_id", "default")
    agent = agents_db.get(session_id)
    if agent:
        agent.reset()
        if session_id in agents_db:
            del agents_db[session_id]
    return {"success": True, "status": "状态已清除"}


# ===================== 入口 =====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
