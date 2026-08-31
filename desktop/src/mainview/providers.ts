/**
 * 内置 LLM 供应商预设。
 *
 * 设计目标：用户在配置中心选一个供应商，端点 + 默认模型 + 文档链接自动带出，
 * 只需填 API Key。当前后端真正接通的是 OpenAI-compatible transport；原生协议
 * provider 会明确标记为暂未支持，避免出现“下拉可选但请求必失败”。
 *
 * 模型清单已逐家核对官方文档（2026-06）：
 * - DeepSeek（api-docs.deepseek.com/quick_start/pricing）：
 *     现役 deepseek-v4-flash / deepseek-v4-pro（上下文 1M，输出 384K）。
 *     deepseek-chat / deepseek-reasoner 将于 2026/07/24 弃用，分别映射到
 *     deepseek-v4-flash 的非思考 / 思考模式。
 * - 智谱 GLM（docs.bigmodel.cn/cn/guide/start/model-overview）：
 *     旗舰 glm-5.1，高智能 glm-5 / glm-5-turbo / glm-4.7 / glm-4.6，
 *     高性价比 glm-4.5-air / glm-4.5-airx，超长 glm-4-long，
 *     免费 glm-4.7-flash / glm-4-flash-250414。
 * - Moonshot Kimi（platform.moonshot.cn/docs/api/chat、/docs/pricing/chat）：
 *     代码/Agent 旗舰 kimi-k2.7-code（默认思考模式），多模态 kimi-k2.6，
 *     上一代 kimi-k2.5，经典 moonshot-v1-8k/32k/128k。无 kimi-latest。
 * - 阿里通义千问/百炼（help.aliyun.com/zh/model-studio/getting-started/models，
 *   last-modified 2026-06-02）：旗舰 qwen3.7-max，均衡 qwen3.7-plus，
 *     极速 qwen3.6-flash，长文 qwen-long，代码 qwen3-coder-plus。
 * - OpenAI（developers.openai.com/api/docs/models/all，标注 Latest: GPT-5.5）：
 *     旗舰 gpt-5.5，配 gpt-5.4-mini / gpt-5.4-nano。
 * - Anthropic（platform.claude.com/docs/en/about-claude/models/overview）：
 *     旗舰 claude-opus-4-8，配 claude-sonnet-4-6 / claude-haiku-4-5。
 * - 小米 MiMo（mimo.mi.com、platform.xiaomimimo.com，默认走 Token Plan 国内站）：
 *     旗舰 mimo-v2.5-pro（1M 上下文），配 mimo-v2.5（多模态）。
 *     Token Plan 国内站 base_url: https://token-plan-cn.xiaomimimo.com/v1
 *     按量付费 base_url: https://api.xiaomimimo.com/v1（Key 与 Token Plan 不互通）。
 * - OpenCode Go（opencode.ai/docs/go/）：订阅制聚合网关，OpenAI 兼容。
 *     base_url: https://opencode.ai/zen/go/v1；已实测（2026-08）：
 *     deepseek-v4-flash / glm-5.3 输出干净 JSON 适合本应用；
 *     kimi-k3 强制 temperature=1（本应用固定 0.3，不兼容）；
 *     minimax-m3 思考内容混入正文（报告会被污染）；qwen3.8-max 偏慢。
 *
 * 字段说明：
 * - id：内部标识，存进 AppConfig.provider
 * - label：下拉显示名
 * - endpoint：默认 base_url（OpenAI 兼容）
 * - defaultModel：该供应商常用的默认模型（用户可改）
 * - models：可选的常用模型候选，供下拉快速选
 * - keyHint：API Key 输入框的占位提示（环境变量名）
 * - apiKeyEnv：后端读取的环境变量名（可选）
 * - endpointEnv：后端读取的 endpoint 环境变量名（可选）
 * - requiresApiKey：该端点是否必须有 API Key；本地端点可为 false
 * - docsUrl：获取 API Key 的官方文档链接
 * - note：该供应商的小提示（可选）
 */
export type ProviderPreset = {
	id: string;
	label: string;
	endpoint: string;
	defaultModel: string;
	models: string[];
	keyHint: string;
	apiKeyEnv?: string;
	endpointEnv?: string;
	requiresApiKey: boolean;
	supported: boolean;
	docsUrl: string;
	note?: string;
};

