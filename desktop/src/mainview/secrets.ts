/**
 * API Key 的安全存储抽象层。
 *
 * 优先级：
 * 1. Electron 真实环境：经 IPC 调用主进程的 safeStorage（系统加密存储）。
 * 2. 浏览器/开发模式（无 IPC bridge）：回退到 localStorage，仅用于本地开发，
 *    并通过 hasSecretsBridge() 让 UI 显示"未加密"提示。
 *
 * 注意：localStorage 回退只在本机 dev 下出现；打包后的 Electron 一定有 bridge。
 */

const LOCAL_KEY = "arxiv_agent_api_key_dev";

type SecretsBridge = {
  secrets?: {
    get: (key: string) => Promise<string | null>;
    set: (key: string, value: string) => Promise<void>;
    delete: (key: string) => Promise<void>;
    encryptionAvailable: () => Promise<boolean>;
  };
};

function getDesktop(): SecretsBridge | undefined {
  if (typeof window === "undefined") return undefined;
  return (window as unknown as { arxivAgentDesktop?: SecretsBridge }).arxivAgentDesktop;
}

/** 是否存在 Electron safeStorage bridge。 */
export function hasSecretsBridge(): boolean {
  return Boolean(getDesktop()?.secrets);
}

/** 当前是否启用加密存储（仅在有 bridge 时有意义）。 */
export async function encryptionAvailable(): Promise<boolean> {
  const bridge = getDesktop()?.secrets;
  if (!bridge) return false;
  try {
    return await bridge.encryptionAvailable();
  } catch {
    return false;
  }
}

export async function loadApiKey(): Promise<string> {
  const bridge = getDesktop()?.secrets;
  if (bridge) {
    try {
      const value = await bridge.get("api_key");
      return value ?? "";
    } catch (err) {
      console.warn("从 safeStorage 读取 API Key 失败，回退内存", err);
      return "";
    }
  }
  // 开发模式回退
  return localStorage.getItem(LOCAL_KEY) ?? "";
}

export async function saveApiKey(value: string): Promise<void> {
  const bridge = getDesktop()?.secrets;
  if (bridge) {
    await bridge.set("api_key", value);
    // 写入安全存储后，清掉可能残留的开发回退
    localStorage.removeItem(LOCAL_KEY);
    return;
  }
  if (value) {
    localStorage.setItem(LOCAL_KEY, value);
  } else {
    localStorage.removeItem(LOCAL_KEY);
  }
}

export async function clearApiKey(): Promise<void> {
  const bridge = getDesktop()?.secrets;
  if (bridge) {
    try {
      await bridge.delete("api_key");
    } catch (err) {
      console.warn("从 safeStorage 删除 API Key 失败", err);
    }
  }
  localStorage.removeItem(LOCAL_KEY);
}

/**
 * 通用 secret 读取（供 provider 专用 key 等使用）。
 * 有 Electron bridge 走 safeStorage，否则开发模式回退 localStorage。
 */
export async function loadSecret(key: string): Promise<string> {
  const bridge = getDesktop()?.secrets;
  if (bridge) {
    try {
      return (await bridge.get(key)) ?? "";
    } catch (err) {
      console.warn(`从 safeStorage 读取 ${key} 失败，回退空`, err);
      return "";
    }
  }
  return localStorage.getItem(key) ?? "";
}

/**
 * 通用 secret 写入。有 bridge 走 safeStorage，否则开发模式回退 localStorage。
 * 空值表示删除。
 */
export async function saveSecret(key: string, value: string): Promise<void> {
  const bridge = getDesktop()?.secrets;
  if (bridge) {
    if (value) {
      await bridge.set(key, value);
    } else {
      await bridge.delete(key);
    }
    return;
  }
  if (value) {
    localStorage.setItem(key, value);
  } else {
    localStorage.removeItem(key);
  }
}
