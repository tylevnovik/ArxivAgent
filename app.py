"""
多源论文检索 Agent - FastAPI Service
支持流式输出、实时检索状态展示、多格式导出

对外提供两套 API：
1. 新契约（推荐）：/api/threads/** —— 线程持久化 + 结构化事件 (AgentEventEnvelope)
2. 旧契约（仅 index.html 兼容，已 deprecated）：/api/search、/api/export、/api/clear
"""
import json
import os
import queue
import threading
from datetime import datetime
from typing import Optional

import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from openai import OpenAI

from core.agent import ArxivAgent, EventType, AgentEvent
from core.contracts import (
    AgentEventEnvelope,
    CancelResponse,
    ConfigHealth,
    ConfigHealthRequest,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    EvidenceChunk,
    ExportRequest,
    ExportResponse,
    HealthResponse,
    MessagePatchRequest,
    MessageRequest,
    Paper,
    ProviderHealth,
    ThreadCreateRequest,
    ThreadCreateResponse,
    ThreadDetail,
    ThreadListResponse,
    ThreadMeta,
    ThreadPatchRequest,
)
from core.llm import CancelledError
from core.memory import Memory
from core.threads import Thread, thread_manager
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 旧契约兼容（仅 index.html）：session_id -> ArxivAgent。已 deprecated。
agents_db: dict = {}


# ---------- 错误响应工具 ----------

def _error_json(code: ErrorCode, message: str, recoverable: bool = True,
                status_code: int = 400) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, recoverable=recoverable))
    return JSONResponse(status_code=status_code, content=body.model_dump())


# 已知检索源白名单（与 core/search_service._make_provider 的分支一致）。
# 未知 provider 名会触发 INVALID_PROVIDER 结构化错误，而不是 500。
KNOWN_PROVIDERS = {"arxiv", "openalex", "crossref", "semantic_scholar", "semanticscholar", "s2"}


def _validate_providers(providers: Optional[list[str]]) -> Optional[list[str]]:
    """返回规整后的 provider 名列表；含未知名时返回 None 表示校验失败。"""
    if providers is None:
        return None  # 调用方按"未指定"处理（用进程默认）
    names = [str(p).strip().lower() for p in providers if str(p).strip()]
    unknown = [n for n in names if n not in KNOWN_PROVIDERS]
    if unknown:
        return None
    return names


# ---------- AgentEvent -> AgentEventEnvelope 映射 ----------

def _envelope_from_agent_event(event: AgentEvent) -> AgentEventEnvelope:
    """把内部 AgentEvent 映射成对外稳定的事件信封。"""
    et = event.event_type
    r = event.round_num or None

    if et == EventType.THINKING:
        return AgentEventEnvelope(type="thinking", message=event.content, round=r,
                                   payload={"step": event.step_name})
    if et == EventType.SEARCH_START:
        return AgentEventEnvelope(type="searching", message=event.content, round=r,
                                   payload={"step": event.step_name})
    if et == EventType.SEARCH_DONE:
        papers = (event.data or {}).get("papers", [])
        return AgentEventEnvelope(
            type="searching_done", message=event.content, round=r,
            payload={"papers": [Paper.from_dict(p).model_dump() for p in papers],
                     "step": event.step_name},
        )
    if et == EventType.REVIEW:
        data = event.data or {}
        return AgentEventEnvelope(
            type="reviewing", message=event.content, round=r,
            payload={"review": data.get("review", {}),
                     "relevant_papers": [Paper.from_dict(p).model_dump()
                                         for p in data.get("relevant_papers", [])]},
        )
    if et == EventType.REFINE:
        return AgentEventEnvelope(type="refining", message=event.content, round=r,
                                   payload=event.data or {})
    if et == EventType.REPORT:
        return AgentEventEnvelope(type="report", message=event.content, round=r)
    if et == EventType.CHAT_RESPONSE:
        return AgentEventEnvelope(type="chat", message=event.content, round=r)
    if et == EventType.ERROR:
        return AgentEventEnvelope(type="error", message=event.content, round=r)
    if et == EventType.DONE:
        data = event.data or {}
        if data.get("cancelled"):
            return AgentEventEnvelope(type="cancelled", message=event.content, round=r)
        if data.get("type") == "chat":
            return AgentEventEnvelope(type="done", message=event.content, round=r,
                                       payload={"kind": "chat"})
        # 检索完成：把最终论文作为结构化 papers 一并发出
        final_papers = data.get("final_papers", [])
        report = data.get("report", "")
        evidence = data.get("evidence", [])
        return AgentEventEnvelope(
            type="done", message=event.content, round=r,
            payload={"kind": "search",
                     "papers": [Paper.from_dict(p).model_dump() for p in final_papers],
                     "report": report,
                     "evidence": [EvidenceChunk.from_dict(e).model_dump() for e in evidence]},
        )
    # STEP_START 及其它：归一化为 intent（步骤提示）
    return AgentEventEnvelope(type="intent", message=event.content, round=r,
                               payload={"step": event.step_name})


