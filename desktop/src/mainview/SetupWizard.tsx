/**
 * 首屏引导向导。两种触发场景共用：
 *
 * 1. 后端环境缺失（Python/依赖未装）：展示诊断结果 + uv 恢复命令 + 重启按钮。
 *    由 App 检测 backend.diagnose() 不通过时打开。
 * 2. API Key 未配置：展示 LLM 供应商快速配置面板，用户选供应商 + 填 Key + 测试 + 保存。
 *    由 App 检测 secrets 无 key 且 has_api_key 标志缺失时打开。
 *
 * 两部分共存（用分区展示），用户按需配置。
 */
import { useEffect, useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  TextField,
  Typography,
} from "@mui/material";
import {
  Refresh as RefreshIcon,
  CheckCircle as CheckCircleIcon,
  Cancel as CancelIcon,
  ContentCopy as ContentCopyIcon,
  OpenInNew as OpenInNewIcon,
  AutoAwesome as AutoAwesomeIcon,
} from "@mui/icons-material";
import type { AppConfig, ConfigHealth } from "./api";
import { getConfigHealth } from "./api";
import { PROVIDER_PRESETS, getPreset } from "./providers";
import { hasSecretsBridge, saveApiKey } from "./secrets";

type Diagnosis = {
  ok: boolean;
  healthy: boolean;
  python: { path: string; source: string } | null;
  missing: string[];
  error: string | null;
  backend_dir?: string;
};

