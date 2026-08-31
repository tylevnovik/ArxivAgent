# ArxivAgent 稳定性与发布加固实施计划

> **For Codex:** 按任务顺序执行，每个任务完成后运行对应的最小验证，再进行完整回归。

**Goal:** 收口最新审查中确认的线程生命周期、终态状态、配置、中文检索、安全边界、输入约束、文档/CI 与打包 smoke 问题。

**Architecture:** 后端由 `ThreadManager` 统一协调磁盘线程与运行期任务；worker 只通过 manager 的受保护持久化入口写回。配置仍由 `config.py` 集中读取。FastAPI 仅允许桌面端和本地开发源跨域，配置健康探测纳入 HMAC 保护。桌面主进程为 smoke 提供确定性退出和超时包装。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、pytest、Electron、React/Vite、Bun、Vitest、Playwright、electron-builder、GitHub Actions。

---

## 1. 回归测试先行

- 为运行中删除增加竞态测试：删除成功后 worker 结束不得重新生成线程 JSON。
- 为错误事件和取消 DONE 事件增加 worker 终态断言。
- 为 `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` 增加环境变量覆盖测试。
- 为无 DOI/arXiv ID 的中文论文增加身份、中文 token、排序测试。
- 为 CORS 允许/拒绝来源和 `MessageRequest` 数值边界增加测试。

## 2. 实现核心修复

- `ThreadManager` 增加删除 tombstone、按 handle 校验的原子持久化与任务收尾，阻断删除竞态。
- worker 根据 `ERROR`、`DONE(cancelled)` 与异常设置真实终态，并保存 `last_error`。
- 配置默认值改为读取环境变量；请求参数增加有界约束。
- 检索身份与 token 归一化支持 Unicode/CJK，保留 DOI/arXiv/source 优先级。
- 收紧 CORS origin/method/header，移除配置健康探测的公开白名单。

## 3. 发布链路

- 主进程 smoke 分支等待后端停止，bootstrap 异常明确退出。
- 新增跨平台的打包 smoke runner，提供超时、进程树清理和退出码。
- 更新 README 与实施记录的测试事实、配置说明和验证边界。
- 新增 GitHub Actions，覆盖后端测试、前端类型检查/单测/构建和 Electron E2E。

## 4. 验证

- `uv run pytest tests/backend -q`
- `uv run python -m compileall -q app.py core`
- `bun run typecheck`
- `bun run test:unit`
- `bun run build`
- `bun run test:e2e`
- `bun run smoke`
- `bun run pack` 后运行 `bun run smoke:packaged`
- 最后检查 `git diff --check`、工作树差异和残留进程/端口。
