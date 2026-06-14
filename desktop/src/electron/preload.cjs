const { contextBridge, ipcRenderer } = require("electron");

/**
 * 渲染进程 <-> 主进程 的唯一 IPC 接口。
 *
 * secrets: API Key 走主进程的 safeStorage（系统加密存储）。
 * backend: 后端启动诊断与重试（打包版环境向导用）。
 *
 * 主进程 handler 见 main.cjs。
 */
contextBridge.exposeInMainWorld("arxivAgentDesktop", {
	platform: process.platform,
	versions: {
		electron: process.versions.electron,
		chrome: process.versions.chrome,
		node: process.versions.node,
	},
	secrets: {
		get: (key) => ipcRenderer.invoke("secrets:get", key),
		set: (key, value) => ipcRenderer.invoke("secrets:set", key, value),
		delete: (key) => ipcRenderer.invoke("secrets:delete", key),
		encryptionAvailable: () => ipcRenderer.invoke("secrets:encryptionAvailable"),
	},
	backend: {
		diagnose: () => ipcRenderer.invoke("backend:diagnose"),
		retry: () => ipcRenderer.invoke("backend:retry"),
	},
	windowControls: {
		minimize: () => ipcRenderer.send("window:minimize"),
		toggleMaximize: () => ipcRenderer.send("window:toggleMaximize"),
		close: () => ipcRenderer.send("window:close"),
	},
});
