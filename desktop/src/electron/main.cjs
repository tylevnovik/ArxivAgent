const { app, BrowserWindow, shell, ipcMain, safeStorage } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { fileURLToPath, pathToFileURL } = require("node:url");
const {
	decodeSecretEntry,
	encodeSecretEntry,
	isEncryptionAvailable,
} = require("./secrets-store.cjs");

const DEV_SERVER_PORT = 5173;
const DEV_SERVER_URL = `http://127.0.0.1:${DEV_SERVER_PORT}`;
const BACKEND_PORT = 7860;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

let mainWindow = null;
let pythonProc = null;
let isQuitting = false;
const isSmokeTest = process.argv.includes("--smoke-test");
const GRACEFUL_SHUTDOWN_TIMEOUT_MS = 3000;
const FORCE_KILL_WAIT_MS = 2000;

// ===================== HMAC 认证 Token =====================
// Python 后端启动时通过 stdout 输出 ARXIV_AGENT_AUTH_TOKEN=<hex>，
// Electron 主进程捕获后存于此，通过 IPC 传给渲染进程。
let currentAuthToken = null;

function sleep(ms) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function canFetch(url, options = {}) {
	const controller = new AbortController();
	const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 1000);
	try {
		const response = await fetch(url, {
			method: options.method ?? "GET",
			signal: controller.signal,
		});
		return response.ok;
	} catch {
		return false;
	} finally {
		clearTimeout(timeout);
	}
}

async function waitForUrl(url, timeoutMs, options = {}) {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		if (await canFetch(url, options)) {
			return true;
		}
		await sleep(300);
	}
	return false;
}

async function isBackendHealthy() {
	return canFetch(`${BACKEND_URL}/api/health`, { method: "GET", timeoutMs: 1200 });
}

// ===================== 安全存储（API Key） =====================
// safeStorage 在 Linux 无 keyring 环境不可用；此时回退到明文文件，并经 IPC 告知前端。

function secretsFile() {
	return path.join(app.getPath("userData"), "secrets.json");
}

function readSecretsStore() {
	const file = secretsFile();
	try {
		if (!fs.existsSync(file)) return {};
		return JSON.parse(fs.readFileSync(file, "utf-8")) || {};
	} catch (err) {
		console.warn("[Electron Main] 读取 secrets 文件失败，重置为空:", err);
		return {};
	}
}

function writeSecretsStore(store) {
	const file = secretsFile();
	fs.mkdirSync(path.dirname(file), { recursive: true });
	// mode: 0o600 限制只有所有者可读写。Linux 无 keyring 时 safeStorage 回退明文，
	// 此权限防止同机其他用户读到 API Key。Windows 上 mode 被忽略，无副作用。
	fs.writeFileSync(file, JSON.stringify(store, null, 2), { encoding: "utf-8", mode: 0o600 });
}

function registerSecretsHandlers() {
	ipcMain.handle("secrets:encryptionAvailable", () => {
		return isEncryptionAvailable(safeStorage);
	});

	ipcMain.handle("secrets:get", (_event, key) => {
		if (typeof key !== "string" || !key) return null;
		if (process.env.ARXIV_AGENT_E2E === "1" && key === "api_key") {
			return "e2e-api-key";
		}
		const store = readSecretsStore();
		const entry = store[key];
		if (!entry) return null;
		try {
			return decodeSecretEntry(entry, safeStorage);
		} catch (err) {
			console.warn(`[Electron Main] 解密 ${key} 失败:`, err);
			return null;
		}
	});

	ipcMain.handle("secrets:set", (_event, key, value) => {
		if (typeof key !== "string" || !key) return;
		const store = readSecretsStore();
		const v = typeof value === "string" ? value : String(value ?? "");
		if (!v) {
			delete store[key];
		} else {
			store[key] = encodeSecretEntry(v, safeStorage);
		}
		writeSecretsStore(store);
	});

	ipcMain.handle("secrets:delete", (_event, key) => {
		if (typeof key !== "string" || !key) return;
		const store = readSecretsStore();
		delete store[key];
		writeSecretsStore(store);
	});
}