export const PROVIDER_PRESETS: ProviderPreset[] = [
	{
		id: "deepseek",
		label: "DeepSeek（官方）",
		endpoint: "https://api.deepseek.com",
		defaultModel: "deepseek-v4-flash",
		models: ["deepseek-v4-flash", "deepseek-v4-pro"],
		keyHint: "留空则用环境变量 DEEPSEEK_API_KEY",
		apiKeyEnv: "DEEPSEEK_API_KEY",
		endpointEnv: "DEEPSEEK_BASE_URL",
		requiresApiKey: true,
		supported: true,
		docsUrl: "https://platform.deepseek.com/api_keys",
		note: "推荐。国内可直连，性价比高。V4 系列上下文 1M，支持思考/非思考模式（默认思考）。",
	},
	{
		id: "zhipu",
		label: "智谱 GLM",
		endpoint: "https://open.bigmodel.cn/api/paas/v4",
		defaultModel: "glm-4.6",
		models: ["glm-5.1", "glm-5", "glm-4.7", "glm-4.6", "glm-4.5-air", "glm-4.7-flash", "glm-4-long"],
		keyHint: "留空则用环境变量 ZHIPU_API_KEY",
		apiKeyEnv: "ZHIPU_API_KEY",
		endpointEnv: "ZHIPU_API_BASE",
		requiresApiKey: true,
		supported: true,
		docsUrl: "https://open.bigmodel.cn/usercenter/apikeys",
		note: "国内直连。旗舰 GLM-5.1（200K）；GLM-4.6 通用高性价比；glm-4.7-flash 免费可用。",
	},
	{
		id: "moonshot",
		label: "Moonshot Kimi",
		endpoint: "https://api.moonshot.cn/v1",
		defaultModel: "kimi-k2.7-code",
		models: ["kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5", "moonshot-v1-128k", "moonshot-v1-32k"],
		keyHint: "留空则用环境变量 MOONSHOT_API_KEY",
		apiKeyEnv: "MOONSHOT_API_KEY",
		endpointEnv: "MOONSHOT_API_BASE",
		requiresApiKey: true,
		supported: true,
		docsUrl: "https://platform.moonshot.cn/console/api-keys",
		note: "国内直连。旗舰 kimi-k2.7-code（代码/Agent 最强，默认思考）；kimi-k2.6 多模态（256K）。",
	},
	{
		id: "dashscope",
		label: "阿里通义千问（百炼）",
		endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1",
		defaultModel: "qwen-plus",
		models: ["qwen3.7-max", "qwen3.7-plus", "qwen3.6-flash", "qwen-long", "qwen3-coder-plus"],
		keyHint: "留空则用环境变量 DASHSCOPE_API_KEY",
		apiKeyEnv: "DASHSCOPE_API_KEY",
		endpointEnv: "DASHSCOPE_API_BASE",
		requiresApiKey: true,
		supported: true,
		docsUrl: "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
		note: "国内直连，走 DashScope OpenAI 兼容模式。旗舰 qwen3.7-max；qwen3.7-plus 均衡。",
	},
	{
		id: "openai",
		label: "OpenAI",
		endpoint: "https://api.openai.com/v1",
		defaultModel: "gpt-5.5",
		models: ["gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano"],
		keyHint: "留空则用环境变量 OPENAI_API_KEY",
		apiKeyEnv: "OPENAI_API_KEY",
		endpointEnv: "OPENAI_API_BASE",
		requiresApiKey: true,
		supported: true,
		docsUrl: "https://platform.openai.com/api-keys",
		note: "国内通常需要代理。旗舰 gpt-5.5，最适合编程与 Agent 任务。",
	},
	{
		id: "anthropic",
		label: "Anthropic Claude",
		endpoint: "https://api.anthropic.com/v1",
		defaultModel: "claude-sonnet-4-6",
		models: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
		keyHint: "留空则用环境变量 ANTHROPIC_API_KEY",
		apiKeyEnv: "ANTHROPIC_API_KEY",
		endpointEnv: "ANTHROPIC_BASE_URL",
		requiresApiKey: true,
		supported: false,
		docsUrl: "https://console.anthropic.com/settings/keys",
		note: "后端默认走 OpenAI 兼容协议；部分场景可能需额外适配。旗舰 claude-opus-4-8。",
	},
	{
		id: "gemini",
		label: "Google Gemini",
		endpoint: "https://generativelanguage.googleapis.com/v1beta/openai",
		defaultModel: "gemini-2.5-flash",
		models: ["gemini-2.5-flash", "gemini-2.5-pro"],
		keyHint: "留空则用环境变量 GEMINI_API_KEY",
		apiKeyEnv: "GEMINI_API_KEY",
		endpointEnv: "GEMINI_API_BASE",
		requiresApiKey: true,
		supported: false,
		docsUrl: "https://aistudio.google.com/app/apikey",
		note: "已纳入 provider 目录；当前版本仍需 LiteLLM/native transport，暂不可直接调用。",
	},
	{
		id: "mimo",
		label: "小米 MiMo（Token Plan 国内站）",
		endpoint: "https://token-plan-cn.xiaomimimo.com/v1",
		defaultModel: "mimo-v2.5-pro",
		models: ["mimo-v2.5-pro", "mimo-v2.5"],
		keyHint: "留空则用环境变量 MIMO_API_KEY",
		apiKeyEnv: "MIMO_API_KEY",
		endpointEnv: "MIMO_API_BASE",
		requiresApiKey: true,
		supported: true,
		docsUrl: "https://platform.xiaomimimo.com/token-plan",
		note: "国内直连。Token Plan 订阅包（¥39/月起），Key 与按量付费不互通。旗舰 mimo-v2.5-pro（1M 上下文）。",
	},
	{
		id: "opencode-go",
		label: "OpenCode Go（订阅网关）",
		endpoint: "https://opencode.ai/zen/go/v1",
		defaultModel: "deepseek-v4-flash",
		models: [
			"deepseek-v4-flash",
			"deepseek-v4-pro",
			"glm-5.3",
			"glm-5.3-flash",
			"qwen3.8-max",
			"mimo-v2.5-pro",
		],
		keyHint: "填入 OpenCode Go 订阅的 API Key",
		apiKeyEnv: "OPENCODE_GO_API_KEY",
		endpointEnv: "OPENCODE_GO_API_BASE",
		requiresApiKey: true,
		supported: true,
		docsUrl: "https://opencode.ai/docs/go/",
		note: "订阅制聚合网关（一个 Key 用多家模型）。推荐 deepseek-v4-flash（快且 JSON 稳）；kimi-k3 不兼容（强制 temperature=1），minimax 系列思考内容会混入正文。",
	},
	{
		id: "ollama",
		label: "Ollama（本地）",
		endpoint: "http://localhost:11434/v1",
		defaultModel: "llama3.2",
		models: [],
		keyHint: "本地 Ollama 不需要 API Key",
		apiKeyEnv: "OLLAMA_API_KEY",
		endpointEnv: "OLLAMA_API_BASE",
		requiresApiKey: false,
		supported: true,
		docsUrl: "https://ollama.com/",
		note: "默认连接 localhost:11434/v1；请先启动 Ollama 并下载模型。",
	},
	{
		id: "vllm",
		label: "vLLM（本地）",
		endpoint: "http://localhost:8000/v1",
		defaultModel: "",
		models: [],
		keyHint: "本地 vLLM 通常不需要 API Key",
		apiKeyEnv: "VLLM_API_KEY",
		endpointEnv: "VLLM_API_BASE",
		requiresApiKey: false,
		supported: true,
		docsUrl: "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
		note: "默认连接 localhost:8000/v1；模型名称需与 vLLM 的 --served-model-name 一致。",
	},
	{
		id: "custom",
		label: "自定义 (OpenAI 兼容)",
		endpoint: "",
		defaultModel: "",
		models: [],
		keyHint: "可选：按端点要求填写 API Key",
		apiKeyEnv: "CUSTOM_API_KEY",
		endpointEnv: "CUSTOM_API_BASE",
		requiresApiKey: false,
		supported: true,
		docsUrl: "",
		note: "任何兼容 OpenAI 协议的端点（本地 Ollama / vLLM / LM Studio 等）。",
	},
];

/** 按 id 查预设，未匹配时回退到 custom。 */
export function getPreset(id: string): ProviderPreset {
	return (
		PROVIDER_PRESETS.find((p) => p.id === id) ??
		PROVIDER_PRESETS[PROVIDER_PRESETS.length - 1]
	);
}

/**
 * 只把用户真正改过的 endpoint/model 发给后端。
 * 预设默认值留空后，provider 自己的环境变量才能按后端规则生效。
 */
export function getProviderRequestOverrides(
	provider: string,
	endpoint: string,
	model: string,
): { base_url?: string; model?: string } {
	const preset = getPreset(provider);
	const normalizedEndpoint = endpoint.trim().replace(/\/+$/, "");
	const normalizedPresetEndpoint = preset.endpoint.trim().replace(/\/+$/, "");
	return {
		base_url:
			normalizedEndpoint && normalizedEndpoint !== normalizedPresetEndpoint
				? normalizedEndpoint
				: undefined,
		model: model.trim() && model.trim() !== preset.defaultModel ? model.trim() : undefined,
	};
}
