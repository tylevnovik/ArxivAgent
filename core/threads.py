"""
线程持久化 + 运行期任务索引。

每个线程 = 一份 JSON 文件 (DATA_DIR/threads/<id>.json)，存：
  元信息 (id/title/status/created_at/updated_at/last_error) + 完整 Memory 序列化。

运行期 ThreadManager 还维护一个内存索引 tasks：
  thread_id -> TaskHandle(cancel_event, worker_thread, status)
供 /api/threads/{id}/cancel 找到正在跑的任务并请求取消。

写入用临时文件 + os.replace 原子落盘，避免流式中途崩溃损坏 JSON。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from typing import Optional

import config
from core.memory import Memory


def _now_iso() -> str:
    return datetime.now().isoformat()


def _new_thread_id() -> str:
    # 短而唯一的线程 id（前端列表 key 与文件名）
    return datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4)


class Thread:
    """一个会话线程：元信息 + Memory。"""

    def __init__(
        self,
        thread_id: str,
        title: str = "",
        status: str = "idle",
        created_at: str = "",
        updated_at: str = "",
        memory: Optional[Memory] = None,
        last_error: Optional[str] = None,
    ):
        self.id = thread_id
        self.title = title or "新对话"
        self.status = status
        self.created_at = created_at or _now_iso()
        self.updated_at = updated_at or self.created_at
        self.memory = memory if memory is not None else Memory()
        self.last_error = last_error

    # ---------- 序列化 ----------

    def serialize(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
            "memory": self.memory.serialize(),
        }

    @classmethod
    def deserialize(cls, data: dict) -> "Thread":
        return cls(
            thread_id=str(data.get("id", "") or _new_thread_id()),
            title=str(data.get("title", "") or "新对话"),
            status=str(data.get("status", "idle") or "idle"),
            created_at=str(data.get("created_at", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
            memory=Memory.deserialize(data.get("memory", {}) or {}),
            last_error=data.get("last_error"),
        )

    # ---------- 派生字段 ----------

    @property
    def papers(self) -> list[dict]:
        return self.memory.get_all_relevant_papers()

    @property
    def has_report(self) -> bool:
        return bool(self.memory.final_report.strip())

    @property
    def message_count(self) -> int:
        return len(self.memory.conversation)

    def meta_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "papers_count": len(self.papers),
            "has_report": self.has_report,
            "message_count": self.message_count,
            "last_error": self.last_error,
        }

    def detail_dict(self) -> dict:
        return {
            **self.meta_dict(),
            "messages": [
                {
                    "id": str(m.get("id") or f"{self.id}:{i}"),
                    "persisted_index": i,
                    "role": m.get("role", "assistant"),
                    "content": m.get("content", ""),
                    "timestamp": m.get("timestamp", ""),
                    "kind": "text",
                }
                for i, m in enumerate(self.memory.conversation)
            ],
            "papers": self.papers,
            "report": self.memory.final_report,
            "evidence": self.memory.evidence_chunks,
        }

    # ---------- 持久化 ----------

    def _path(self) -> str:
        return os.path.join(config.THREADS_DIR, f"{self.id}.json")

    def save(self) -> str:
        """原子写入：先写临时文件再 os.replace。返回最终路径。"""
        self.updated_at = _now_iso()
        path = self._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.serialize(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path

    def delete(self) -> bool:
        path = self._path()
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False


# ===================== 任务运行期索引 =====================

class TaskHandle:
    """一个正在运行（或刚结束）的检索任务的句柄。"""

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        # 标记 worker 是否已自然结束（无论成功/失败/取消）
        self.finished = threading.Event()


class ThreadManager:
    """
    线程存储 + 运行期任务索引的统一入口。

    线程列表/CRUD 直接读写磁盘 JSON；任务取消通过内存 tasks 索引。
    """

    def __init__(self):
        self._tasks: dict[str, TaskHandle] = {}
        self._lock = threading.Lock()

    # ---------- 列表 / CRUD ----------

    def list_threads(self) -> list[Thread]:
        entries: list[Thread] = []
        if not os.path.isdir(config.THREADS_DIR):
            return entries
        for name in os.listdir(config.THREADS_DIR):
            if not name.endswith(".json"):
                continue
            path = os.path.join(config.THREADS_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries.append(Thread.deserialize(data))
            except (OSError, json.JSONDecodeError):
                # 跳过损坏文件，不阻塞列表
                continue
        entries.sort(key=lambda t: t.updated_at, reverse=True)
        return entries

    def get(self, thread_id: str) -> Optional[Thread]:
        path = os.path.join(config.THREADS_DIR, f"{thread_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return Thread.deserialize(json.load(f))
        except (OSError, json.JSONDecodeError):
            return None

    def create(self, title: Optional[str] = None) -> Thread:
        thread = Thread(thread_id=_new_thread_id(), title=title or "新对话")
        thread.save()
        return thread

    def rename(self, thread_id: str, title: str) -> Optional[Thread]:
        thread = self.get(thread_id)
        if thread is None:
            return None
        thread.title = title
        thread.save()
        return thread

    def delete(self, thread_id: str) -> bool:
        thread = self.get(thread_id)
        if thread is None:
            return False
        return thread.delete()

    # ---------- 任务索引 ----------

    def start_task(self, thread_id: str) -> TaskHandle:
        """登记一个新任务，返回其 handle（含 cancel_event）。"""
        with self._lock:
            # 同一线程已有任务在跑：先取消旧的（防止并发写同一线程）
            old = self._tasks.get(thread_id)
            if old and not old.finished.is_set():
                old.cancel_event.set()
            handle = TaskHandle(thread_id)
            self._tasks[thread_id] = handle
            return handle

    def get_task(self, thread_id: str) -> Optional[TaskHandle]:
        with self._lock:
            return self._tasks.get(thread_id)

    def finish_task(self, thread_id: str) -> None:
        with self._lock:
            handle = self._tasks.get(thread_id)
            if handle:
                handle.finished.set()

    def request_cancel(self, thread_id: str) -> bool:
        """请求取消某线程当前任务。返回是否找到了在跑的任务。"""
        with self._lock:
            handle = self._tasks.get(thread_id)
        if handle is None or handle.finished.is_set():
            return False
        handle.cancel_event.set()
        return True


# 进程级单例：app.py 直接 import 使用
thread_manager = ThreadManager()