function registerBackendHandlers() {
	// 诊断：返回解释器/依赖/health 状态，供渲染进程向导展示
	ipcMain.handle("backend:diagnose", async () => diagnoseBackend());

	// 重试：停止旧进程并重新走启动流程，返回是否成功（带诊断原因）
	ipcMain.handle("backend:retry", async () => {
		stopPythonBackend();
		const result = await startPythonBackend();
		return { ok: result.ok, error: result.error || null };
	});
}

function registerAuthHandlers() {
	// 返回当前后端的 HMAC token（供渲染进程签名请求用）。
	ipcMain.handle("auth:getToken", () => {
		return currentAuthToken;
	});
}

function registerWindowHandlers() {
	ipcMain.on("window:minimize", (event) => {
		BrowserWindow.fromWebContents(event.sender)?.minimize();
	});
	ipcMain.on("window:toggleMaximize", (event) => {
		const win = BrowserWindow.fromWebContents(event.sender);
		if (!win) return;
		if (win.isMaximized()) {
			win.unmaximize();
		} else {
			win.maximize();
		}
	});
		ipcMain.on("window:close", (event) => {
			BrowserWindow.fromWebContents(event.sender)?.close();
		});
	}

// ===================== PID 文件 & 进程清理 =====================

function pidFilePath() {
	return path.join(app.getPath("userData"), "backend-data", "backend.pid");
}

/**
 * 从 PID 文件读取残留进程 PID，检查进程是否仍存在。
 * 返回 { pid, alive } 或 null（文件不存在）。
 */
function readStalePidFile() {
	const file = pidFilePath();
	try {
		if (!fs.existsSync(file)) return null;
		const pid = parseInt(fs.readFileSync(file, "utf-8").trim(), 10);
		if (isNaN(pid) || pid <= 0) return { pid: null, alive: false };
		// 检查进程是否存活
		try {
			process.kill(pid, 0); // signal 0 = 探测
			return { pid, alive: true };
		} catch {
			return { pid, alive: false };
		}
	} catch {
		return null;
	}
}

/**
 * 删除 PID 文件（忽略错误）。
 */
function removePidFile() {
	try {
		const file = pidFilePath();
		if (fs.existsSync(file)) fs.unlinkSync(file);
	} catch {
		// ignore
	}
}

/**
 * 尝试通过 HTTP POST /api/shutdown 优雅关闭后端。
 * 返回 true 表示关闭请求已发送（不保证进程已退出）。
 */
async function gracefulShutdownBackend() {
	try {
		const controller = new AbortController();
		const timer = setTimeout(() => controller.abort(), GRACEFUL_SHUTDOWN_TIMEOUT_MS);
		const res = await fetch(`${BACKEND_URL}/api/shutdown`, {
			method: "POST",
			signal: controller.signal,
		});
		clearTimeout(timer);
		return res.ok;
	} catch {
		return false;
	}
}

/**
 * 强制杀死一个 PID 对应的进程及其子进程树。
 */
function forceKillPid(pid) {
	if (!pid || pid <= 0) return;
	console.log(`[Electron Main] Force killing PID ${pid}.`);
	if (process.platform === "win32") {
		spawn("taskkill", ["/pid", String(pid), "/T", "/F"], {
			stdio: "ignore",
			windowsHide: true,
		});
	} else {
		try {
			process.kill(-pid, "SIGKILL"); // 杀进程组
		} catch {
			try {
				process.kill(pid, "SIGKILL");
			} catch {
				// 进程可能已退出
			}
		}
	}
}

/**
 * 清理残留后端进程：PID 文件 → 优雅关闭 → 强制杀 → 等待端口释放。
 * 在启动新后端前调用。
 */
async function cleanupStaleBackend() {
	const stale = readStalePidFile();
	if (!stale || !stale.alive) {
		// PID 文件不存在或进程已退出 → 清理文件即可
		removePidFile();
		return;
	}

	console.log(`[Electron Main] 检测到残留后端进程 PID ${stale.pid}，正在清理…`);

	// 1. 尝试优雅关闭（HTTP）
	const shutdownOk = await gracefulShutdownBackend();

	// 2. 等待进程退出
	if (shutdownOk) {
		const deadline = Date.now() + FORCE_KILL_WAIT_MS;
		while (Date.now() < deadline) {
			try {
				process.kill(stale.pid, 0);
				await sleep(200);
			} catch {
				// 进程已退出
				break;
			}
		}
	}

	// 3. 检查是否还在，如果还活着就强制杀
	try {
		process.kill(stale.pid, 0);
		forceKillPid(stale.pid);
		// 等端口释放
		await sleep(1000);
	} catch {
		// 进程已退出
	}

	removePidFile();
	console.log("[Electron Main] 残留后端进程清理完成。");
}

