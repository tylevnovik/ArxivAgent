import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearApiKey,
  hasSecretsBridge,
  loadApiKey,
  saveApiKey,
} from "./secrets";

const LOCAL_KEY = "arxiv_agent_api_key_dev";

// 每个测试之间清理 window bridge 与 localStorage
beforeEach(() => {
	localStorage.clear();
	window.arxivAgentDesktop = undefined;
});

afterEach(() => {
	vi.restoreAllMocks();
	window.arxivAgentDesktop = undefined;
});

function installBridge(store: Record<string, string>) {
	window.arxivAgentDesktop = {
		platform: "test",
		versions: { electron: "0", chrome: "0", node: "0" },
		secrets: {
			get: vi.fn(async (key: string) => store[key] ?? null),
			set: vi.fn(async (key: string, value: string) => {
				store[key] = value;
			}),
			delete: vi.fn(async (key: string) => {
				delete store[key];
			}),
			encryptionAvailable: vi.fn(async () => true),
		},
	};
	return store;
}

describe("secrets without bridge (dev fallback)", () => {
  it("hasSecretsBridge is false", () => {
    expect(hasSecretsBridge()).toBe(false);
  });

  it("save/load roundtrip via localStorage", async () => {
    await saveApiKey("sk-fallback");
    expect(localStorage.getItem(LOCAL_KEY)).toBe("sk-fallback");
    expect(await loadApiKey()).toBe("sk-fallback");
  });

  it("clear removes from localStorage", async () => {
    await saveApiKey("sk-fallback");
    await clearApiKey();
    expect(localStorage.getItem(LOCAL_KEY)).toBeNull();
  });
});

describe("secrets with safeStorage bridge", () => {
  it("hasSecretsBridge is true when bridge present", () => {
    installBridge({});
    expect(hasSecretsBridge()).toBe(true);
  });

  it("save uses bridge, not localStorage", async () => {
    const store = installBridge({});
    await saveApiKey("sk-secret");
    expect(window.arxivAgentDesktop!.secrets!.set).toHaveBeenCalledWith("api_key", "sk-secret");
    expect(store["api_key"]).toBe("sk-secret");
    // bridge 存在时不应写 localStorage
    expect(localStorage.getItem(LOCAL_KEY)).toBeNull();
  });

  it("load reads from bridge", async () => {
    installBridge({ api_key: "sk-from-bridge" });
    expect(await loadApiKey()).toBe("sk-from-bridge");
    expect(window.arxivAgentDesktop!.secrets!.get).toHaveBeenCalledWith("api_key");
    // 不应触达 localStorage
    expect(localStorage.getItem(LOCAL_KEY)).toBeNull();
  });

  it("clear deletes via bridge", async () => {
    const store = installBridge({ api_key: "sk-x" });
    await clearApiKey();
    expect(window.arxivAgentDesktop!.secrets!.delete).toHaveBeenCalledWith("api_key");
    expect(store["api_key"]).toBeUndefined();
  });
});
