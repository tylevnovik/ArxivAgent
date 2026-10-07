# ArxivAgent 审查与改进执行计划

> **执行者：GPT-5.6 Luna。** 使用可用的 executing-plans 技能逐项执行。本文件是审查交接，不是已完成修复清单。不要创建额外任务或委派子代理；不要自动提交或发布。

**Goal:** 修复会话错位、并发覆盖、误取消和正文缓存复用错误，并建立能够覆盖这些边界的发布验收。

**Architecture:** 保留 Python/FastAPI + 磁盘 JSON 会话 + Electron/React 架构。将并发一致性收敛到 ThreadManager，将前端异步结果的归属检查收敛到会话加载入口；保持现有 API 和历史数据兼容，按任务小步修改。

**Tech Stack:** Python 3.12、uv、pytest、FastAPI、React/TypeScript、Bun、Vitest、Playwright/Electron。

---

## 0. 审查结论与证据边界

- 审查日期：2026-09-05；基线提交：`4a76da7`。开始审查时 `git status --short` 为空。
- 当前基础功能和验证体系已经成形，不建议先重构全项目或扩展检索源。
- 本次实跑：后端 **200 passed**（54.29 秒，1 个 Starlette 弃用警告）；前端 **52 passed / 7 files**；TypeScript 检查通过；Vite 生产构建通过。52 项中包含契约集成测试，不能另计一份。
- 现有真实 Electron + mock 后端 E2E **3 passed**（51.0 秒）：完成检索、停止检索、重命名/切换/删除。它们尚未覆盖下列故障注入场景。`git diff --check` 通过；审查结束仅此计划为新增未跟踪文件。
- 构建有空 `react` chunk 提示；没有证据表明这是用户故障，暂不作为优先改进。
- 下述三项隔离脚本已复现底层机制：500 字以后内容变化仍得到同一缓存键；旧消息快照保存把重命名恢复成旧标题；旧任务结束后按会话 ID 取消会设置新 handle 的取消标志。并发 HTTP/真实 UI 触发频率尚未测量。
- 前端请求乱序和 NDJSON 问题来自明确代码路径，尚未运行专门故障注入测试。执行者须先写回归用例证实，再修复。
- 本次未调用真实付费模型或真实检索源，未重新打包/验收安装器；不能声称真实论文研究质量或最终安装包已通过。
- 审查阶段只新增本计划，不修改业务源码。既有计划中的历史“通过”记录不等于新版本通过。

## 1. 优先级与顺序

|任务|级别|问题及后果|证据定位（基线）|
|---|---|---|---|
|T1|P1|旧详情响应覆盖当前会话，可能把后续输入发到错误会话|`desktop/src/mainview/App.tsx:350`、`:579`|
|T2|P1|消息编辑/删除的读、忙检查、写不原子，覆盖标题或运行状态|`app.py:436`、`:464`；`core/threads.py:363`|
|T3|P1|旧流 finally 按会话 ID 取消新任务；初始保存失败可遗留 busy|`app.py:658` 附近至 ndjson_generator；`core/threads.py:401`|
|T4|P1|正文/来源变化复用旧检索器或 Qdrant 集合，证据可能过时|`core/agent.py:157`、`:882`；`core/rag.py:514`、`:577`|
|T5|P2|流末尾事件丢弃、解析异常与回调异常混淆，流提前结束无明确诊断|`desktop/src/mainview/api.ts:385`|
|T6|P2|取消 UI 过早显示可用，又被 running 快照覆盖|`desktop/src/mainview/App.tsx:536`、`:579`、`:604`|
|T7|P2|现有 E2E 是真实 Electron + mock 后端，缺少上述故障及真实后端研究链路验收|`desktop/tests/e2e/app.spec.ts`、`.github/workflows/ci.yml`|

执行顺序：T1 → T2 → T3 → T4 → T5 → T6 → T7。T6 依赖 T1/T3/T5；T7 最后汇总。每项完成后在本文件下追加结果、命令、剩余限制；失败时不得标为完成。

## 2. 执行约束

1. 工作目录 `C:\Users\blmpt\Downloads\ArxivAgent`。先运行 `git status --short`、`git rev-parse HEAD`，核对本计划的函数仍存在。保护此后用户新增修改，不 reset/clean，不覆盖历史计划。
2. 测试使用临时目录和假模型/假 provider，不读写用户 threads、exports、PDF 缓存或密钥。故障注入用 Event/barrier/deferred promise，不用随机 sleep 猜测并发顺序。
3. 每项先新增能在旧代码失败的行为测试，运行并记录失败原因；然后最小修复、运行相关测试。不要通过放松断言、删除用例、增加无条件 retry 得到通过。
4. 不在本轮改数据库、引入新状态库或新框架。不安装全新大依赖来解决这些问题。不自动提交、推送或发布。
5. 以下路径均相对上述工作目录；行号只作基线定位，以函数名为准。

## T1：阻止前端旧请求覆盖当前会话

**修改：** `desktop/src/mainview/App.tsx`；必要时新增 `desktop/src/mainview/useThreadSelection.ts`。
**新增测试：** `desktop/src/mainview/threadSelection.test.tsx`（若抽 hook，直接测试 hook 行为）。