function findBackendRoot() {
	const candidates = [];

	if (app.isPackaged) {
		candidates.push(path.join(process.resourcesPath, "backend"));
	}

	candidates.push(path.resolve(__dirname, "..", "..", ".."));
	candidates.push(path.resolve(app.getAppPath(), ".."));

	return candidates.find((candidate) => fs.existsSync(path.join(candidate, "app.py")));
}

function findPythonPath(backendRoot) {
	/**
	 * 解析 Python 解释器路径。返回 { path, source } 便于诊断。
	 *
	 * 解析顺序（不再向上遍历 cwd 找 .venv —— 那是让 dev smoke 假通过的根源）：
	 *   1. ARXIV_AGENT_PYTHON 环境变量（显式覆盖）
	 *   2. packaged resources/python（打包脚本准备的 sidecar runtime）
	 *   3. backendRoot/.venv（dev 模式仓库根有 .venv）
	 *   4. app 目录祖先的 .venv（仅限 dev：app.getAppPath()/resourcesPath）
	 *   5. 系统 python（最后兜底；干净机器上会失败 → 触发向导）
	 */
	if (process.env.ARXIV_AGENT_PYTHON && fs.existsSync(process.env.ARXIV_AGENT_PYTHON)) {
		console.log(`[Electron Main] Using ARXIV_AGENT_PYTHON: ${process.env.ARXIV_AGENT_PYTHON}`);
		return { path: process.env.ARXIV_AGENT_PYTHON, source: "env" };
	}

	const bundledPython = getBundledPythonInfo();
	if (bundledPython) {
		console.log(`[Electron Main] Found bundled Python: ${bundledPython.path}`);
		return bundledPython;
	}

	const windowsVenvPython = path.join(backendRoot, ".venv", "Scripts", "python.exe");
	const posixVenvPython = path.join(backendRoot, ".venv", "bin", "python");

	if (process.platform === "win32" && fs.existsSync(windowsVenvPython)) {
		return { path: windowsVenvPython, source: "venv" };
	}
	if (process.platform !== "win32" && fs.existsSync(posixVenvPython)) {
		return { path: posixVenvPython, source: "venv" };
	}

	// 仅 dev 模式向上找 .venv（app.getAppPath 在 dev 指向源码目录）；
	// 不再用 process.cwd() —— 它在 packaged 下指向用户的任意目录，是误判来源。
	if (!app.isPackaged) {
		const ancestorVenvPython = findAncestorVenvPython([
			backendRoot,
			app.getAppPath(),
			__dirname,
		]);
		if (ancestorVenvPython) {
			return { path: ancestorVenvPython, source: "venv-ancestor" };
		}
	}

	console.log("[Electron Main] Virtualenv not found. Falling back to system python.");
	return {
		path: process.platform === "win32" ? "python" : "python3",
		source: "system",
	};
}

function getBundledPythonInfo() {
	if (!app.isPackaged) return null;

	const runtimeRoot = path.join(process.resourcesPath, "python");
	const sitePackages = path.join(process.resourcesPath, "python-site-packages");
	const candidates = process.platform === "win32"
		? [path.join(runtimeRoot, "python.exe")]
		: [
			path.join(runtimeRoot, "bin", "python3"),
			path.join(runtimeRoot, "bin", "python"),
			path.join(runtimeRoot, "python"),
		];
	const pythonPath = candidates.find((candidate) => fs.existsSync(candidate));
	if (!pythonPath) return null;

	return {
		path: pythonPath,
		source: "bundled",
		sitePackages: fs.existsSync(sitePackages) ? sitePackages : null,
	};
}

