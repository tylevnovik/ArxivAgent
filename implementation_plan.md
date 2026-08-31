# ArxivAgent 本地改进计划 (v0.4)

## 产品方向

ArxivAgent 是一个论文检索研究工作台：左侧真线程列表，中间 assistant-ui 风格聊天壳，
右侧结构化研究资料（文献 + 报告 + 引用证据）。前后端通过稳定的产品契约交互；
打包版在前置依赖检查失败时给出 uv 恢复向导，不再静默打开坏窗口。

## 设计原则

1. **契约优先**：前后端唯一接触面是 `core/contracts.py`（Pydantic）和前端 `api.ts`。
2. **真线程**：每个会话是后端磁盘 JSON，可重命名/删除/恢复。
3. **桌面数据隔离**：导出/缓存/PDF/Qdrant/threads 写到 Electron userData。
4. **安全存储**：API Key 走 Electron safeStorage。
5. **证据可追溯**：报告引用回溯到具体论文的正文切片。
6. **闭环验证**：pytest + vitest + 集成测试 + Electron smoke + Playwright E2E。

## v0.4 已落地并验证

### 报告引用证据（结构化 evidence + 前端可点击展开）
- `core/contracts.py`：新增 `EvidenceChunk` 模型；`ThreadDetail` 增加 `evidence` 字段。
- `core/memory.py`：`Memory.evidence_chunks` 字段，纳入无损 `serialize()`/`deserialize()`。
- `core/agent.py` `_step_report`：把命中的 RAG chunks 保存到 memory（不再丢弃），
  DONE 事件携带 `evidence` 键。
- `app.py`：DONE(search) envelope 映射 evidence；线程详情输出 evidence。
- 前端 `citations-core.ts`（纯函数解析，可单测）+ `citations.tsx`（CitationChip/EvidencePopover）+
  `markdown.ts`（把 【正文…】 标记转成可点击徽标）。
- 报告下方 EvidenceList 展示被引用的正文切片（标题/分块/来源/分数/原文）。
- 测试：后端 done 事件 evidence 断言 + 前端解析/match/split 单测。

### 打包版前置依赖检查 + uv 引导
- 新增 `pyproject.toml`（依赖从 requirements.txt 迁移，[tool.uv] 配置）+
  `.python-version`（3.12）。引导统一走 `uv sync`。
- 后端 `GET /api/system/deps`：探测 Python 版本 + 缺失模块 + 返回 uv 命令模板。
- `main.cjs` 重构：
  - `findPythonPath` 返回 `{path, source}`，**删除 cwd 向上找 .venv**（dev smoke 假通过的根源）；
    仅 dev 模式保留 app.getAppPath 祖先探测。
  - `checkPythonDeps(pythonPath)`：spawn 快速 import 探测，返回缺失模块列表。
  - `diagnoseBackend()`：结构化诊断（health/python/missing/error）。
  - IPC `backend:diagnose` / `backend:retry`。
- 前端 `SetupWizard.tsx`：后端不健康时全屏向导，展示诊断 + uv 命令（可复制）+ 重试。
- 测试：后端 deps 探测（齐全/缺失两路）。

### 第一屏健康状态条 + Markdown 安全加固
- `SystemStatusBar`：空状态底部紧凑状态条——后端就绪/未连接、API Key 是否配置、
  当前模型；无 Key 时直接给"去配置"按钮。
- `markdown.ts` 集中 DOMPurify 加固：禁用 script/iframe/form/on* 事件属性，
  限制 href 仅 http/https/mailto/data:image（防 javascript:）。
- 测试：markdown 安全单测（XSS 各路径）。

### 测试闭环（当前）
- **后端 pytest** (200 collected)：health / 错误协议 / 线程 CRUD+持久化 / running 崩溃恢复与 schema 迁移 /
  检索事件序列 / 结构化 papers / **evidence 链路** / 取消令牌 / 导出 / **deps 探测** /
  原子缓存与 PDF 下载 / 多源合并与 provider 部分失败状态。
- **前端 vitest** (52 passed)：eventReducer / secrets / provider override / **citation 解析+match+split** /
  **markdown 安全** / **mock 后端集成闭环** / keyless provider / config failure recovery /
  rename/export/delete。

## 最新验证状态（2026-08-31）