步骤：
1. 用可控 promise 模拟点击 B、再点击 C；先完成 C 请求再完成 B，断言当前标题、会话 ID、消息和发送目标全部仍为 C。
2. 增加“旧 run 收尾刷新开始 → 用户切换 C → 旧刷新返回”用例。当前 finally 仅在 await 前检查 activeIdRef；await 后无归属检查。
3. 建立独立的 selection generation；每次选中意图立即递增，响应和错误返回时都核对 generation。不能只依赖 runSequenceRef，因为没有 run 时也可乱序。
4. 详情加载期间阻止向仍显示的旧会话发消息，或立即清空旧详情并显示加载状态；选择目标、显示内容和可发送目标保持一致。A→B→A 也必须使 B 的在途请求失效，不能被旧 activeThreadId 的提前 return 拦住。
5. run 收尾刷新在 await 后重新核对 selection generation、threadId、新 run 是否启动；新建、删除后的自动选择走同一入口。

验收：上述乱序用例以及 A→B→A、加载失败、组件卸载均不污染其他会话；旧请求的失败不弹当前会话错误。
命令（desktop）：`bunx vitest run src/mainview/threadSelection.test.tsx`；`bun run typecheck`。

## T2：把消息变更和任务启动纳入同一一致性边界

**修改：** `core/threads.py`、`app.py`。
**测试：** 扩展 `tests/backend/test_message_ops.py`、`tests/backend/test_lifecycle.py`。

已复现：`stale = manager.get(id)` → `manager.rename(id, 'renamed')` → `manager.persist(stale)`，最终标题恢复为旧值。现有 persist 的锁只保护写入，不能保护读取到写入之间的业务事务。

步骤：
1. 写 barrier 控制的消息 PATCH 与 rename 交错用例；再写消息 DELETE 与 start_task 交错用例，验证不能保存旧快照覆盖 running 状态。
2. 在 manager 内增加消息变更入口，在同一锁内检查存在性/tombstone/当前任务，读取最新快照，定位消息、更新派生字段并保存。路由不再直接读改写旧 Thread。
3. 把 `_sync_derived_message_fields` 的逻辑移为可复用纯辅助函数或 manager 内方法，保持原有报告/用户查询同步语义。
4. 同时检查任务启动的快照来源：现代码在 start_task 前取得 thread 并构造 agent。为启动增加原子“读取最新快照 + 登记 handle”入口，agent 必须使用登记时的快照；构造失败交由 T3 回滚。
5. 保持现有 404/409/400 错误契约；禁止持锁执行 LLM、PDF、HTTP 等长操作。

验收：并发操作要么按合法顺序成功，要么明确 409/404；已确认的标题/消息不回滚；已有编辑/删除报告用例保持通过。
命令（根目录）：`uv run pytest tests/backend/test_message_ops.py tests/backend/test_lifecycle.py tests/backend/test_threads.py -q`。

## T3：取消绑定任务实例，启动失败必须清理

**修改：** `app.py`、`core/threads.py`。
**测试：** `tests/backend/test_cancel.py`、`tests/backend/test_lifecycle.py`、`tests/backend/test_search_events.py`。

步骤：
1. 建立旧 handle 完成、新 handle 登记、旧 generator finally 执行的确定性用例；断言新 handle 未被取消。可直接构造 route 返回的 StreamingResponse，手动消费/关闭 body_iterator，不依赖网络竞速。
2. 给 `request_cancel` 增加可选预期 handle 参数，在同一锁内核对实例并 set；旧 generator 的 finally 传捕获的 handle。用户显式 cancel API 保留“取消当前任务”的语义。
3. 分别注入初始 persist 抛 OSError、agent 构造失败、worker.start 抛 RuntimeError。登记后至成功启动前使用一致的失败清理路径，调用 handle 校验版 finish_task，并尽力保存明确 error 状态。
4. 返回结构化内部错误，不能把磁盘写入失败伪装成 404。再次尝试启动不能因为旧 handle 永久返回 409。
5. 检查队列消费取消：`asyncio.to_thread(out_queue.get)` 在协程取消后仍可能等待。改用带界限的等待并检查关闭/取消，明确处理断流后生产者停止投递或清理，避免无消费者时无限累积；不要直接增加有界 queue 后让 worker 永久堵在 put。

验收：旧请求不影响新任务；所有启动失败路径释放任务登记；客户端断开不挂住后台队列等待；删除中的 worker 不复活会话。
命令：`uv run pytest tests/backend/test_cancel.py tests/backend/test_lifecycle.py tests/backend/test_search_events.py -q`。

## T4：正文缓存身份覆盖完整内容和配置

**修改：** `core/agent.py`、`core/rag.py`；建议新增 `core/chunk_identity.py` 供两处共享。
**测试：** `tests/backend/test_agent_pure.py`、`tests/backend/test_rag_pure.py`。

已复现：同一论文/分块且前 500 字相同，后文 OLD→NEW，`_retriever_cache_key` 完全相同。`_chunk_id` 也截断到 500 字，修一处不足以避免持久化集合复用。

