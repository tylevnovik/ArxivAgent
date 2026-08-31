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
import re
import secrets
import threading
import tempfile
from datetime import datetime
from typing import Optional

import config
from core.memory import Memory


THREAD_SCHEMA_VERSION = 2
CORRUPT_THREADS_DIR = ".corrupt"


def _now_iso() -> str:
    return datetime.now().isoformat()


def _new_thread_id() -> str:
    # 短而唯一的线程 id（前端列表 key 与文件名）
    return datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4)


# thread_id 只允许字母、数字、下划线、连字符，杜绝路径穿越（../、/、%2e 等）。
# _new_thread_id 生成的 id 天然符合，这里是对外部输入（URL 路径参数）的防御。
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_valid_thread_id(thread_id: str) -> bool:
    """校验 thread_id 是否只含安全字符，避免被拼进文件路径时穿越目录。"""
    return bool(thread_id) and bool(_THREAD_ID_RE.match(thread_id))


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
            "schema_version": THREAD_SCHEMA_VERSION,
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
        if not isinstance(data, dict):
            raise ValueError("线程文件根节点必须是 JSON 对象")
        try:
            version = int(data.get("schema_version", 1) or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("线程 schema_version 无效") from exc
        if version > THREAD_SCHEMA_VERSION:
            raise ValueError(
                f"线程 schema_version={version} 高于当前版本 {THREAD_SCHEMA_VERSION}"
            )

        # v1 没有 schema_version，且少数早期快照把历史称为 history；
        # 迁移只补结构，不改变用户内容。
        normalized = dict(data)
        memory = dict(normalized.get("memory", {}) or {})
        if "conversation" not in memory and "history" in memory:
            memory["conversation"] = list(memory.get("history", []) or [])
        normalized["memory"] = memory
        thread = cls(
            thread_id=str(normalized.get("id", "") or _new_thread_id()),
            title=str(normalized.get("title", "") or "新对话"),
            status=str(normalized.get("status", "idle") or "idle"),
            created_at=str(normalized.get("created_at", "") or ""),
            updated_at=str(normalized.get("updated_at", "") or ""),
            memory=Memory.deserialize(memory),
            last_error=normalized.get("last_error"),
        )
        thread._loaded_schema_version = version
        return thread

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
            "citation_check": self.memory.citation_check,
        }

    # ---------- 持久化 ----------

    def _path(self) -> str:
        return os.path.join(config.THREADS_DIR, f"{self.id}.json")

    def save(self) -> str:
        """原子写入：先写临时文件再 os.replace。返回最终路径。"""
        self.updated_at = _now_iso()
        path = self._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.id}.", suffix=".tmp", dir=os.path.dirname(path)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.serialize(), f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
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


class ThreadDeletedError(RuntimeError):
    """任务启动时线程已被删除。"""


class ThreadBusyError(RuntimeError):
    """线程已有一个活跃任务，不能启动第二个任务或修改标题。"""