function buildPythonEnv(pythonInfo) {
	const env = { ...process.env };
	if (pythonInfo?.sitePackages) {
		const pywin32Paths = [
			path.join(pythonInfo.sitePackages, "win32"),
			path.join(pythonInfo.sitePackages, "win32", "lib"),
			path.join(pythonInfo.sitePackages, "pythonwin"),
			path.join(pythonInfo.sitePackages, "pywin32_system32"),
		].filter((candidate) => fs.existsSync(candidate));
		const pythonPathEntries = [pythonInfo.sitePackages, ...pywin32Paths];
		if (env.PYTHONPATH) pythonPathEntries.push(env.PYTHONPATH);
		env.PYTHONPATH = pythonPathEntries.join(path.delimiter);
		if (pywin32Paths.length) {
			env.PATH = [path.join(pythonInfo.sitePackages, "pywin32_system32"), env.PATH || ""]
				.filter(Boolean)
				.join(path.delimiter);
		}
	}
	env.PYTHONUTF8 = "1";
	env.PYTHONIOENCODING = "utf-8";
	return env;
}

function findAncestorVenvPython(startPaths) {
	const seen = new Set();
	for (const startPath of startPaths) {
		if (!startPath) continue;

		let current = fs.existsSync(startPath) && fs.statSync(startPath).isFile()
			? path.dirname(startPath)
			: startPath;

		for (let depth = 0; depth < 8; depth += 1) {
			const resolved = path.resolve(current);
			if (seen.has(resolved)) break;
			seen.add(resolved);

			const candidate = process.platform === "win32"
				? path.join(resolved, ".venv", "Scripts", "python.exe")
				: path.join(resolved, ".venv", "bin", "python");
			if (fs.existsSync(candidate)) {
				return candidate;
			}

			const parent = path.dirname(resolved);
			if (parent === resolved) break;
			current = parent;
		}
	}

	return null;
}

/** 快速探测一个 Python 解释器能否 import 核心依赖。返回 {ok, missing}。 */
function checkPythonDeps(pythonInfo) {
	return new Promise((resolve) => {
		// 一行式 import 探测，避免起整个后端
		const probeScript = "import importlib,sys;"
			+ "mods=['fastapi','uvicorn','openai','requests','pypdf','qdrant_client','bm25s'];"
			+ "missing=[m for m in mods if importlib.util.find_spec(m) is None];"
			+ "sys.stdout.write(','.join(missing));";
		let out = "";
		let errText = "";
		let child;
		try {
			child = spawn(pythonInfo.path, ["-c", probeScript], {
				stdio: ["ignore", "pipe", "pipe"],
				env: buildPythonEnv(pythonInfo),
				windowsHide: true,
			});
		} catch (e) {
			resolve({ ok: false, missing: [], error: `无法启动 ${pythonInfo.path}: ${e.message}` });
			return;
		}
		const timer = setTimeout(() => {
			try { child.kill("SIGKILL"); } catch { /* noop */ }
			resolve({ ok: false, missing: [], error: "探测超时" });
		}, 8000);
		child.stdout.on("data", (d) => { out += d.toString(); });
		child.stderr.on("data", (d) => { errText += d.toString(); });
		child.on("error", (e) => {
			clearTimeout(timer);
			resolve({ ok: false, missing: [], error: `启动失败：${e.message}（可能未安装 Python）` });
		});
		child.on("exit", (code) => {
			clearTimeout(timer);
			if (code !== 0 && !out) {
				resolve({ ok: false, missing: [], error: errText.slice(0, 300) || `退出码 ${code}` });
				return;
			}
			const missing = out.trim().split(",").filter(Boolean);
			resolve({ ok: missing.length === 0, missing });
		});
	});
}

/** 诊断当前后端启动状态：解释器、依赖、health。供 IPC 向导用。 */
async function diagnoseBackend() {
	const alreadyHealthy = await isBackendHealthy();
	if (alreadyHealthy) {
		return { ok: true, healthy: true, python: null, missing: [], error: null };
	}
	const backendRoot = findBackendRoot();
	if (!backendRoot) {
		return { ok: false, healthy: false, python: null, missing: [], error: "未找到后端代码 app.py" };
	}
	const pythonInfo = findPythonPath(backendRoot);
	const deps = await checkPythonDeps(pythonInfo);
	return {
		ok: deps.ok,
		healthy: false,
		python: { path: pythonInfo.path, source: pythonInfo.source },
		missing: deps.missing,
		error: deps.error || null,
		backend_dir: backendRoot,
	};
}