步骤：
1. 测试相同前 500 字、不同后文必须得到不同内容身份和 collection；仅页码/来源变化时返回 evidence 必须使用新元数据。
2. 使用结构化、确定序列化的字段（不要无转义 `|` 拼接），哈希完整正文，加入身份算法版本。集合身份至少包含完整内容和 embedding 模型标签。
3. 进程缓存键还要包含检索器类型、影响索引构建的配置及证据元数据；从实际 HybridRetriever 配置读取所需字段，排除密钥。结果不能因上次 TF-IDF fallback 被永久固定在旧模式；给失败回退缓存明确失效策略并写测试。
4. `_normalize_chunk` 对传入 chunk_id 的兼容要显式处理：保留外部 ID 可作来源标识，但不能让旧 ID 绕过新内容指纹导致集合复用。
5. 原有磁盘集合可以自然失效重建；不要删除用户正文/PDF/会话。使用 fake embedding/Qdrant 验证，不下载模型。

验收：完整正文变化失效、元数据更新可见、相同输入稳定复用、检索配置变化失效；旧格式数据仍可加载。
命令：`uv run pytest tests/backend/test_agent_pure.py tests/backend/test_rag_pure.py -q`。

## T5：健壮读取 NDJSON 并保留错误

**修改：** `desktop/src/mainview/api.ts`，必要时提取 `ndjson.ts`。
**新增测试：** `desktop/src/mainview/api.stream.test.ts`。

步骤：
1. 用 ReadableStream 构造：中文 UTF-8 跨块、多个事件同块、CRLF、末行无换行、坏 JSON、回调抛错、空流、只有中间事件就 EOF、AbortError。
2. 当前 `if (done) break` 丢弃残留 buffer；结束时 flush decoder，再处理末行。
3. JSON 解析的 try/catch 只包解析，onEvent 错误必须上抛，不能被记录成“NDJSON 解析失败”。坏协议给明确错误，不把完整原始响应写到 console。
4. 验证最小 envelope 形状，跟踪终态。EOF 前没有 done/error/cancelled 应报流提前中断；不要在看到 done 后立刻停止读，因为 backend 可能随后发送持久化失败 error。
5. finally 释放 reader，失败/取消时正确取消读取。普通正常 EOF 不合成虚假 done。

验收：每条完整事件恰好分发一次；尾行不丢；业务回调错误不吞；中断和正常完成可区分。
命令（desktop）：`bunx vitest run src/mainview/api.stream.test.ts tests/integration/contract.spec.ts`。

## T6：让取消状态与后端真实状态一致

**修改：** `desktop/src/mainview/App.tsx`，沿用 T1 的加载归属逻辑。
**新增测试：** `desktop/src/mainview/cancellation.test.tsx`；扩展 `desktop/tests/mock-backend/server.js`。

问题：abort 后 finally 立即解除 isSearching 并刷新详情；上游阻塞时后端仍是 running，可覆盖“已停止”，下一次发送得到 409。现有慢流测试不等价于上游长期不产 token。

步骤：
1. 模拟 cancel 接口已受理、详情连续返回 running、稍后 cancelled，验证界面显示“正在停止”，提交保持禁用，最后才恢复。
2. cancel 请求失败必须可见并提供重试；不要 `.catch(() => {})` 静默丢弃用户停止失败。
3. 用可取消且有超时的短轮询确认终态（建议 500ms 起步、最多 30 秒），每次返回核对会话 generation。超时显示“停止尚未确认”，不声称已经停止；允许重新检查或重试取消。
4. 切换/卸载时结束轮询。没有后端终态证据时不把本地 abort 等同于任务已结束。

验收：取消等待、失败、超时、切换会话均可恢复；不重复追加多条停止消息；新任务不受旧轮询影响。
命令（desktop）：`bunx vitest run src/mainview/cancellation.test.tsx src/mainview/threadSelection.test.tsx`；`bun run typecheck`。

## T7：补充用户路径验收并闭合发布记录

**修改/新增：** `desktop/tests/e2e/app.spec.ts`、`desktop/tests/e2e/backend-flow.spec.ts`、必要的独立测试 harness、`.github/workflows/ci.yml`、`desktop/playwright.config.ts`、`README.md`、`implementation_plan.md`。

步骤：
1. 把 T1/T6 最关键乱序/停止路径加入真实 Electron UI 测试。每个测试用 try/finally 关闭窗口，失败也清理自有子进程。
2. 新增真实 FastAPI + 真实 Electron 链路：在测试 harness 中替换 LLM/provider/PDF 外部边界，保留路由、worker、持久化和认证链。使用全新临时 DATA_DIR，测试从 UI 检索→正文证据→导出→关闭重启→历史恢复。
3. 端口 7860 若被占用，应快速报错并说明占用，不能杀用户后端。不要为了验收开启真实付费模型。
4. Playwright 当前 retries=0 与 trace=on-first-retry 不会提供首次失败 trace，改为 retain-on-failure，并在 CI 失败时上传 trace/测试报告。
5. 在最后一次源码改动后串行运行下方完整流程。CI 目前已有 pack 和 packaged smoke，不要重复新增同名工作。
6. 发布记录写明 commit/工作区差异、平台、每项命令退出码、产物路径和 SHA256。没有执行安装器验收就明确仅验 unpacked。README 的依赖恢复说明应按当前内置 Python 运行时核对，不能继续把外部 uv 当所有用户必备条件。

根目录：
```powershell
uv run pytest tests/backend -q
uv run python -m compileall -q app.py config.py core
git diff --check
```

desktop 目录（逐条执行，某条失败先解决再继续）：
```powershell
bun run test:unit
bun run build
bun run test:e2e
bun run smoke
bun run pack
bun run smoke:packaged
```