class ThreadManager:
    """
    线程存储 + 运行期任务索引的统一入口。

    线程列表/CRUD 直接读写磁盘 JSON；任务取消通过内存 tasks 索引。
    """

    def __init__(self):
        self._tasks: dict[str, TaskHandle] = {}
        # 删除中的线程 tombstone：worker 收尾期间仍可能持有旧 Thread 对象，
        # tombstone 防止它把已删除的 JSON 重新写回磁盘。
        self._deleted_ids: set[str] = set()
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
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                # 隔离损坏/未来版本文件，避免每次刷新都重复报错；不阻塞列表。
                self._quarantine_corrupt(path, str(exc))
        entries.sort(key=lambda t: t.updated_at, reverse=True)
        return entries

    def get(self, thread_id: str) -> Optional[Thread]:
        # 防御路径穿越：thread_id 来自 URL 路径参数，校验后才拼路径
        if not _is_valid_thread_id(thread_id):
            return None
        path = os.path.join(config.THREADS_DIR, f"{thread_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return Thread.deserialize(json.load(f))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def _quarantine_corrupt(self, path: str, reason: str = "") -> None:
        """把不可读线程快照移到 .corrupt，原文件永远不作为有效线程加载。"""
        if not os.path.exists(path):
            return
        try:
            quarantine_dir = os.path.join(config.THREADS_DIR, CORRUPT_THREADS_DIR)
            os.makedirs(quarantine_dir, exist_ok=True)
            name = os.path.basename(path)
            stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            destination = os.path.join(quarantine_dir, f"{name}.{stamp}.bad")
            os.replace(path, destination)
            if reason:
                with open(destination + ".reason", "w", encoding="utf-8") as f:
                    f.write(reason[:500])
        except OSError:
            # 隔离失败不应阻塞用户读取其他线程。
            pass

    def recover_interrupted(self) -> dict[str, int]:
        """启动时迁移旧快照，并把上次进程残留的 running 标为 interrupted。"""
        stats = {"interrupted": 0, "migrated": 0, "quarantined": 0}
        if not os.path.isdir(config.THREADS_DIR):
            return stats
        with self._lock:
            for name in os.listdir(config.THREADS_DIR):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(config.THREADS_DIR, name)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    thread = Thread.deserialize(raw)
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    self._quarantine_corrupt(path, str(exc))
                    stats["quarantined"] += 1
                    continue

                changed = int(raw.get("schema_version", 1) or 1) != THREAD_SCHEMA_VERSION
                if thread.status == "running":
                    thread.status = "interrupted"
                    thread.last_error = (
                        "后端在该任务运行期间退出，任务未完成；请重新发起检索。"
                    )
                    stats["interrupted"] += 1
                    changed = True
                if changed:
                    thread.save()
                    stats["migrated"] += 1
        return stats

    def create(self, title: Optional[str] = None) -> Thread:
        thread = Thread(thread_id=_new_thread_id(), title=title or "新对话")
        thread.save()
        return thread

    def rename(self, thread_id: str, title: str) -> Optional[Thread]:
        if not _is_valid_thread_id(thread_id):
            return None
        path = os.path.join(config.THREADS_DIR, f"{thread_id}.json")
        with self._lock:
            if thread_id in self._deleted_ids or not os.path.exists(path):
                return None
            handle = self._tasks.get(thread_id)
            if handle is not None and not handle.finished.is_set():
                raise ThreadBusyError(f"线程 {thread_id} 正在运行")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    thread = Thread.deserialize(json.load(f))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return None
            thread.title = title
            thread.save()
            return thread

    def delete(self, thread_id: str) -> bool:
        if not _is_valid_thread_id(thread_id):
            return False
        path = os.path.join(config.THREADS_DIR, f"{thread_id}.json")
        with self._lock:
            if not os.path.exists(path):
                return False
            self._deleted_ids.add(thread_id)
            handle = self._tasks.get(thread_id)
            if handle is not None and not handle.finished.is_set():
                handle.cancel_event.set()
            try:
                os.remove(path)
                return True
            except OSError:
                # 保留 tombstone；若 worker 稍后收尾，仍不得重新写回。
                return False

    def persist(self, thread: Thread, handle: Optional[TaskHandle] = None) -> bool:
        """仅在线程仍有效时持久化；检查与写入在同一锁内完成。"""
        with self._lock:
            if thread.id in self._deleted_ids:
                return False
            if handle is not None and self._tasks.get(thread.id) is not handle:
                return False
            thread.save()
            return True

    # ---------- 任务索引 ----------

    def start_task(self, thread_id: str) -> TaskHandle:
        """登记一个新任务，返回其 handle（含 cancel_event）。"""
        with self._lock:
            if thread_id in self._deleted_ids:
                raise ThreadDeletedError(f"线程 {thread_id} 已删除")
            # 同一线程已有任务在跑：先取消旧的（防止并发写同一线程）
            old = self._tasks.get(thread_id)
            if old and not old.finished.is_set():
                raise ThreadBusyError(f"线程 {thread_id} 已有任务在运行")
            handle = TaskHandle(thread_id)
            self._tasks[thread_id] = handle
            return handle

    def get_task(self, thread_id: str) -> Optional[TaskHandle]:
        with self._lock:
            return self._tasks.get(thread_id)

    def finish_task(self, thread_id: str, handle: Optional[TaskHandle] = None) -> None:
        with self._lock:
            current = self._tasks.get(thread_id)
            if current is None or (handle is not None and current is not handle):
                return
            current.finished.set()
            self._tasks.pop(thread_id, None)
            self._deleted_ids.discard(thread_id)

    def request_cancel(self, thread_id: str) -> bool:
        """请求取消某线程当前任务。返回是否找到了在跑的任务。"""
        with self._lock:
            handle = self._tasks.get(thread_id)
        if handle is None or handle.finished.is_set():
            return False
        handle.cancel_event.set()
        return True

    def cancel_all(self) -> None:
        """取消所有正在运行的任务（优雅关闭时调用）。"""
        with self._lock:
            handles = list(self._tasks.values())
        for handle in handles:
            if not handle.finished.is_set():
                handle.cancel_event.set()


# 进程级单例：app.py 直接 import 使用
thread_manager = ThreadManager()