- `uv run pytest tests/backend -q` ✅ 200 passed，1 个既有 Starlette 弃用警告。
- `bun run typecheck` ✅
- `bun run test:unit` ✅ 52 passed
- `bun run build` ✅
- `bun run test:e2e` ✅ 3 passed（检索完成 / 取消 / 切换线程并重命名删除）
- `bun run smoke` ✅ backendReady / authOk / rendererReady 均为 true，exit 0。
- `bun run pack` ✅ 从当前源码重建 Windows unpacked 产物。
- `bun run smoke:packaged` ✅ backendReady / authOk / rendererReady 均为 true，exit 0；
  结束后未发现 7860 残留监听或打包后端进程。
- `node --check src/electron/main.cjs` 与 `node --check scripts/smoke-packaged.cjs` ✅
- `git diff --check` ✅

## 已知限制 / 后续

- 取消令牌不能中断已在飞的 HTTP 请求，只能在边界退出（同步 + requests 固有限制）。
- safeStorage 在无 keyring 的 Linux 不可用 → 明文回退 + UI 标注。
- evidence chunk 文本截断到 500 字展示，避免单条事件过大。
- 页码/章节元数据依赖 PDF 文本层和保守标题识别；扫描版 PDF 仍可能缺少可靠信息。
- 引用校验能确认标题+分块号是否命中已保存 evidence，但不能替代人工核对论断的语义真实性。

## 发布前固定流程

```bash
# 后端
pytest tests/backend -q

# 前端
cd desktop
bun run typecheck
bun run build
bun run test:unit          # vitest 单测 + 集成测试（含 mock 后端）
bun run smoke              # Electron 启动后端 + renderer（需正常 Electron 安装）
bun run test:e2e           # Playwright E2E（需正常 Electron 安装）
bun run build:canary       # electron-builder --win nsis
bun run smoke:packaged     # 打包版 smoke（带超时、退出码与进程树清理）
```

## 文件结构（v0.4 新增/改动）

```
core/
  contracts.py      [改] EvidenceChunk + ThreadDetail.evidence
  memory.py         [改] evidence_chunks + set_final_results(evidence)
  agent.py          [改] _step_report 保存 evidence；DONE 携带 evidence
  threads.py        [改] detail_dict 输出 evidence
app.py              [改] DONE envelope evidence + GET /api/system/deps
pyproject.toml      [新] uv 项目配置
.python-version     [新] 3.12
desktop/
  src/mainview/
    api.ts          [改] EvidenceChunk 类型 + ThreadDetail.evidence
    eventReducer.ts [改] done 写入 evidence
    markdown.ts     [新] 集中 sanitize 加固 + 引用标记转 span
    citations-core.ts [新] 纯函数解析（可单测）
    citations.tsx   [新] CitationChip / EvidencePopover
    SetupWizard.tsx [新] 打包版环境向导
    App.tsx         [改] SetupWizard + SystemStatusBar + evidence 渲染
    global.d.ts     [改] backend 命名空间类型
  src/electron/
    main.cjs        [改] 依赖检查 + 诊断 IPC + 删 cwd venv 探测
    preload.cjs     [改] backend 命名空间
tests/backend/      [改] test_search_events evidence + test_deps
desktop/tests/mock-backend/server.js [改] done 携带 evidence
desktop/src/mainview/{citations,markdown,eventReducer}.test.ts [新/改]
```

## 最新稳定性与发布加固（2026-08-29）

- `core/threads.py` / `app.py`：删除运行中线程先登记 tombstone，再取消并删除文件；worker
  只能通过按 handle 校验的 manager 入口持久化，避免收尾阶段复活已删除线程；错误事件和取消
  DONE 事件分别落为 `error` / `cancelled`，任务完成后清理运行期索引。
- `config.py`：`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 真正读取环境变量；`MessageRequest`
  对检索轮次和每轮结果数设置 1-10 / 1-100 的上限。
- `core/search_service.py`：Unicode/CJK token、全角归一化和中文标题 fallback 身份，修复
  无 DOI/arXiv ID 的中文论文被丢弃或无法排序的问题。
- `app.py` / `core/auth.py`：CORS 收紧到本地来源与明确方法/请求头，配置健康探测移出公开白名单。
- `desktop/src/electron/main.cjs`：smoke 等待后端停止，bootstrap 异常明确退出；
  `desktop/scripts/smoke-packaged.cjs` 提供超时、退出码和进程树清理。
- `.github/workflows/ci.yml`：加入后端 pytest/compile、前端 typecheck/unit/build 和 Electron E2E。
- 详细任务分解见 `docs/plans/2026-08-29-stabilize-project.md`。