验收：真实 Electron 路径、真实本地后端路径、打包后启动分别有证据；最后打包之后才运行 packaged smoke；进程清理检查仅针对本次启动的 PID。

## 3. 后续建议（本轮不执行）

- 完成正确性修复后，再按配置页/会话状态/资料面板拆分超大的 App.tsx；用行为测试守住边界，避免一次性重写。
- 普通 JSON API 增加统一 timeout/AbortSignal 与分类错误；配置健康探测应有独立耗时预算，不能照搬流式请求超时。
- 针对真实中文/英文问题建小型固定检索评测集，记录召回、来源失败、证据覆盖、延迟和成本。此项需另行约定真实模型与调用预算；当前 mock 测试不能证明研究质量。
- 再评估全局并发上限、PDF/RAG 内存预算、缓存容量和检索列表性能；先记录代表性负载指标，再决定是否引入队列或数据库。

## 4. 给 Luna 的交接指令

请阅读本计划及当前代码，从 T1 起按顺序实施，保护既有修改，不跳过旧代码应失败的回归测试。每项完成后更新下方记录。只在全部适用验收通过后称完成；没有实跑的外部调用、安装器或视觉验收明确标注未验证。遇到与计划假设不符的代码，先记录证据并调整最小方案，不按过时行号机械改写。

### 执行记录

待执行。填写格式：任务 ID / 修改文件 / 旧代码失败证据 / 修复后命令与结果 / 剩余限制。

---

#### T1：阻止前端旧请求覆盖当前会话 — 已完成（2026-09-05）

- **修改文件**：新增 `desktop/src/mainview/useThreadSelection.ts`（会话选择归属管理 hook）；修改 `desktop/src/mainview/App.tsx`（接入 hook、切换守卫、run 收尾刷新归属核对）；新增 `desktop/src/mainview/threadSelection.test.tsx`。
- **做法**：将 App.tsx 的 `selectThread`/run 收尾刷新逻辑原样抽为 hook（行为不变）先跑回归测试，随后修复：
  1. 引入独立 selection generation（`generationRef`）：`selectThread` 在发出请求前先递增 generation、同步 `threadIdRef` 并立即清空旧详情（`thread=null`、`loading=true`）；响应/错误返回时经 `applyIfCurrent` 核对（存活 + generation + threadId + 可选 stillValid 谓词）后才落地。
  2. 详情加载期间 `activeThread` 为 null，`handleSendQuery` 的 `!activeThread` 检查阻止向旧会话发送；状态栏显示"正在加载会话..."。
  3. `handleSelectThread` 去重守卫改为 `id === activeThreadId && activeThread`：详情未加载时允许重选，A→B→A 不再被"旧 activeThreadId 提前 return"拦住。
  4. run 收尾刷新改走 `fetchDetailIfCurrent(threadId, gen, () => !activeRunRef.current)`：await 后重新核对 generation、threadId、新 run 是否启动，新 run 的乐观消息不会被旧快照清掉。
  5. 过期请求失败静默丢弃，`onLoadError` 仅在失败请求仍是当前意图时触发（toast 不再弹到别的会话）；卸载后（`aliveRef=false`）不更新状态。
  6. 初始加载、新建、删除后自动选择全部走同一 `selectThread` 入口。
- **旧代码失败证据**（hook 为旧逻辑直移版，`bunx vitest run src/mainview/threadSelection.test.tsx`，6 failed | 2 passed）：
  - 乱序返回 B 后于 C 完成：`expected 'b' to be 'c'`（B 覆盖了 C 的标题/消息/发送目标）；
  - A→B→A：`expected 'b' to be 'a'`；
  - 旧 run 收尾刷新在用户切走后返回：`expected 'b' to be 'c'`；
  - 收尾快照落地清掉新 run 乐观消息：`expected ['B-收尾快照'] to deeply equal ['B-1']`；
  - 加载期间无 loading 状态且旧详情仍显示：`expected false to be true`；
  - 旧请求失败把错误弹到当前会话：`onLoadError` 被以"B 加载失败"调用。
- **修复后命令与结果**：
  - `bunx vitest run src/mainview/threadSelection.test.tsx` → **8 passed**；
  - `bun run typecheck` → 通过；
  - `bun run test:unit` → **8 files / 60 passed**（原 52 项 + 新增 8 项，无回归）。
- **剩余限制**：归属核对针对"选择意图"粒度；run 内事件仍由 `activeRunRef.runId + threadIdRef` 双重守卫（既有机制，行为未变）。加载期间向空态 composer 输入后发送是静默 no-op（本地后端加载通常 <100ms，未加禁用态，属可接受 UX 取舍）。App 级集成（真实点击路径）由 T7 的 Electron E2E 补充覆盖。

---

#### T2：把消息变更和任务启动纳入同一一致性边界 — 已完成（2026-09-05）

