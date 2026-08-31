# ArxivAgent Complete Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成审计中剩余的并发、生命周期、缓存、检索质量、证据可信度和发布测试加固。

**Architecture:** 保持 FastAPI + 后台 worker thread + Electron/React 的现有边界。HTTP 流只在事件循环中异步等待线程队列；ThreadManager 负责任务句柄、原子持久化、schema 迁移和崩溃恢复；SearchService/PDF parser 负责自己的按 key 并发控制和安全缓存。证据在进入 API 前统一补齐来源、页码/章节和可验证引用状态。

**Tech Stack:** Python 3.12, FastAPI, pytest, pypdf, Electron, React, TypeScript, Vitest, Playwright, GitHub Actions。

---

### Task 1: 修复流式 HTTP 与前端任务竞态

**Files:** `app.py`, `desktop/src/mainview/App.tsx`, `tests/backend/`, `desktop/tests/`

- 用 `asyncio.to_thread(queue.get)` 替代事件循环中的阻塞 `queue.get()`。
- 为前端每次检索创建 runId 和运行句柄；旧任务的 callback/catch/finally 不得清理新任务状态。
- 新增取消、切换线程和旧任务延迟收尾测试。

### Task 2: 线程 schema、崩溃恢复和重命名竞态

**Files:** `core/threads.py`, `core/contracts.py`, `app.py`, `tests/backend/`

- 引入当前 schema version、旧格式迁移和损坏 JSON 隔离目录。
- 启动扫描把残留 `running` 标记为 `interrupted`，保留可诊断错误。
- rename 与 start_task 在同一锁内判定，运行中返回 409 `thread_busy`。

### Task 3: 缓存/PDF 并发安全

**Files:** `core/search_service.py`, `core/pdf_parser.py`, `config.py`, `tests/backend/`

- 使用按 key 锁、唯一临时文件和 `os.replace`，避免并行写入半成品。
- 增加搜索缓存总容量/文件数清理和损坏缓存隔离。
- PDF 下载按论文 key 加锁，并保留页级文本元数据。

### Task 4: 多源合并和证据校验

**Files:** `core/search_service.py`, `core/arxiv_search.py`, `core/rag.py`, `core/agent.py`, `core/contracts.py`, `desktop/src/mainview/`

- 按 DOI、arXiv ID、来源 ID、规范化标题建立 canonical record；后到记录只补充更完整字段，不再简单先到先得。
- 事件中返回每个 provider 的成功/失败状态。
- evidence 增加 page/section/source 字段；报告生成后校验正文引用是否命中证据，未命中引用显式标记。

### Task 5: UI、CI 和打包验证

**Files:** `desktop/tests/e2e/app.spec.ts`, `desktop/tests/mock-backend/server.js`, `.github/workflows/ci.yml`, `README.md`

- 增加取消、切换线程、重命名/删除、导出、配置失败恢复、无 Key 场景。
- CI 增加 Electron smoke 和 packaged smoke 所需构建步骤。
- 运行后端/前端全量测试、构建、E2E、smoke，并记录未能在当前环境验证的边界。