# ===================== 基础端点 =====================

@app.get("/api/health")
def api_health():
    return HealthResponse(ok=True, version=config.APP_VERSION)


@app.get("/", response_class=HTMLResponse)
def read_index():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h3>Error: index.html not found</h3>", status_code=404)


# ===================== 系统依赖探测 =====================

# 启动时需要可用的核心模块。缺任意一个，向导会提示用户用 uv 恢复。
REQUIRED_MODULES = [
    "fastapi", "uvicorn", "openai", "requests",
    "pypdf", "qdrant_client", "bm25s",
]


def _probe_modules():
    """返回 (missing, versions)。"""
    import importlib
    import sys
    missing = []
    versions = {}
    for name in REQUIRED_MODULES:
        try:
            mod = importlib.import_module(name)
            versions[name] = getattr(mod, "__version__", "unknown")
        except Exception as e:  # noqa: BLE001
            missing.append({"module": name, "error": str(e)[:200]})
    return missing, versions, sys.version


@app.get("/api/system/deps")
def api_system_deps():
    """
    供打包版启动向导用：返回 Python 版本 + 缺失模块 + 建议的 uv 命令。
    不需要后端"正常运行"也能调用（因为它就是在排查后端为何不能正常运行）。
    """
    missing, versions, py_version = _probe_modules()
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    return {
        "ok": len(missing) == 0,
        "python_version": py_version,
        "missing_modules": missing,
        "installed_versions": versions,
        "backend_dir": backend_dir,
        "requirements_file": os.path.join(backend_dir, "requirements.txt"),
        # 给向导用的命令模板（前端可复制）
        "uv_commands": {
            "install_uv": "pip install uv",
            "setup": "uv python install 3.12 && uv sync",
            "sync_only": "uv sync",
        },
    }


# ===================== 线程 CRUD =====================

@app.get("/api/threads")
def api_list_threads():
    threads = thread_manager.list_threads()
    return ThreadListResponse(threads=[ThreadMeta(**t.meta_dict()) for t in threads])


@app.post("/api/threads")
def api_create_thread(body: ThreadCreateRequest):
    thread = thread_manager.create(title=body.title)
    return ThreadCreateResponse(thread=ThreadMeta(**thread.meta_dict()))


@app.get("/api/threads/{thread_id}")
def api_get_thread(thread_id: str):
    thread = thread_manager.get(thread_id)
    if thread is None:
        return _error_json(ErrorCode.NOT_FOUND, f"线程 {thread_id} 不存在",
                           recoverable=False, status_code=404)
    return ThreadDetail(**thread.detail_dict())


@app.patch("/api/threads/{thread_id}")
def api_rename_thread(thread_id: str, body: ThreadPatchRequest):
    title = (body.title or "").strip()
    if not title:
        return _error_json(ErrorCode.VALIDATION, "标题不能为空", status_code=400)
    thread = thread_manager.rename(thread_id, title)
    if thread is None:
        return _error_json(ErrorCode.NOT_FOUND, f"线程 {thread_id} 不存在",
                           recoverable=False, status_code=404)
    return ThreadMeta(**thread.meta_dict())


def _message_mutation_busy_error(thread_id: str) -> Optional[JSONResponse]:
    existing = thread_manager.get_task(thread_id)
    if existing is not None and not existing.finished.is_set():
        return _error_json(
            ErrorCode.THREAD_BUSY,
            "该线程正在运行，完成或停止后才能编辑消息。",
            recoverable=True,
            status_code=409,
        )
    return None