- **修改文件**：`core/threads.py`（新增 `sync_derived_message_fields` 模块函数、`ThreadManager.mutate_messages`、`ThreadManager.begin_task`）；`app.py`（PATCH/DELETE 消息路由改走 `mutate_messages`，POST 启动路由改走 `begin_task`，删除 `_message_mutation_busy_error`/`_resolve_message`/`_sync_derived_message_fields`）；扩展 `tests/backend/test_message_ops.py`、`tests/backend/test_lifecycle.py`（新增 7 个用例）。
- **旧代码失败证据**（`uv run pytest tests/backend/test_message_ops.py tests/backend/test_lifecycle.py -q`，7 failed | 6 passed）：
  - 路由级复现（通过 monkeypatch `thread_manager.get` 在路由读取快照后同步注入并发 rename）：`AssertionError: 已确认的 rename 被消息 PATCH 的旧快照回滚: 原始标题`（计划复现路径 `stale → rename → persist(stale)` 的 HTTP 等价物）；
  - 路由级复现（包装 `_message_mutation_busy_error` 放行后注入 `start_task` + running 持久化）：`AssertionError: running 状态被消息 DELETE 的旧快照覆盖: error`；
  - 4 个 manager 级用例 + 2 个 `begin_task` 用例：旧代码无 `mutate_messages`/`begin_task`（AttributeError，能力缺失）。
- **修复要点**：
  1. `mutate_messages(thread_id, mutator)`：存在性/tombstone/忙检查、读取最新快照、执行 mutator、持久化在同一锁内完成；mutator 只做内存操作（无 LLM/PDF/HTTP），异常上抛不落盘；忙时抛 `ThreadBusyError`（路由映射 409，消息与既有契约一致）。
  2. `begin_task(thread_id)`：原子"读取最新快照 + 登记 handle"，agent 必须使用返回的登记时快照；忙抛 `ThreadBusyError`，缺失/损坏抛 `ThreadDeletedError`（404）。`start_task` 保留给 worker/测试路径。
  3. POST 路由：早检查仅保留 404/409 优先级；`begin_task` → 构造 agent（ValueError 时立即 `finish_task` 回滚登记，T3 再统一系统化）→ persist running → worker.start。agent 上下文来自登记时快照。
  4. PATCH/DELETE 路由的 404→409→400→消息 404 错误优先级与既有契约逐级保持；`_sync_derived_message_fields` 移为 `core.threads.sync_derived_message_fields`，报告/用户查询同步语义不变。
- **修复后命令与结果**：`uv run pytest tests/backend/test_message_ops.py tests/backend/test_lifecycle.py tests/backend/test_threads.py -q` → **26 passed**；`uv run pytest tests/backend -q` → **207 passed**（基线 200 + 新增 7，无回归）。
- **剩余限制**：互斥用例中 `time.sleep(0.05)` 仅用于"持锁期间另一方必须仍阻塞"的负向断言检测（不决定通过与否）；`begin_task` 快照读取持锁做小 JSON 磁盘读（与 `rename` 同级，符合"锁内禁止长操作"约束）；worker 收尾仍使用带 handle 校验的 `persist`（既有语义，T3 再绑定取消与失败清理）。

---

#### T3：取消绑定任务实例，启动失败必须清理 — 已完成（2026-09-05）

- **修改文件**：`core/threads.py`（`request_cancel` 增加可选 `expected_handle` 参数，实例核对与 set 同锁）；`app.py`（新增 `_cleanup_failed_start` 与模块级 `_make_ndjson_stream`，POST 路由三条失败路径统一清理，流生成器改带界限等待 + 按捕获 handle 取消）；扩展 `tests/backend/test_cancel.py`、`tests/backend/test_lifecycle.py`（新增 6 个用例）。
- **旧代码失败证据**（`uv run pytest tests/backend/test_cancel.py tests/backend/test_lifecycle.py tests/backend/test_search_events.py -q`，6 failed | 16 passed）：
  - `TypeError: request_cancel() got an unexpected keyword argument 'expected_handle'`（取消未绑定实例，旧流 finally 会按会话 ID 取消新任务）；
  - `AttributeError: module 'app' has no attribute '_make_ndjson_stream'`（流生成器为路由内闭包，不可直接驱动；旧 finally `request_cancel(thread.id)` 无实例绑定由上一条佐证）；
  - 注入初始 persist 抛 OSError：`OSError: 注入的磁盘故障` 未捕获直接冒泡（无结构化错误 + handle 泄漏）；
  - 注入 agent 构造失败：`assert 'idle' == 'error'`（未保存明确 error 状态）；
  - 注入 worker.start 抛 RuntimeError：异常冒泡、登记未释放（重试将永久 409）；
  - 队列等待无界限：worker 已死且无哨兵时 `asyncio.to_thread(out_queue.get)` 永久挂起。
- **修复要点**：
  1. `request_cancel(thread_id, expected_handle=None)`：同锁内核对登记实例后才 set；旧流 finally 传捕获 handle；用户 cancel API 不传参，保留"取消当前任务"语义。
  2. `_cleanup_failed_start`：persist error 状态（尽力而为）→ handle 校验版 `finish_task`。三条失败路径（persist OSError / agent 构造 ValueError / worker.start 异常）统一走它。
  3. persist OSError 与 worker.start 失败返回结构化 `internal` 500（磁盘故障不再伪装 404，也不再裸异常）；线程已删除（persist 返回 False）仍 404；重试启动不再被旧 handle 卡 409。
  4. `_make_ndjson_stream`：队列等待改 `out_queue.get(True, 0.5)` 带界限，`queue.Empty` 时检查 `finished/cancel_event` 再决定继续或退出；队列保持无界（worker 收尾即停投递，不会堵死在 put，无消费者时不无限累积）。
