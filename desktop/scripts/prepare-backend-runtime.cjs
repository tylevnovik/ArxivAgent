const fs = require("node:fs");
const path = require("node:path");

const desktopRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(desktopRoot, "..");
const venvRoot = path.join(repoRoot, ".venv");
const pyvenvCfg = path.join(venvRoot, "pyvenv.cfg");
const sitePackagesSource = path.join(venvRoot, "Lib", "site-packages");
const outputRoot = path.join(desktopRoot, ".backend-runtime");
const pythonOutput = path.join(outputRoot, "python");
const sitePackagesOutput = path.join(outputRoot, "site-packages");

function assertInside(parent, child) {
	const parentPath = path.resolve(parent);
	const childPath = path.resolve(child);
	if (childPath !== parentPath && !childPath.startsWith(parentPath + path.sep)) {
		throw new Error(`Refusing to operate outside ${parentPath}: ${childPath}`);
	}
}

function readPythonHome() {
	if (process.env.ARXIV_AGENT_PYTHON_RUNTIME) {
		return path.resolve(process.env.ARXIV_AGENT_PYTHON_RUNTIME);
	}

	if (!fs.existsSync(pyvenvCfg)) {
		throw new Error(`Missing ${pyvenvCfg}. Create the backend venv before packaging.`);
	}

	const cfg = fs.readFileSync(pyvenvCfg, "utf-8");
	const homeLine = cfg.split(/\r?\n/).find((line) => line.toLowerCase().startsWith("home ="));
	if (!homeLine) {
		throw new Error(`Could not find "home =" in ${pyvenvCfg}.`);
	}

	return path.resolve(homeLine.slice(homeLine.indexOf("=") + 1).trim());
}

function copyDir(source, target, options = {}) {
	if (!fs.existsSync(source)) {
		throw new Error(`Missing source directory: ${source}`);
	}
	const excludedRuntimeSitePackages = options.excludeRuntimeSitePackages
		? path.join(path.resolve(source), "Lib", "site-packages")
		: null;

	fs.cpSync(source, target, {
		recursive: true,
		force: true,
		dereference: true,
		filter: (src) => {
			if (excludedRuntimeSitePackages) {
				const resolved = path.resolve(src);
				if (
					resolved === excludedRuntimeSitePackages
					|| resolved.startsWith(excludedRuntimeSitePackages + path.sep)
				) {
					return false;
				}
			}
			const name = path.basename(src);
			if (name === "__pycache__") return false;
			if (name.endsWith(".pyc") || name.endsWith(".pyo")) return false;
			return true;
		},
	});
}

function main() {
	const pythonHome = readPythonHome();
	const pythonExe = process.platform === "win32"
		? path.join(pythonHome, "python.exe")
		: path.join(pythonHome, "bin", "python3");
	if (!fs.existsSync(pythonExe)) {
		throw new Error(`Python runtime does not contain an interpreter: ${pythonExe}`);
	}
	if (!fs.existsSync(sitePackagesSource)) {
		throw new Error(`Backend site-packages not found: ${sitePackagesSource}`);
	}

	assertInside(desktopRoot, outputRoot);
	fs.rmSync(outputRoot, { recursive: true, force: true });
	fs.mkdirSync(outputRoot, { recursive: true });

	console.log(`[prepare-backend-runtime] Python runtime: ${pythonHome}`);
	console.log(`[prepare-backend-runtime] Site packages: ${sitePackagesSource}`);
	copyDir(pythonHome, pythonOutput, { excludeRuntimeSitePackages: true });
	copyDir(sitePackagesSource, sitePackagesOutput);

	const manifest = {
		generated_at: new Date().toISOString(),
		python_home: pythonHome,
		site_packages: sitePackagesSource,
	};
	fs.writeFileSync(path.join(outputRoot, "manifest.json"), JSON.stringify(manifest, null, 2));
	console.log(`[prepare-backend-runtime] Wrote ${outputRoot}`);
}

main();