def _resolve_message(thread: Thread, message_index: int) -> tuple[Optional[dict], Optional[JSONResponse]]:
    if message_index < 0 or message_index >= len(thread.memory.conversation):
        return None, _error_json(
            ErrorCode.NOT_FOUND,
            f"消息 {message_index} 不存在",
            recoverable=False,
            status_code=404,
        )
    return thread.memory.conversation[message_index], None


def _sync_derived_message_fields(thread: Thread, old_content: str, new_content: str = "") -> None:
    if old_content == thread.memory.final_report:
        thread.memory.final_report = new_content
    if old_content == thread.memory.user_query:
        if new_content:
            thread.memory.user_query = new_content
        else:
            first_user = next(
                (m.get("content", "") for m in thread.memory.conversation if m.get("role") == "user"),
                "",
            )
            thread.memory.user_query = first_user


@app.patch("/api/threads/{thread_id}/messages/{message_index}")
def api_patch_thread_message(thread_id: str, message_index: int, body: MessagePatchRequest):
    thread, err = _resolve_thread_for_message(thread_id)
    if err is not None:
        return err
    busy = _message_mutation_busy_error(thread_id)
    if busy is not None:
        return busy

    content = (body.content or "").strip()
    if not content:
        return _error_json(ErrorCode.VALIDATION, "消息内容不能为空", status_code=400)

    message, msg_err = _resolve_message(thread, message_index)
    if msg_err is not None:
        return msg_err

    old_content = str(message.get("content", "") or "")
    message["content"] = content
    if not message.get("timestamp"):
        message["timestamp"] = datetime.now().isoformat()
    _sync_derived_message_fields(thread, old_content, content)
    thread.save()
    return ThreadDetail(**thread.detail_dict())


@app.delete("/api/threads/{thread_id}/messages/{message_index}")
def api_delete_thread_message(thread_id: str, message_index: int):
    thread, err = _resolve_thread_for_message(thread_id)
    if err is not None:
        return err
    busy = _message_mutation_busy_error(thread_id)
    if busy is not None:
        return busy

    message, msg_err = _resolve_message(thread, message_index)
    if msg_err is not None:
        return msg_err

    old_content = str(message.get("content", "") or "")
    del thread.memory.conversation[message_index]
    _sync_derived_message_fields(thread, old_content, "")
    thread.save()
    return ThreadDetail(**thread.detail_dict())


@app.delete("/api/threads/{thread_id}")
def api_delete_thread(thread_id: str):
    # 若该线程有任务在跑，先请求取消
    thread_manager.request_cancel(thread_id)
    ok = thread_manager.delete(thread_id)
    if not ok:
        return _error_json(ErrorCode.NOT_FOUND, f"线程 {thread_id} 不存在",
                           recoverable=False, status_code=404)
    return {"ok": True, "status": "deleted"}


# ===================== 线程内消息（流式事件） =====================

def _resolve_thread_for_message(thread_id: str) -> tuple[Optional[Thread], Optional[JSONResponse]]:
    thread = thread_manager.get(thread_id)
    if thread is None:
        return None, _error_json(ErrorCode.NOT_FOUND, f"线程 {thread_id} 不存在",
                                 recoverable=False, status_code=404)
    # 同一线程已有任务在跑：拒绝并发
    existing = thread_manager.get_task(thread_id)
    if existing is not None and not existing.finished.is_set():
        return None, _error_json(ErrorCode.THREAD_BUSY, "该线程已有检索任务在运行",
                                 recoverable=True, status_code=409)
    return thread, None


def _build_agent_for_thread(thread: Thread, msg: MessageRequest, api_key: str) -> ArxivAgent:
    provider_settings = {
        "openalex_mailto": (msg.openalex_mailto or "").strip(),
        "crossref_mailto": (msg.crossref_mailto or "").strip(),
        "semantic_scholar_api_key": (msg.semantic_scholar_api_key or "").strip(),
    }
    agent = ArxivAgent(
        api_key=api_key,
        base_url=msg.base_url or None,
        model=msg.model or None,
        max_search_rounds=msg.max_search_rounds,
        max_results_per_round=msg.max_results_per_round,
        providers=msg.providers,
        provider_settings=provider_settings,
    )
    # 用已持久化的 memory 恢复上下文，支持多轮
    agent.memory = thread.memory
    return agent