- **修复后命令与结果**：`uv run pytest tests/backend/test_cancel.py tests/backend/test_lifecycle.py tests/backend/test_search_events.py -q` → **22 passed**；`uv run pytest tests/backend -q` → **213 passed**（207 + 新增 6，无回归）。
- **剩余限制**：`_make_ndjson_stream` 的空队列轮询间隔为 0.5s（断流后最多 0.5s 退出等待）；`worker.start` 失败捕获范围为 `Exception`（任何启动失败都必须清理登记）；"删除中的 worker 不复活会话"由既有 `test_delete_running_thread_is_not_resurrected` 继续覆盖（通过）。

---

#### T4：正文缓存身份覆盖完整内容和配置 — 已完成（2026-09-05）

- **修改文件**：新增 `core/chunk_identity.py`（版本化内容指纹，供 agent 缓存键与 rag 集合身份共享）；`core/agent.py`（`_retriever_cache_key` 重写、`_build_rag_retriever` 失败回退缓存策略）；`core/rag.py`（`_chunk_id`/`_normalize_chunk`/`_collection_name` 重写）；扩展 `tests/backend/test_agent_pure.py`、`tests/backend/test_rag_pure.py`（新增 15 个用例）。
- **旧代码失败证据**（`uv run pytest tests/backend/test_agent_pure.py tests/backend/test_rag_pure.py -q`，10 failed | 69 passed）：
  - 500 字后内容变化：`_retriever_cache_key` 与 `_chunk_id` 均得到相同指纹（截断到 500 字）→ 旧检索器/旧 Qdrant 集合被复用，证据过时（与计划审查阶段隔离脚本复现一致）；
  - 仅页码/来源变化：缓存键与集合身份不变 → evidence 沿用旧元数据；
  - 字段值含 `|` 分隔符：无转义拼接产生指纹碰撞（`arxiv_id="x", doi="y|z"` 与 `arxiv_id="x|y", doi="z"` 同键）；
  - 外部旧 chunk_id 被 `_normalize_chunk` 原样保留，绕过新内容指纹；
  - hybrid 失败回退 TF-IDF 被写进进程缓存，hybrid 恢复后仍复用降级检索器；
  - 缓存键不含检索器类型/embedding 模型/reranker 开关。
- **修复要点**：
  1. `core/chunk_identity.py`：`IDENTITY_VERSION=2` + 完整 text + 证据元数据（page_number/page_end/section_title/source_url/pdf_url 等 10 个字段）的 `json.dumps(sort_keys)` 确定性序列化 + sha256；`chunks_identity` 顺序敏感聚合。不含任何密钥字段。
  2. `_chunk_id` = 版本化内容指纹（chunk_index 兜底）；`_normalize_chunk` 把外部传入的旧 chunk_id 保留为 `source_chunk_id`（来源标识），`chunk_id` 一律重算指纹，旧 ID 无法绕过；`_collection_name` = 模型标签 + 各分块指纹（sha256）。
  3. `_retriever_cache_key` = 分块聚合指纹 + 检索器类型 + embedding 模型 + reranker 开关（与 HybridRetriever 构建时读取的同组 config 值一致）。
  4. 失败回退失效策略：hybrid 失败产生的 TF-IDF 回退**不写入**进程缓存，下一轮必重试 hybrid；显式配置的 TF-IDF 模式仍正常缓存。
  5. 兼容性：旧磁盘集合按新命名自然失效重建（由既有 `_prune_stale_collections` 清理残留）；用户正文/PDF/会话数据不动；旧线程 JSON 不受影响。
- **修复后命令与结果**：`uv run pytest tests/backend/test_agent_pure.py tests/backend/test_rag_pure.py -q` → **79 passed**；`uv run pytest tests/backend -q` → **228 passed**（213 + 新增 15，无回归）。
- **剩余限制**：进程缓存键的配置项来自 config 模块当前值（config 为进程启动时环境注入，运行期无变更入口）；真实 FastEmbed/Qdrant 未在单测中运行（按计划用纯函数与替身验证，模型下载不发生）；"元数据更新可见"的端到端链路由既有 `test_rag_context_exposes_page_and_section_metadata`（通过）与 T7 打包验收补充。

---

#### T5：健壮读取 NDJSON 并保留错误 — 已完成（2026-09-05）

- **修改文件**：新增 `desktop/src/mainview/ndjson.ts`（流解析独立模块 `readNdjsonStream`）；修改 `desktop/src/mainview/api.ts`（`streamThreadMessage` 委托流读取）；新增 `desktop/src/mainview/api.stream.test.ts`（12 个用例）。
- **旧代码失败证据**（`bunx vitest run src/mainview/api.stream.test.ts`，6 failed | 6 passed）：
  - 末行无换行：`expected [ { type: 'intent', … } ] to have a length of 2 but got 1`（`if (done) break` 丢弃残留 buffer，尾行丢失）；
  - 坏 JSON：`promise resolved instead of rejecting`（静默跳过，且 console.warn 携带完整原始行）；
  - 回调抛错：`Failed to parse NDJSON line Error: 业务回调内部错误`（try/catch 同时包解析与回调，业务错误被吞并误标为解析失败）；
  - 空流 / 只有中间事件就 EOF：promise 正常 resolve（无终态校验，中断与完成不可区分）；
  - （AbortError 用例首次运行为测试自身未触发 abort，修正后通过。）