async function startPythonBackend() {
	// 1. 检查是否已有健康的后端（可能是上次残留或手动启动的）
	if (await isBackendHealthy()) {
		console.log(`[Electron Main] Backend already healthy at ${BACKEND_URL}.`);
		return { ok: true };
	}

	// 2. 清理残留进程（PID 文件 + 端口探测 + 强制杀）
	await cleanupStaleBackend();

	// 二次检查：清理后端口可能就释放了
	if (await isBackendHealthy()) {
		console.log(`[Electron Main] Backend became healthy after cleanup.`);
		return { ok: true };
	}

	const backendRoot = findBackendRoot();
	if (!backendRoot) {
		console.error("[Electron Main] Could not find Python backend app.py.");
		return { ok: false, error: "未找到后端代码 app.py" };
	}

	const pythonInfo = findPythonPath(backendRoot);
	const pythonPath = pythonInfo.path;
	const appPyPath = path.join(backendRoot, "app.py");
	const backendDataDir = path.join(app.getPath("userData"), "backend-data");
	fs.mkdirSync(backendDataDir, { recursive: true });
	const pidFile = path.join(backendDataDir, "backend.pid");
	console.log(`[Electron Main] Spawning Python process: ${pythonPath} ${appPyPath}`);

	// 记录最近一段 stderr，用于在 ready 失败时给出诊断（端口占用 / 依赖缺失等）。
	let lastStderr = "";
	let exitedEarly = false;
	let exitInfo = null;

	try {
		pythonProc = spawn(pythonPath, [appPyPath], {
			cwd: backendRoot,
			// stdin 忽略；stdout 捕获（解析 auth token）；stderr 捕获副本用于诊断，
			// 同时在 data handler 中转发到 process.stderr 保持控制台日志可见性。
			stdio: ["ignore", "pipe", "pipe"],
			env: {
				...buildPythonEnv(pythonInfo),
				ARXIV_AGENT_HOST: "127.0.0.1",
				ARXIV_AGENT_PORT: String(BACKEND_PORT),
				ARXIV_AGENT_DATA_DIR: backendDataDir,
				ARXIV_AGENT_PIDFILE: pidFile,
				PYTHONDONTWRITEBYTECODE: "1",
				PYTHONUNBUFFERED: "1",
			},
			windowsHide: true,
		});

		// 从 Python 后端 stdout 捕获 auth token。
		// Python 启动时会输出 "ARXIV_AGENT_AUTH_TOKEN=<hex>"，我们解析后存入 currentAuthToken。
		let stdoutBuffer = "";
		if (pythonProc.stdout) {
			pythonProc.stdout.on("data", (chunk) => {
				const text = chunk.toString("utf-8");
				stdoutBuffer += text;
				// 逐行扫描 token 标记
				const lines = stdoutBuffer.split("\n");
				stdoutBuffer = lines.pop() || "";
				for (const line of lines) {
					const trimmed = line.trim();
					if (trimmed.startsWith("ARXIV_AGENT_AUTH_TOKEN=")) {
						currentAuthToken = trimmed.slice("ARXIV_AGENT_AUTH_TOKEN=".length);
						console.log("[Electron Main] Captured HMAC auth token from backend.");
					}
				}
			});
			pythonProc.stdout.on("end", () => {
				// 处理最后可能没有换行的残余
				if (stdoutBuffer.trim().startsWith("ARXIV_AGENT_AUTH_TOKEN=")) {
					currentAuthToken = stdoutBuffer.trim().slice("ARXIV_AGENT_AUTH_TOKEN=".length);
					console.log("[Electron Main] Captured HMAC auth token from backend (flush).");
				}
			});
		}

		console.log(`[Electron Main] Python process spawned (PID: ${pythonProc.pid}).`);
		pythonProc.stderr?.on("data", (d) => {
			const text = d.toString();
			// 转发到 Electron 控制台（替代 stdio: inherit 的透传行为）
			process.stderr.write(text);
			// 同时保留最近一段用于诊断
			lastStderr = (lastStderr + text).slice(-2000);
		});
		pythonProc.once("exit", (code, signal) => {
			console.log(`[Electron Main] Python backend exited with code ${code}, signal ${signal}.`);
			exitedEarly = true;
			exitInfo = { code, signal };
			pythonProc = null;
			currentAuthToken = null;
		});
	} catch (err) {
		console.error("[Electron Main] Failed to spawn Python backend:", err);
		return { ok: false, error: `无法启动后端进程: ${err.message}` };
	}

	const ready = await waitForUrl(`${BACKEND_URL}/api/health`, 15000, { method: "GET" });
	if (ready) {
		return { ok: true };
	}

	// ready 失败：尽量给出可读原因。
	console.error(`[Electron Main] Backend did not become healthy at ${BACKEND_URL}.`);
	let reason;
	if (exitedEarly) {
		const hint = /EADDRINUSE|address already in use/i.test(lastStderr)
			? `端口 ${BACKEND_PORT} 可能被占用`
			: /ModuleNotFoundError|ImportError/.test(lastStderr)
				? "依赖缺失"
				: `进程提前退出（code=${exitInfo?.code}）`;
		reason = `后端启动后立即退出：${hint}。详见控制台日志。`;
	} else {
		// 进程还活着但 health 不通 —— 可能还在初始化（Qdrant 模型加载较慢）。
		reason = `后端进程在运行，但 15s 内未响应 health 检查（可能在加载模型或下载依赖）。`;
	}
	return { ok: false, error: reason };
}