def _run_agent_worker(thread: Thread, agent: ArxivAgent, query: str,
                      out_queue: "queue.Queue", handle):
    """后台线程：跑 agent.chat()，把事件投递到队列。结束后投递哨兵。"""
    final_status = "done"
    last_error: Optional[str] = None
    try:
        for event in agent.chat(query):
            if handle.cancel_event.is_set():
                # 已取消：让 chat 内部边界抛 CancelledError；这里不重复处理
                pass
            out_queue.put(_envelope_from_agent_event(event))
    except CancelledError:
        final_status = "cancelled"
        out_queue.put(AgentEventEnvelope(type="cancelled", message="已停止当前检索。"))
    except Exception as e:  # noqa: BLE001 - 顶层兜底，保证流不挂死
        final_status = "error"
        last_error = str(e)
        out_queue.put(AgentEventEnvelope(type="error", message=f"❌ Agent 运行出错: {e}"))
    finally:
        # 持久化线程状态（memory 已被 agent 更新）
        thread.status = final_status
        thread.last_error = last_error
        # 自动起标题：若仍是默认且线程里已有用户消息
        if (thread.title in ("", "新对话") and thread.memory.conversation):
            first_user = next(
                (m.get("content", "") for m in thread.memory.conversation
                 if m.get("role") == "user"),
                "",
            )
            thread.title = (first_user[:30] + ("…" if len(first_user) > 30 else "")) or "新对话"
        try:
            thread.save()
        except Exception:
            pass
        thread_manager.finish_task(thread.id)
        out_queue.put(None)  # 哨兵


@app.post("/api/threads/{thread_id}/messages")
async def api_thread_message(thread_id: str, body: MessageRequest):
    thread, err = _resolve_thread_for_message(thread_id)
    if err is not None:
        return err

    query = (body.query or "").strip()
    if not query:
        return _error_json(ErrorCode.VALIDATION, "query 不能为空", status_code=400)

    # 解析 API Key：请求 > 环境变量
    api_key = (body.api_key or "").strip()
    if not api_key:
        return _error_json(
            ErrorCode.NO_API_KEY,
            "未提供 API Key。请在设置中配置，或设置环境变量 DEEPSEEK_API_KEY。",
            recoverable=True, status_code=400,
        )

    # 校验检索源：未知名直接返回结构化错误，避免构造 agent 时 500
    if body.providers is not None:
        normalized = _validate_providers(body.providers)
        if normalized is None:
            unknown = sorted({str(p).strip().lower() for p in body.providers
                              if str(p).strip().lower() not in KNOWN_PROVIDERS})
            return _error_json(
                ErrorCode.INVALID_PROVIDER,
                f"未知的检索源: {', '.join(unknown)}。支持的源: arxiv, openalex, crossref, semantic_scholar。",
                recoverable=True, status_code=400,
            )
        body.providers = normalized

    # 构造 agent：捕获 _make_provider 抛出的 ValueError 等构造期错误，
    # 转成结构化 invalid_provider 错误（防御性兜底，正常路径已被上面的校验拦下）。
    try:
        agent = _build_agent_for_thread(thread, body, api_key)
    except ValueError as e:
        return _error_json(
            ErrorCode.INVALID_PROVIDER,
            str(e) or "无效的检索源配置。",
            recoverable=True, status_code=400,
        )

    handle = thread_manager.start_task(thread.id)
    agent.cancel_event = handle.cancel_event

    out_queue: "queue.Queue[Optional[AgentEventEnvelope]]" = queue.Queue()
    worker = threading.Thread(
        target=_run_agent_worker,
        args=(thread, agent, query, out_queue, handle),
        daemon=True,
    )
    handle.worker = worker
    thread.status = "running"
    thread.save()
    worker.start()

    async def ndjson_generator():
        try:
            # 标记运行开始
            yield AgentEventEnvelope(type="intent", message="Agent 已启动…").model_dump_json() + "\n"
            while True:
                item = out_queue.get()
                if item is None:
                    break
                yield item.model_dump_json() + "\n"
        finally:
            # 客户端断开：请求取消后台任务（防止后台空跑）
            thread_manager.request_cancel(thread.id)

    return StreamingResponse(ndjson_generator(), media_type="application/x-ndjson")