- **修复要点**：
  1. `readNdjsonStream`：循环读块；结束时 `decoder.decode()` flush 残留多字节字节并处理末行（无换行的完整行不丢）；`\n`/`\r\n` 均兼容；空行跳过。
  2. 解析 try/catch 只包 `JSON.parse`：坏行抛 `NDJSON 协议错误：第 N 行…（≤80 字符摘要）`，完整原始行不落 console；`onEvent` 调用在保护块之外，业务错误原样上抛。
  3. 最小信封校验：必须是对象且含非空字符串 `type`。
  4. 终态跟踪：`done/error/cancelled` 记为终态；EOF 无终态 → 抛"事件流提前中断"；看到 `done` 后继续读完（后端随后发送的持久化失败 `error` 仍会分发）；正常 EOF 不合成虚假事件。
  5. 资源清理：失败/取消路径 `reader.cancel(err)` 后 `finally` 中 `releaseLock()`；正常 EOF 只释放锁。
- **修复后命令与结果**：`bunx vitest run src/mainview/api.stream.test.ts tests/integration/contract.spec.ts` → **16 passed**（12 新增 + 4 契约集成）；`bun run typecheck` → 通过；`bun run test:unit` → **72 passed**（60 + 新增 12，无回归）。
- **剩余限制**：跨块 UTF-8 在旧实现已正确（`stream: true` 解码），新用例将其锁定为回归保障；真实断连（TCP RST 等）场景由 AbortError/EOF 用例模拟，未在真实网络环境注入。

---

#### T6：让取消状态与后端真实状态一致 — 已完成（2026-09-05）

- **修改文件**：新增 `desktop/src/mainview/useStopConfirmation.ts`（停止确认 hook：cancel 请求 + 带界限短轮询）；修改 `desktop/src/mainview/App.tsx`（`handleStopSearch` 重写、`handleSendQuery` catch/finally 的停止路径让位、`releaseStopWait` 接入切换/新建/删除入口）；新增 `desktop/src/mainview/cancellation.test.tsx`；扩展 `desktop/tests/mock-backend/server.js`（`[sticky-cancel]`/`[sticky-cancel-<ms>]` 脚本：cancel 受理后线程保持 running 至窗口结束，用于 UI 与 E2E 验证）。
- **旧代码失败证据**（`bunx vitest run src/mainview/cancellation.test.tsx`，对旧语义直移版 hook：5 failed | 5 failed——全部失败）：
  - 立即声称已停止：`expected 'idle' to be 'stopping'`（本地 abort 即确认，不核实后端；对应旧行为 finally 立即解除 isSearching + catch 追加"已停止当前检索。"）；
  - cancel 请求失败被 `.catch(() => {})` 静默吞掉：`onCancelRequestError` 未被调用；
  - 无轮询：`getThread` 从未被调用（终态无从确认）；
  - 无超时语义：`onTimeout` 未被调用；
  - 归属无关：切换会话后仍会误报终态。
  （旧行为代码证据：原 `handleStopSearch` 的 `cancelThread(run.threadId).catch(() => {})` 与流 finally 的立即 `setIsSearching(false)` + 详情刷新。）
- **修复要点**：
  1. `useStopConfirmation`：`confirmStop(threadId, stillRelevant)` = cancel 请求（失败回调可见，不放弃）→ 500ms 间隔、30s 上限短轮询 `getThread`，每轮核对 selection generation + threadId（沿用 T1 归属逻辑）→ 非 running 终态 `onConfirmed`；超时 `onTimeout`（phase=unconfirmed，"停止尚未确认"，不声称已停止）。
  2. `handleStopSearch`：运行中 → abort + 确认；等待/超时态 → 重试确认（幂等 abort）；切换/新建/删除 → `releaseStopWait()` 复位轮询与 isSearching，新会话立即可用。
  3. `handleSendQuery`：AbortError 且 stopRequested → 直接返回（不追加消息、不声称已停止）；finally 在 stopRequested 时保持 isSearching=true（提交禁用）、跳过详情刷新与状态覆盖；"已停止当前检索。"提示只在 `onConfirmed` 且后端状态为 cancelled 时追加一次（不重复追加）。
  4. mock 后端：sticky-cancel 窗口内 cancel/断流都不立即置 cancelled，计时器到期才转——UI 与 E2E 均可验证"正在停止"等待路径。
- **修复后命令与结果**：`bunx vitest run src/mainview/cancellation.test.tsx src/mainview/threadSelection.test.tsx` → **13 passed**；`bun run typecheck` → 通过；`bun run test:unit` → **77 passed**（72 + 新增 5，无回归）；`node --check tests/mock-backend/server.js` → 通过。
- **剩余限制**：状态栏文案/提交禁用的**真实 UI 呈现**由 T7 Electron E2E（sticky-cancel 路径）覆盖；轮询参数为常量（500ms/30s），未做配置化；30s 超时后用户若不重试，UI 保持"停止尚未确认"且提交禁用（后端终态仍可从会话详情看到）——这是"不把本地 abort 等同于任务已结束"的保守取舍。

---

#### T7：补充用户路径验收并闭合发布记录 — 已完成（2026-09-05）

