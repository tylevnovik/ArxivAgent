"""
多源论文检索 Agent - FastAPI Service
线程持久化 + 结构化事件流 (AgentEventEnvelope)，配合桌面端 desktop/ 使用。
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
from fastapi.responses import StreamingResponse, JSONResponse
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

# ===================== FastAPI Web App Setup =====================

app = FastAPI(title=f"多源论文检索 Agent v{config.APP_VERSION}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # 本地后端不走 cookie/凭据，allow_credentials=False 与 allow_origins=["*"]
    # 是浏览器规范允许的唯一组合（带 credentials 的通配源会被规范禁止）。
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        "pyproject_file": os.path.join(backend_dir, "pyproject.toml"),
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
        except Exception as save_err:
            # 持久化失败要让用户知道，而不是静默吞掉（否则 UI 显示成功但下次刷新数据丢了）
            import traceback as _tb
            print(f"[ERROR] Thread {thread.id} 持久化失败: {save_err}\n{_tb.format_exc()}",
                  flush=True)
            out_queue.put(AgentEventEnvelope(
                type="error",
                message=f"⚠️ 检索已完成，但结果未能保存到磁盘：{save_err}。可重试或导出当前结果。",
            ))
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


# ===================== 入口 =====================
if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("ARXIV_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("ARXIV_AGENT_PORT", "7860"))
    uvicorn.run(app, host=host, port=port)