@app.post("/api/threads/{thread_id}/cancel")
def api_thread_cancel(thread_id: str):
    if thread_manager.get(thread_id) is None:
        return _error_json(ErrorCode.NOT_FOUND, f"线程 {thread_id} 不存在",
                           recoverable=False, status_code=404)
    ok = thread_manager.request_cancel(thread_id)
    return CancelResponse(status="cancel requested" if ok else "no running task")


@app.get("/api/threads/{thread_id}/papers")
def api_thread_papers(thread_id: str):
    thread = thread_manager.get(thread_id)
    if thread is None:
        return _error_json(ErrorCode.NOT_FOUND, f"线程 {thread_id} 不存在",
                           recoverable=False, status_code=404)
    return {"ok": True, "papers": [Paper.from_dict(p).model_dump() for p in thread.papers]}


@app.get("/api/threads/{thread_id}/report")
def api_thread_report(thread_id: str):
    thread = thread_manager.get(thread_id)
    if thread is None:
        return _error_json(ErrorCode.NOT_FOUND, f"线程 {thread_id} 不存在",
                           recoverable=False, status_code=404)
    return {"ok": True, "report": thread.memory.final_report}


# ===================== 配置健康检查 =====================

def _ping_llm(api_key: str, base_url: str, model: str) -> tuple[bool, str]:
    """发一次最小 ping 验证模型可达。超时 5s。"""
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=5.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
            stream=False,
        )
        # 能拿到 choices 即视为可达
        _ = resp.choices
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def _ping_provider(name: str, settings: dict) -> ProviderHealth:
    """轻量探测检索源可达性。超时 3s。只做连通性，不解析结果。"""
    name = (name or "").strip().lower()
    try:
        if name == "arxiv":
            url = "https://export.arxiv.org/api/query?search_query=all:test&max_results=1"
            r = requests.get(url, timeout=3.0)
            ok = r.status_code == 200
            return ProviderHealth(name=name, ok=ok, detail=f"HTTP {r.status_code}")
        if name == "openalex":
            mailto = settings.get("openalex_mailto", "")
            ep = "https://api.openalex.org/works?per-page=1"
            if mailto:
                ep += f"&mailto={mailto}"
            r = requests.get(ep, timeout=3.0)
            return ProviderHealth(name=name, ok=r.status_code == 200, detail=f"HTTP {r.status_code}")
        if name == "crossref":
            mailto = settings.get("crossref_mailto", "")
            ep = "https://api.crossref.org/works?rows=1"
            if mailto:
                ep += f"?mailto={mailto}"
            r = requests.get(ep, timeout=3.0)
            return ProviderHealth(name=name, ok=r.status_code == 200, detail=f"HTTP {r.status_code}")
        if name == "semantic_scholar":
            r = requests.get("https://api.semanticscholar.org/graph/v1/paper/search?query=test&limit=1",
                             timeout=3.0)
            return ProviderHealth(name=name, ok=r.status_code in (200, 404),
                                  detail=f"HTTP {r.status_code}")
        return ProviderHealth(name=name, ok=False, detail="未知检索源")
    except Exception as e:  # noqa: BLE001
        return ProviderHealth(name=name, ok=False, detail=str(e)[:150])