- **修改/新增文件**：
  - `desktop/tests/e2e/app.spec.ts` [改]：新增 T6 sticky-cancel UI 用例与 T1 乱序详情用例；每个测试 try/finally 关闭窗口；beforeAll 增加端口预检。
  - `desktop/tests/e2e/backend-flow.spec.ts` [新] + `desktop/tests/e2e/backend_harness.py` [新]：真实 FastAPI + 真实 Electron 全链路。harness 仅替换 LLM 流 / 检索源 / PDF 下载 / embedding（确定性 32 维伪向量 + qdrant `:memory:`），路由、worker、持久化、HMAC 认证链全部真实；全新临时 DATA_DIR；覆盖 检索→正文证据→导出→关闭重启→历史恢复；含端口占用 fail-fast 用例（自起占用进程，测试自行清理）。
  - `desktop/src/electron/main.cjs` [改]：`ARXIV_AGENT_E2E_BACKEND_ENTRY`（E2E 后端入口覆盖，生产默认仍 app.py）；`exports:save` 源目录在 E2E 数据目录覆盖下对齐。
  - `desktop/tests/mock-backend/server.js` [改]：`/__mock/detail-delay` 控制端点；`[sticky-cancel]`/`[sticky-cancel-<ms>]` 脚本；`chat-title` 断言配合。
  - `desktop/src/mainview/App.tsx` [改]：ChatHeader 标题加 `data-testid="chat-title"`。
  - `desktop/playwright.config.ts` [改]：trace → `retain-on-failure`（原 on-first-retry 与 retries=0 组合永不产生 trace）；`workers: 1`（app.spec 与 backend-flow 均独占 7860，必须串行）。
  - `.github/workflows/ci.yml` [改]：E2E 失败时上传 `desktop/test-results/` 与 `desktop/playwright-report/`（保留 7 天）。
  - `README.md` [改]：依赖恢复说明按内置 Python 运行时核对——打包版用户开箱即用，不需要 uv/Python；`uv sync` 仅面向源码/开发部署。
  - `implementation_plan.md` [改]：追加 T7 落地记录。
- **E2E 结果**：`bun run test:e2e` → **7 passed（2.0m）**：3 个既有用例（检索完成/慢检索取消/重命名切换删除）+ sticky-cancel 停止等待 + 乱序详情 + 真实后端全链路（33.8s，重启后历史恢复与 UI 导出均通过）+ 端口占用 fail-fast。
- **端口约定**：7860 被占用时两个 spec 均快速报错并说明可能来源，不杀任何进程；真实后端链路由 Electron 主进程以 harness 为入口自行 spawn 并从 stdout 捕获 HMAC 凭证（认证链真实走通，smoke 日志 `authOk:true` 同为佐证）。

---

### 发布记录（2026-09-05）

- **基线 commit**：`4a76da785b9e76ed6bec5356855741db262e0854`（本报告未提交，全部改动在工作区）。
- **工作区差异**：`git diff --stat` = 18 个已跟踪文件修改，+1428/−315 行；新增未跟踪文件 10 个（`core/chunk_identity.py`、`desktop/src/mainview/{ndjson,useThreadSelection,useStopConfirmation}.ts`、`threadSelection.test.tsx`、`api.stream.test.ts`、`cancellation.test.tsx`、`desktop/tests/e2e/{backend-flow.spec.ts,backend_harness.py}`），另有本计划文档。
- **平台**：Windows 11（win32 10.0.26200 x64），Git Bash；Python 3.12（uv 管理，`.venv`）；Bun 1.3.x；Electron 42。
- **最后一次源码改动后串行验证（全部实跑，退出码 0）**：

  | 命令 | 退出码 | 结果 |
  |---|---|---|
  | `uv run pytest tests/backend -q` | 0 | 228 passed（基线 200 + 新增 28） |
  | `uv run python -m compileall -q app.py config.py core` | 0 | 通过 |
  | `git diff --check` | 0 | 通过（仅 CRLF 提示，无空白错误） |
  | `bun run test:unit` | 0 | 10 files / 77 passed（基线 52 + 新增 25） |
  | `bun run build` | 0 | vite 构建通过 |
  | `bun run test:e2e` | 0 | 7 passed（含真实后端全链路） |
  | `bun run smoke` | 0 | `backendReady:true, authOk:true, rendererReady:true` |
  | `bun run pack` | 0 | uv sync → prepare:backend-runtime → build → electron-builder --dir |
  | `bun run smoke:packaged` | 0 | `[packaged-smoke] Passed (exit 0)` |

- **产物**：`desktop/build/electron/win-unpacked/`（unpacked 目录，657 MB）；主程序 `desktop/build/electron/win-unpacked/ArxivAgent.exe` SHA256 = `338cd59392283e830c3f6e88fc3c41872925143e255fb7ab54bf3a97e897541e`。`pack` 未生成新的 NSIS 安装包。
- **验收边界（明确声明）**：
  - 本轮仅验收 **unpacked** 打包产物（`pack` + `smoke:packaged`）；**未执行 NSIS 安装器安装/卸载验收**（`build:canary` 未运行，`build/electron/` 下既有 `ArxivAgent-Setup-0.4.0.exe` 为历史产物，非本轮构建）。
  - 未调用真实付费模型 / 真实检索源；真实后端链路中的 LLM、检索源、PDF、embedding 均为确定性替身（边界替换清单见 harness 文件头）。"真实论文研究质量"仍未验证。
  - 视觉/UI 呈现仅通过 Playwright 结构断言（文本、role、testid）验证，未做像素级视觉验收。
  - 未执行安装器与真实模型验收即声明：本轮发布验证等级为 **unpacked + 冒烟**。
