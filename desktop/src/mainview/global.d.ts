// Electron preload 暴露的全局接口类型（见 src/electron/preload.cjs）
export {};

declare global {
	interface Window {
		arxivAgentDesktop?: {
			platform: string;
			versions: { electron: string; chrome: string; node: string };
			secrets?: {
				get: (key: string) => Promise<string | null>;
				set: (key: string, value: string) => Promise<void>;
				delete: (key: string) => Promise<void>;
				encryptionAvailable: () => Promise<boolean>;
			};
			backend?: {
				diagnose: () => Promise<BackendDiagnosis>;
				retry: () => Promise<{ ok: boolean }>;
			};
			windowControls?: {
				minimize: () => void;
				toggleMaximize: () => void;
				close: () => void;
			};
		};
	}
}

export type BackendDiagnosis = {
	ok: boolean;
	healthy: boolean;
	python: { path: string; source: string } | null;
	missing: string[];
	error: string | null;
	backend_dir?: string;
};