@app.post("/api/config/health")
def api_config_health(body: ConfigHealthRequest):
    # 解析 key 来源
    req_key = (body.api_key or "").strip()
    env_key = config.DEEPSEEK_API_KEY
    if req_key:
        api_key = req_key
        source = "request"
    elif env_key:
        api_key = env_key
        source = "env"
    else:
        api_key = ""
        source = "none"

    endpoint = body.base_url or config.DEEPSEEK_BASE_URL
    model = body.model or config.DEEPSEEK_MODEL
    provider = body.provider or "deepseek"

    # 检索源健康（并行太重，这里顺序 3s 超时即可）
    settings = {
        "openalex_mailto": body.openalex_mailto or "",
        "crossref_mailto": body.crossref_mailto or "",
        "semantic_scholar_api_key": body.semantic_scholar_api_key or "",
    }
    provider_names = body.providers or config.SEARCH_PROVIDERS
    provider_health = [_ping_provider(n, settings) for n in provider_names if str(n).strip()]

    llm_reachable: Optional[bool] = None
    llm_detail = ""
    if body.ping_llm:
        if not api_key:
            llm_reachable = False
            llm_detail = "无 API Key"
        else:
            llm_reachable, llm_detail = _ping_llm(api_key, endpoint, model)

    overall_ok = bool(api_key) and (llm_reachable in (None, True))
    return ConfigHealth(
        ok=overall_ok,
        api_key_configured=bool(api_key),
        api_key_source=source,
        provider=provider,
        endpoint=endpoint,
        model=model,
        data_dir=config.DATA_DIR,
        llm_reachable=llm_reachable,
        llm_detail=llm_detail,
        providers=provider_health,
    )


@app.get("/api/config/health")
def api_config_health_get():
    """GET 版本：用进程默认配置，不 ping LLM。供前端轻量探测。"""
    return ConfigHealth(
        ok=bool(config.DEEPSEEK_API_KEY),
        api_key_configured=bool(config.DEEPSEEK_API_KEY),
        api_key_source="env" if config.DEEPSEEK_API_KEY else "none",
        provider="deepseek",
        endpoint=config.DEEPSEEK_BASE_URL,
        model=config.DEEPSEEK_MODEL,
        data_dir=config.DATA_DIR,
        llm_reachable=None,
        providers=[],
    )


# ===================== 线程导出 =====================

@app.post("/api/threads/{thread_id}/export")
def api_thread_export(thread_id: str, body: ExportRequest):
    thread = thread_manager.get(thread_id)
    if thread is None:
        return _error_json(ErrorCode.NOT_FOUND, f"线程 {thread_id} 不存在",
                           recoverable=False, status_code=404)
    memory = thread.memory
    export_type = body.type

    # 空数据判定
    if export_type == "chat" and not memory.conversation:
        return _error_json(ErrorCode.EXPORT_EMPTY, "暂无对话记录可导出",
                           recoverable=True, status_code=400)
    if export_type in ("md", "csv", "json") and not memory.search_rounds:
        return _error_json(ErrorCode.EXPORT_EMPTY, "暂无检索结果可导出",
                           recoverable=True, status_code=400)
    if export_type == "report" and not (memory.final_report or "").strip():
        return _error_json(ErrorCode.EXPORT_EMPTY, "暂无报告可导出",
                           recoverable=True, status_code=400)

    try:
        if export_type == "chat":
            content = exporter.export_conversation_md(memory)
            fname = f"完整对话记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        elif export_type == "md":
            content = exporter.export_search_results_md(memory)
            fname = f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        elif export_type == "csv":
            content = exporter.export_search_results_csv(memory)
            fname = f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        elif export_type == "json":
            content = exporter.export_search_results_json(memory)
            fname = f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        else:  # report
            content = exporter.export_final_report(memory)
            fname = f"最终报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    except Exception as e:  # noqa: BLE001
        return _error_json(ErrorCode.INTERNAL, f"导出出错: {e}", recoverable=False,
                           status_code=500)

    filepath = exporter.save_export(content, fname)
    return ExportResponse(filename=os.path.basename(filepath),
                          status=f"✅ 已导出: {os.path.basename(filepath)}")


# ===================== 下载 =====================

@app.get("/api/download")
def api_download(file: str):
    filename = os.path.basename(file)
    safe_path = os.path.join(config.EXPORT_DIR, filename)
    if os.path.exists(safe_path):
        return FileResponse(safe_path, filename=filename)
    return _error_json(ErrorCode.NOT_FOUND, "文件不存在", recoverable=False, status_code=404)


# ===================== 旧契约兼容（仅 index.html，已 deprecated） =====================

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
    host = os.environ.get("ARXIV_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("ARXIV_AGENT_PORT", "7860"))
    uvicorn.run(app, host=host, port=port)