async function getRendererUrl() {
	if (!app.isPackaged) {
		const viteReady = await waitForUrl(DEV_SERVER_URL, 15000, { method: "HEAD" });
		if (viteReady) {
			console.log(`[Electron Main] Using Vite dev server at ${DEV_SERVER_URL}.`);
			return DEV_SERVER_URL;
		}

		console.log("[Electron Main] Vite dev server unavailable; falling back to dist/index.html.");
	}

	const indexHtml = findRendererIndexHtml();
	return pathToFileURL(indexHtml).toString();
}

function findRendererIndexHtml() {
	// 不再用 process.cwd() 作为候选 —— 打包后它指向用户任意目录，
	// 是误判来源（见 findPythonPath 同名注释）。
	const candidates = [
		path.join(app.getAppPath(), "dist", "index.html"),
		path.resolve(__dirname, "..", "..", "dist", "index.html"),
	];
	const indexHtml = candidates.find((candidate) => fs.existsSync(candidate));
	if (!indexHtml) {
		return candidates[0];
	}
	return indexHtml;
}

async function isRendererAvailable(rendererUrl) {
	if (rendererUrl.startsWith("file:")) {
		return fs.existsSync(fileURLToPath(rendererUrl));
	}
	return canFetch(rendererUrl, { method: "GET", timeoutMs: 1200 });
}

function createWindow(rendererUrl) {
	const isWindows = process.platform === "win32";
	const isMac = process.platform === "darwin";

	mainWindow = new BrowserWindow({
		width: 1280,
		height: 850,
		minWidth: 960,
		minHeight: 680,
		title: "ArxivAgent",
		autoHideMenuBar: true,
		frame: false,
		roundedCorners: true,
		show: false,
		// Win11: prefer Mica over Acrylic. Mica is system-drawn, cheaper for large
		// backgrounds, and keeps native DWM rounded-corner clipping intact.
		backgroundColor: isWindows ? "#0b0c10" : "#00000000",
		backgroundMaterial: isWindows ? "mica" : undefined,
		transparent: isMac,
		vibrancy: isMac ? "under-window" : undefined,
		visualEffectState: "active",
		webPreferences: {
			preload: path.join(__dirname, "preload.cjs"),
			contextIsolation: true,
			nodeIntegration: false,
			sandbox: false,
		},
	});

	mainWindow.once("ready-to-show", () => {
		if (mainWindow) {
			mainWindow.show();
		}
	});

	mainWindow.webContents.setWindowOpenHandler(({ url }) => {
		void shell.openExternal(url);
		return { action: "deny" };
	});

	mainWindow.on("closed", () => {
		mainWindow = null;
	});

	void mainWindow.loadURL(rendererUrl);

	if (!app.isPackaged && process.env.ARXIV_AGENT_DEVTOOLS === "1") {
		mainWindow.webContents.openDevTools({ mode: "detach" });
	}
}