export function SetupWizard({
  open,
  onClose,
  config,
  setConfig,
}: {
  open: boolean;
  onClose: () => void;
  config: AppConfig;
  setConfig: React.Dispatch<React.SetStateAction<AppConfig>>;
}) {
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [loading, setLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  // LLM 配置面板
  const [health, setHealth] = useState<ConfigHealth | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const runDiagnose = async () => {
    setLoading(true);
    try {
      const d = (await window.arxivAgentDesktop?.backend?.diagnose()) as Diagnosis;
      setDiagnosis(d ?? null);
    } catch (err) {
      console.warn("diagnose failed", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) runDiagnose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      const r = await window.arxivAgentDesktop?.backend?.retry();
      if (r?.ok) {
        await runDiagnose();
      }
    } finally {
      setRetrying(false);
    }
  };

  const copy = (text: string, label: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(label);
      setTimeout(() => setCopied(null), 1500);
    });
  };

  const runTest = async () => {
    setTesting(true);
    setHealth(null);
    try {
      const result = await getConfigHealth({
        api_key: config.apiKey,
        base_url: config.endpoint,
        model: config.modelName,
        provider: config.provider,
        providers: config.providers,
        ping_llm: true,
      });
      setHealth(result);
    } catch (err) {
      const e = err as Error;
      setHealth({
        ok: false,
        api_key_configured: Boolean(config.apiKey),
        api_key_source: config.apiKey ? "request" : "none",
        provider: config.provider,
        endpoint: config.endpoint,
        model: config.modelName,
        data_dir: "",
        llm_reachable: false,
        llm_detail: e?.message || "测试失败",
        providers: [],
        encryption_available: null,
      });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      // 持久化非敏感字段
      localStorage.setItem("arxiv_agent_provider", config.provider);
      localStorage.setItem("arxiv_agent_endpoint", config.endpoint);
      localStorage.setItem("arxiv_agent_model_name", config.modelName);
      localStorage.setItem("arxiv_agent_has_api_key", config.apiKey ? "1" : "0");
      // API Key 走安全存储
      try {
        await saveApiKey(config.apiKey);
      } catch (err) {
        console.warn("保存 API Key 失败", err);
      }
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const commands = [
    { label: "安装 uv", cmd: "pip install uv" },
    { label: "安装 Python 3.12 + 同步依赖", cmd: "uv python install 3.12 && uv sync" },
    { label: "仅同步依赖", cmd: "uv sync" },
  ];

  // 判断当前主要问题，决定分区顺序与高亮
  const envBroken =
    diagnosis && !diagnosis.ok && !diagnosis.healthy;
  const keyMissing = !config.apiKey;

  return (
    <Dialog
      open={open}
      onClose={() => {}}
      fullScreen
      scroll="paper"
      slotProps={{
        paper: {
          sx: {
            bgcolor: "rgba(10,10,12,0.92)",
            backdropFilter: "blur(24px)",
          },
        },
      }}
    >
      <DialogTitle sx={{ fontWeight: 760, display: "flex", alignItems: "center", gap: 1 }}>
        <AutoAwesomeIcon sx={{ color: "#9cc8f5" }} />
        欢迎使用 ArxivAgent · 初始配置
      </DialogTitle>
      <DialogContent dividers sx={{ overflowY: "auto", overscrollBehavior: "contain" }}>
        <Box sx={{ maxWidth: 720, mx: "auto", py: 2 }}>
          <Typography sx={{ mb: 3, color: "rgba(255,255,255,0.7)", lineHeight: 1.6 }}>
            完成以下两步即可开始检索论文。配置一次，后续自动记忆。
          </Typography>

          {/* ============ Step 1: LLM 供应商 + API Key ============ */}
          <SectionHeader step={1} title="配置 LLM 供应商与 API Key" required={keyMissing} />
          <Box
            sx={{
              p: 2.5,
              mb: 3,
              borderRadius: 2,
              bgcolor: keyMissing ? "rgba(248,113,113,0.05)" : "rgba(74,222,128,0.05)",
              border: `1px solid ${keyMissing ? "rgba(248,113,113,0.3)" : "rgba(74,222,128,0.3)"}`,
            }}
          >
            {keyMissing ? (
              <Typography sx={{ mb: 2, fontSize: 13, color: "#fca5a5" }}>
                尚未配置 API Key，请先选一个供应商并填入 Key。
              </Typography>
            ) : (
              <Typography sx={{ mb: 2, fontSize: 13, color: "#86efac" }}>
                ✓ 已检测到 API Key。可在此调整供应商或测试连接。
              </Typography>
            )}

            {/* 供应商下拉 */}
            <FormControl size="small" fullWidth sx={{ mb: 2 }}>
              <InputLabel>LLM 供应商</InputLabel>
              <Select
                value={config.provider}
                label="LLM 供应商"
                onChange={(e) => {
                  const preset = getPreset(String(e.target.value));
                  setConfig((prev) => ({
                    ...prev,
                    provider: preset.id,
                    endpoint: preset.endpoint,
                    modelName: preset.defaultModel,
                  }));
                }}
              >
                {PROVIDER_PRESETS.map((p) => (
                  <MenuItem key={p.id} value={p.id}>
                    {p.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {/* 当前预设提示 + 文档链接 */}
            <Box sx={{ mb: 2 }}>
              {(() => {
                const preset = getPreset(config.provider);
                return (
                  <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
                    {preset.note && (
                      <Typography variant="caption" sx={{ color: "rgba(255,255,255,0.6)" }}>
                        {preset.note}
                      </Typography>
                    )}
                    {preset.docsUrl && (
                      <Button
                        size="small"
                        href={preset.docsUrl}
                        target="_blank"
                        rel="noreferrer"
                        startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
                        sx={{
                          alignSelf: "flex-start",
                          textTransform: "none",
                          fontSize: 12,
                          color: "#9cc8f5",
                          minHeight: 24,
                          px: 0,
                        }}
                      >
                        获取 API Key →
                      </Button>
                    )}
                  </Box>
                );
              })()}
            </Box>

            {/* API Key */}
            <TextField
              label="API Key"
              size="small"
              type="password"
              fullWidth
              value={config.apiKey}
              onChange={(e) => setConfig((prev) => ({ ...prev, apiKey: e.target.value }))}
              placeholder={getPreset(config.provider).keyHint}
              sx={{ mb: 2 }}
            />
            <Typography variant="caption" sx={{ display: "block", mb: 2, color: "rgba(255,255,255,0.45)" }}>
              {hasSecretsBridge()
                ? "Key 将加密存于系统安全存储。"
                : "开发模式：Key 仅存内存。"}
            </Typography>

            {/* 端点 + 模型（折叠，默认用预设即可） */}
            <TextField
              label="API 端点 (Base URL)"
              size="small"
              fullWidth
              value={config.endpoint}
              onChange={(e) => setConfig((prev) => ({ ...prev, endpoint: e.target.value }))}
              sx={{ mb: 2 }}
            />
            {(() => {
              const preset = getPreset(config.provider);
              if (preset.models.length === 0) {
                return (
                  <TextField
                    label="模型名称"
                    size="small"
                    fullWidth
                    value={config.modelName}
                    onChange={(e) => setConfig((prev) => ({ ...prev, modelName: e.target.value }))}
                    sx={{ mb: 2 }}
                  />
                );
              }
              const options =
                config.modelName && !preset.models.includes(config.modelName)
                  ? [config.modelName, ...preset.models]
                  : preset.models;
              return (
                <Autocomplete
                  size="small"
                  freeSolo
                  fullWidth
                  value={config.modelName}
                  onChange={(_e, val) => setConfig((prev) => ({ ...prev, modelName: val ?? "" }))}
                  onInputChange={(_e, val) => setConfig((prev) => ({ ...prev, modelName: val }))}
                  options={options}
                  renderInput={(params) => (
                    <TextField {...params} label="模型" size="small" />
                  )}
                  sx={{ mb: 2 }}
                />
              );
            })()}

            {/* 测试连接 */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap" }}>
              <Button
                variant="outlined"
                size="small"
                startIcon={testing ? <CircularProgress size={14} /> : <CheckCircleIcon />}
                onClick={runTest}
                disabled={testing}
              >
                {testing ? "测试中…" : "测试连接"}
              </Button>
              {health && (
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                  {health.llm_reachable === true ? (
                    <CheckCircleIcon sx={{ fontSize: 16, color: "#4ade80" }} />
                  ) : (
                    <CancelIcon sx={{ fontSize: 16, color: "#f87171" }} />
                  )}
                  <Typography variant="caption" sx={{ color: "rgba(255,255,255,0.7)" }}>
                    {health.llm_reachable === true
                      ? `模型可达 (${health.model})`
                      : health.llm_reachable === false
                        ? `不可达: ${health.llm_detail}`
                        : "未测试模型"}
                  </Typography>
                </Box>
              )}
            </Box>
          </Box>

          {/* ============ Step 2: 后端环境（仅当诊断不通过时高亮） ============ */}
          <SectionHeader step={2} title="后端运行环境（Python）" required={Boolean(envBroken)} />
          <Box
            sx={{
              p: 2.5,
              mb: 3,
              borderRadius: 2,
              bgcolor: envBroken ? "rgba(248,113,113,0.05)" : "rgba(255,255,255,0.03)",
              border: `1px solid ${envBroken ? "rgba(248,113,113,0.3)" : "rgba(255,255,255,0.08)"}`,
            }}
          >
            {/* 诊断结果 */}
            <Box sx={{ mb: 2 }}>
              <Typography sx={{ fontWeight: 700, mb: 1 }}>诊断结果</Typography>
              {loading && !diagnosis ? (
                <CircularProgress size={20} />
              ) : diagnosis ? (
                <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
                  <DiagRow
                    ok={diagnosis.healthy}
                    label={diagnosis.healthy ? "后端健康（正在运行）" : "后端未运行"}
                  />
                  <DiagRow
                    ok={Boolean(diagnosis.python)}
                    label={
                      diagnosis.python
                        ? `Python: ${diagnosis.python.path}（来源：${diagnosis.python.source}）`
                        : "未找到 Python 解释器"
                    }
                  />
                  {diagnosis.missing.length > 0 && (
                    <Box>
                      <DiagRow ok={false} label="缺失依赖模块：" />
                      <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", ml: 3, mt: 0.5 }}>
                        {diagnosis.missing.map((m) => (
                          <Chip
                            key={m}
                            label={m}
                            size="small"
                            sx={{
                              height: 20,
                              fontSize: 11,
                              bgcolor: "rgba(248,113,113,0.16)",
                              color: "#fca5a5",
                            }}
                          />
                        ))}
                      </Box>
                    </Box>
                  )}
                  {diagnosis.error && (
                    <Typography sx={{ fontSize: 12, color: "#fca5a5", mt: 0.5, whiteSpace: "pre-wrap" }}>
                      错误：{diagnosis.error}
                    </Typography>
                  )}
                  {diagnosis.backend_dir && (
                    <Typography sx={{ fontSize: 11, color: "rgba(255,255,255,0.4)", mt: 0.5 }}>
                      后端代码目录：{diagnosis.backend_dir}
                    </Typography>
                  )}
                </Box>
              ) : (
                <Typography sx={{ color: "rgba(255,255,255,0.5)" }}>
                  {window.arxivAgentDesktop?.backend
                    ? "点击下方重新诊断。"
                    : "当前为浏览器/开发模式，跳过后端环境检查。"}
                </Typography>
              )}
            </Box>

            {/* uv 恢复命令（仅环境有问题时展示） */}
            {envBroken && window.arxivAgentDesktop?.backend && (
              <>
                <Divider sx={{ my: 1.5 }} />
                <Typography sx={{ fontWeight: 700, mb: 1 }}>恢复步骤（推荐用 uv）</Typography>
                <Typography sx={{ fontSize: 13, color: "rgba(255,255,255,0.6)", mb: 1.5 }}>
                  在后端代码目录打开终端，依次执行：
                </Typography>
                <Box sx={{ display: "flex", flexDirection: "column", gap: 1, mb: 2 }}>
                  {commands.map((c) => (
                    <Box
                      key={c.label}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        p: 1,
                        borderRadius: 1.5,
                        bgcolor: "#0a0a0a",
                        border: "1px solid rgba(255,255,255,0.1)",
                      }}
                    >
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography sx={{ fontSize: 11, color: "rgba(255,255,255,0.45)" }}>
                          {c.label}
                        </Typography>
                        <Typography
                          sx={{
                            fontFamily: "monospace",
                            fontSize: 13,
                            color: "#9cc8f5",
                            wordBreak: "break-all",
                          }}
                        >
                          {c.cmd}
                        </Typography>
                      </Box>
                      <IconButton
                        size="small"
                        onClick={() => copy(c.cmd, c.label)}
                        sx={{ color: "rgba(255,255,255,0.6)" }}
                      >
                        <ContentCopyIcon sx={{ fontSize: 16 }} />
                      </IconButton>
                      {copied === c.label && (
                        <Typography sx={{ fontSize: 11, color: "#4ade80" }}>已复制</Typography>
                      )}
                    </Box>
                  ))}
                </Box>
                <Box sx={{ display: "flex", gap: 1 }}>
                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={loading ? <CircularProgress size={14} /> : <RefreshIcon />}
                    onClick={runDiagnose}
                    disabled={loading}
                  >
                    重新诊断
                  </Button>
                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={retrying ? <CircularProgress size={14} /> : <RefreshIcon />}
                    onClick={handleRetry}
                    disabled={retrying}
                  >
                    {retrying ? "重启中…" : "我已配置，重启后端"}
                  </Button>
                </Box>
              </>
            )}
          </Box>

          {/* ============ 底部操作 ============ */}
          <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 1, mt: 2 }}>
            <Button onClick={onClose} sx={{ color: "rgba(255,255,255,0.5)" }}>
              稍后配置
            </Button>
            <Button
              variant="contained"
              startIcon={saving ? <CircularProgress size={16} /> : undefined}
              onClick={handleSave}
              disabled={saving || keyMissing}
            >
              保存并开始
            </Button>
          </Box>
        </Box>
      </DialogContent>
    </Dialog>
  );
}

const SectionHeader = ({
  step,
  title,
  required,
}: {
  step: number;
  title: string;
  required: boolean;
}) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5 }}>
    <Box
      sx={{
        width: 24,
        height: 24,
        borderRadius: "50%",
        bgcolor: required ? "#f87171" : "#4ade80",
        color: "#050505",
        display: "grid",
        placeItems: "center",
        fontWeight: 800,
        fontSize: 13,
      }}
    >
      {step}
    </Box>
    <Typography sx={{ fontWeight: 760, fontSize: 16 }}>{title}</Typography>
    {required && (
      <Typography variant="caption" sx={{ color: "#fca5a5" }}>
        必填
      </Typography>
    )}
  </Box>
);

const DiagRow = ({ ok, label }: { ok: boolean; label: string }) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
    {ok ? (
      <CheckCircleIcon sx={{ fontSize: 16, color: "#4ade80" }} />
    ) : (
      <CancelIcon sx={{ fontSize: 16, color: "#f87171" }} />
    )}
    <Typography sx={{ fontSize: 13, color: "rgba(255,255,255,0.75)" }}>{label}</Typography>
  </Box>
);
