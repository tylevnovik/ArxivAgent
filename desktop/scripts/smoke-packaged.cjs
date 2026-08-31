const fs = require("node:fs");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const desktopRoot = path.resolve(__dirname, "..");
const executable = process.platform === "win32"
	? path.join(desktopRoot, "build", "electron", "win-unpacked", "ArxivAgent.exe")
	: path.join(desktopRoot, "build", "electron", "linux-unpacked", "ArxivAgent");
const timeoutMs = 60000;

function killProcessTree(pid) {
	if (!pid) return;
	if (process.platform === "win32") {
		spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], {
			stdio: "ignore",
			windowsHide: true,
		});
		return;
	}
	try {
		process.kill(-pid, "SIGKILL");
	} catch {
		try { process.kill(pid, "SIGKILL"); } catch { /* already exited */ }
	}
}

if (!fs.existsSync(executable)) {
	console.error(`[packaged-smoke] Missing executable: ${executable}`);
	process.exit(1);
}

let stdout = "";
let stderr = "";
let settled = false;
const child = spawn(executable, ["--smoke-test"], {
	cwd: desktopRoot,
	stdio: ["ignore", "pipe", "pipe"],
	windowsHide: true,
});

child.stdout?.on("data", (chunk) => { stdout += chunk.toString(); });
child.stderr?.on("data", (chunk) => { stderr += chunk.toString(); });

const timer = setTimeout(() => {
	if (settled) return;
	settled = true;
	console.error(`[packaged-smoke] Timed out after ${timeoutMs}ms (PID ${child.pid}).`);
	killProcessTree(child.pid);
	if (stdout) process.stdout.write(stdout);
	if (stderr) process.stderr.write(stderr);
	process.exit(1);
}, timeoutMs);

child.once("error", (error) => {
	if (settled) return;
	settled = true;
	clearTimeout(timer);
	console.error(`[packaged-smoke] Failed to start: ${error.message}`);
	process.exit(1);
});

child.once("exit", (code, signal) => {
	if (settled) return;
	settled = true;
	clearTimeout(timer);
	if (stdout) process.stdout.write(stdout);
	if (stderr) process.stderr.write(stderr);
	if (code === 0) {
		console.log(`[packaged-smoke] Passed (exit 0).`);
		return;
	}
	console.error(`[packaged-smoke] Failed (exit ${code}, signal ${signal || "none"}).`);
	process.exit(1);
});