/**
 * 停止 Python 后端：先尝试优雅关闭（HTTP /api/shutdown），超时后强制 kill。
 * 优先使用 async 版本；同步场景（process.exit）下直接走强制 kill。
 */
function stopPythonBackendSync() {
	if (!pythonProc || pythonProc.killed) {
		return;
	}
	const pid = pythonProc.pid;
	console.log(`[Electron Main] Force-stopping Python backend PID ${pid} (sync).`);
	if (process.platform === "win32") {
		spawn("taskkill", ["/pid", String(pid), "/T", "/F"], {
			stdio: "ignore",
			windowsHide: true,
		});
	} else {
		try {
			pythonProc.kill("SIGKILL");
		} catch {
			// 可能已退出
		}
	}
	pythonProc = null;
	removePidFile();
}

async function stopPythonBackend() {
	// 没有活跃进程 → 检查 PID 文件残留
	if (!pythonProc || pythonProc.killed) {
		const stale = readStalePidFile();
		if (stale?.alive) {
			await cleanupStaleBackend();
		}
		return;
	}

	const pid = pythonProc.pid;
	console.log(`[Electron Main] Stopping Python backend PID ${pid} (graceful first).`);

	// 1. 尝试 HTTP 优雅关闭
	const shutdownOk = await gracefulShutdownBackend();

	if (shutdownOk) {
		// 等待进程自然退出
		const deadline = Date.now() + GRACEFUL_SHUTDOWN_TIMEOUT_MS;
		while (Date.now() < deadline && pythonProc && !pythonProc.killed) {
			await sleep(200);
		}
	}

	// 2. 如果进程还在，强制 kill
	if (pythonProc && !pythonProc.killed) {
		console.log(`[Electron Main] Graceful shutdown timed out, force killing PID ${pid}.`);
		if (process.platform === "win32") {
			spawn("taskkill", ["/pid", String(pid), "/T", "/F"], {
				stdio: "ignore",
				windowsHide: true,
			});
		} else {
			try {
				pythonProc.kill("SIGKILL");
			} catch {
				// 可能已退出
			}
		}
	}

	pythonProc = null;
	currentAuthToken = null;
	removePidFile();
}

app.on("before-quit", () => {
	isQuitting = true;
	void stopPythonBackend();
});

app.on("window-all-closed", () => {
	if (process.platform !== "darwin") {
		app.quit();
	}
});

app.on("activate", () => {
	if (BrowserWindow.getAllWindows().length === 0) {
		void bootstrap();
	}
});

async function bootstrap() {
	const [backendResult, rendererUrl] = await Promise.all([
		startPythonBackend(),
		getRendererUrl(),
	]);

	if (isSmokeTest) {
		const rendererReady = await isRendererAvailable(rendererUrl);
		console.log(JSON.stringify({
			backendReady: backendResult.ok,
			backendError: backendResult.error || null,
			rendererReady,
			rendererUrl,
			packaged: app.isPackaged,
		}));
		stopPythonBackend();
		app.exit(backendResult.ok && rendererReady ? 0 : 1);
		return;
	}

	if (!backendResult.ok) {
		console.warn(`[Electron Main] Backend is not healthy: ${backendResult.error || "未知原因"}。renderer will still open.`);
	}

	createWindow(rendererUrl);
}

void app.whenReady().then(() => {
	registerSecretsHandlers();
	registerBackendHandlers();
	registerAuthHandlers();
	registerWindowHandlers();
	return bootstrap();
});

process.on("exit", () => {
	if (!isQuitting) {
		stopPythonBackendSync();
	}
});

// 兜底：未捕获异常/OOM 等场景也要尝试清理
process.on("uncaughtException", (err) => {
	console.error("[Electron Main] Uncaught exception:", err);
	stopPythonBackendSync();
});
process.on("unhandledRejection", (reason) => {
	console.error("[Electron Main] Unhandled rejection:", reason);
});
