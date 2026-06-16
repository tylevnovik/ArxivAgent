import { useCallback, useEffect, useRef, useState } from "react";
import {
  ThemeProvider,
  createTheme,
  CssBaseline,
  Box,
  Typography,
  Button,
  IconButton,
  Card,
  CardContent,
  Chip,
  Tabs,
  Tab,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Checkbox,
  FormControlLabel,
  FormGroup,
  Tooltip,
  Divider,
  Snackbar,
  Alert,
  Autocomplete,
  LinearProgress,
  List,
  ListItemButton,
  ListItemText,
  TextField,
} from "@mui/material";
import {
  Settings as SettingsIcon,
  Delete as DeleteIcon,
  OpenInNew as OpenInNewIcon,
  ContentCopy as ContentCopyIcon,
  Article as ArticleIcon,
  MenuBook as MenuBookIcon,
  FolderZip as FolderZipIcon,
  ExpandMore as ExpandMoreIcon,
  ArrowUpward as ArrowUpwardIcon,
  Stop as StopIcon,
  Add as AddIcon,
  KeyboardArrowDown as KeyboardArrowDownIcon,
  IosShare as IosShareIcon,
  ViewSidebar as ViewSidebarIcon,
  Search as SearchIcon,
  AutoAwesome as AutoAwesomeIcon,
  DataObject as DataObjectIcon,
  Edit as EditIcon,
  BarChart as BarChartIcon,
  TipsAndUpdates as TipsAndUpdatesIcon,
  Close as CloseIcon,
  CheckCircle as CheckCircleIcon,
  Cancel as CancelIcon,
  Refresh as RefreshIcon,
  Minimize as MinimizeIcon,
  CropSquare as CropSquareIcon,
} from "@mui/icons-material";
import {
  ThreadPrimitive,
  ComposerPrimitive,
  useExternalStoreRuntime,
  AssistantRuntimeProvider,
  type TextMessagePart,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import {
  type AppConfig,
  type ChatMessage,
  type ConfigHealth,
  type EvidenceChunk,
  type ExportType,
  type Paper,
  type ThreadDetail,
  type ThreadMeta,
  type ThreadStatus,
  cancelThread,
  createThread,
  deleteThreadMessage,
  deleteThread,
  downloadExport,
  exportThread,
  getConfigHealth,
  getThread,
  listThreads,
  renameThread,
  streamThreadMessage,
  updateThreadMessage,
} from "./api";
import { applyEvent, withUserMessage } from "./eventReducer";
import { clearApiKey, hasSecretsBridge, loadApiKey, loadSecret, saveApiKey, saveSecret } from "./secrets";
import { renderMarkdown } from "./markdown";
import { extractCitations, matchEvidence } from "./citations-core";
import { SetupWizard } from "./SetupWizard";
import { PROVIDER_PRESETS, getPreset } from "./providers";

const DEFAULT_PROVIDER = "deepseek";
const DEFAULT_ENDPOINT = "https://api.deepseek.com";
const DEFAULT_MODEL = "deepseek-v4-flash";

const suggestionPrompts = [
  {
    label: "最新综述",
    icon: MenuBookIcon,
    prompt: "帮我找 2024-2026 年关于大语言模型对齐的高质量综述和代表性论文",
  },
  {
    label: "代码优先",
    icon: DataObjectIcon,
    prompt: "检索带开源代码的多模态大模型训练与评测论文，按实用价值排序",
  },
  {
    label: "写综述",
    icon: EditIcon,
    prompt: "围绕 AI Agent 论文检索与知识管理，整理一个可写综述的研究脉络",
  },
  {
    label: "高引用",
    icon: BarChartIcon,
    prompt: "查找近五年 RAG 方向高引用论文，并总结它们的技术路线差异",
  },
  {
    label: "找空白",
    icon: TipsAndUpdatesIcon,
    prompt: "帮我找 GUI Agent 研究里的开放问题、未解决难点和潜在选题",
  },
];

const STATUS_LABEL: Record<ThreadStatus, string> = {
  idle: "就绪",
  running: "检索中",
  done: "完成",
  error: "出错",
  cancelled: "已取消",
};

function App() {
  const theme = createTheme({
    palette: {
      mode: "dark",
      primary: { main: "#f5f5f5" },
      secondary: { main: "#a3a3a3" },
      background: { default: "#050505", paper: "#101010" },
      divider: "rgba(255,255,255,0.1)",
    },
    typography: {
      fontFamily: "'Inter', 'Noto Sans SC', system-ui, sans-serif",
      button: { textTransform: "none", fontWeight: 650 },
    },
    shape: { borderRadius: 12 },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          html: { backgroundColor: "transparent" },
          body: { backgroundColor: "transparent" },
          "#root": { backgroundColor: "transparent" },
        },
      },
      MuiDialog: {
        styleOverrides: {
          paper: {
            backgroundImage: "none",
            border: "1px solid rgba(255,255,255,0.12)",
          },
        },
      },
    },
  });

  // ---------- 配置（localStorage 存非敏感字段；API Key 走 secrets） ----------
  const [config, setConfig] = useState<AppConfig>(() => ({
    provider: localStorage.getItem("arxiv_agent_provider") || DEFAULT_PROVIDER,
    endpoint: localStorage.getItem("arxiv_agent_endpoint") || DEFAULT_ENDPOINT,
    apiKey: "",
    modelName: localStorage.getItem("arxiv_agent_model_name") || DEFAULT_MODEL,
    maxSearchRounds: parseInt(localStorage.getItem("arxiv_agent_max_search_rounds") || "3", 10),
    maxResultsPerRound: parseInt(
      localStorage.getItem("arxiv_agent_max_results_per_round") || "10",
      10,
    ),
    providers: (localStorage.getItem("arxiv_agent_providers") || "arxiv,openalex,crossref").split(
      ",",
    ),
    openalexMailto: localStorage.getItem("arxiv_agent_openalex_mailto") || "",
    crossrefMailto: localStorage.getItem("arxiv_agent_crossref_mailto") || "",
    // Semantic Scholar key 是敏感字段，走 secrets bridge（异步加载，初始空）
    semanticScholarApiKey: "",
  }));
  const [apiKeyReady, setApiKeyReady] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [systemHealth, setSystemHealth] = useState<ConfigHealth | null>(null);

  // 拉取一次系统健康（后端版本/Key 状态/数据目录），供第一屏状态条展示。
  useEffect(() => {
    getConfigHealth({ ping_llm: false })
      .then((h) => setSystemHealth(h))
      .catch((err) => console.warn("拉取系统健康失败", err));
  }, []);

  // 启动时诊断后端：仅在 Electron 真实环境（有 backend bridge）检查；
  // 浏览器开发模式跳过（依赖 mock/真实后端自行处理）。
  useEffect(() => {
    const bridge = window.arxivAgentDesktop;
    if (!bridge?.backend) return;
    let cancelled = false;
    (async () => {
      try {
        const d = (await bridge.backend!.diagnose()) as {
          ok: boolean;
          healthy: boolean;
        };
        if (!cancelled && !d.ok && !d.healthy) {
          setSetupOpen(true);
        }
      } catch (err) {
        console.warn("后端诊断失败", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 启动时从 secrets 加载 API Key 与 provider secret；若未配置过则开首屏引导
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let key = "";
      let semanticScholarApiKey = "";
      try {
        [key, semanticScholarApiKey] = await Promise.all([loadApiKey(), loadSecret("semantic_scholar_api_key")]);
      } catch (err) {
        console.warn("加载 API Key 失败", err);
      }
      if (cancelled) return;
      setConfig((prev) => ({ ...prev, apiKey: key, semanticScholarApiKey }));

      // 一次性迁移：旧版本把 Semantic Scholar key 明文存 localStorage，迁移到 secrets 后清掉。
      const legacyS2 = localStorage.getItem("arxiv_agent_semantic_scholar_api_key");
      if (legacyS2 && !semanticScholarApiKey) {
        try {
          await saveSecret("semantic_scholar_api_key", legacyS2);
          setConfig((prev) => ({ ...prev, semanticScholarApiKey: legacyS2 }));
          semanticScholarApiKey = legacyS2;
        } catch (err) {
          console.warn("迁移 Semantic Scholar Key 到安全存储失败", err);
        }
      }
      localStorage.removeItem("arxiv_agent_semantic_scholar_api_key");

      setApiKeyReady(true);
      // 未配置过 Key（secrets 与 has_api_key 标志都无）→ 开引导
      const configured = Boolean(key) || localStorage.getItem("arxiv_agent_has_api_key") === "1";
      if (!configured) {
        setSetupOpen(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ---------- 线程状态 ----------
  const [threads, setThreads] = useState<ThreadMeta[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [activeThread, setActiveThread] = useState<ThreadDetail | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [statusText, setStatusText] = useState("就绪，等待输入...");
  const [activeTab, setActiveTab] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [researchOpen, setResearchOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [toast, setToast] = useState<{
    message: string;
    severity: "success" | "info" | "warning" | "error";
  } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeIdRef = useRef<string | null>(null);

  const notify = (message: string, severity: "success" | "info" | "warning" | "error") =>
    setToast({ message, severity });

  // ---------- 加载线程详情 ----------
  const selectThread = useCallback(
    async (id: string) => {
      try {
        const detail = await getThread(id);
        activeIdRef.current = id;
        setActiveThreadId(id);
        setActiveThread(detail);
        setStatusText(STATUS_LABEL[detail.status]);
      } catch (err) {
        console.warn("加载线程详情失败", err);
        notify("加载线程失败，请检查后端", "error");
      }
    },
    [notify],
  );

  const refreshThreads = useCallback(async () => {
    try {
      setThreads(await listThreads());
    } catch (err) {
      console.warn("刷新线程列表失败", err);
    }
  }, []);

  // 初始化：加载列表；首次为空则建一个空线程
  useEffect(() => {
    (async () => {
      try {
        const list = await listThreads();
        if (list.length === 0) {
          const t = await createThread();
          setThreads([t]);
          await selectThread(t.id);
        } else {
          setThreads(list);
          await selectThread(list[0].id);
        }
      } catch (err) {
        console.warn("初始化线程失败", err);
        notify("无法连接后端，请确认本地服务已启动", "error");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------- 新建线程 ----------
  const handleNewThread = useCallback(async () => {
    if (isSearching) {
      abortRef.current?.abort();
      if (activeThreadId) cancelThread(activeThreadId).catch(() => {});
    }
    try {
      const t = await createThread();
      setThreads((prev) => [t, ...prev]);
      await selectThread(t.id);
      setStatusText("新对话已准备好");
    } catch (err) {
      console.warn(err);
      notify("新建线程失败", "error");
    }
  }, [isSearching, activeThreadId, selectThread, notify]);

  // ---------- 切换线程 ----------
  const handleSelectThread = useCallback(
    async (id: string) => {
      if (id === activeThreadId) return;
      if (isSearching) {
        abortRef.current?.abort();
        if (activeThreadId) cancelThread(activeThreadId).catch(() => {});
      }
      await selectThread(id);
    },
    [activeThreadId, isSearching, selectThread],
  );

  // ---------- 重命名 ----------
  const handleRenameThread = useCallback(
    async (id: string, title: string) => {
      try {
        const updated = await renameThread(id, title);
        setThreads((prev) => prev.map((t) => (t.id === id ? updated : t)));
        if (id === activeThreadId) {
          setActiveThread((prev) => (prev ? { ...prev, title: updated.title } : prev));
        }
      } catch (err) {
        console.warn(err);
      }
    },
    [activeThreadId],
  );

  // ---------- 删除线程 ----------
  const handleDeleteThread = useCallback(
    async (id: string) => {
      if (!confirm("确定删除该线程？此操作不可撤销。")) return;
      try {
        await deleteThread(id);
        const remaining = threads.filter((t) => t.id !== id);
        setThreads(remaining);
        if (id === activeThreadId) {
          if (remaining.length > 0) {
            await selectThread(remaining[0].id);
          } else {
            const t = await createThread();
            setThreads([t]);
            await selectThread(t.id);
          }
        }
      } catch (err) {
        console.warn(err);
        notify("删除线程失败", "error");
      }
    },
    [threads, activeThreadId, selectThread, notify],
  );

  // ---------- 发送消息 ----------
  const handleSendQuery = useCallback(
    async (queryText: string) => {
      if (isSearching || !queryText.trim() || !activeThread) return;
      const threadId = activeThread.id;

      // 乐观：先追加用户消息
      setActiveThread((prev) => (prev ? withUserMessage(prev, queryText) : prev));
      setIsSearching(true);
      setStatusText("正在初始化 Agent...");

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamThreadMessage(
          threadId,
          {
            query: queryText,
            api_key: config.apiKey,
            base_url: config.endpoint,
            model: config.modelName,
            provider: config.provider,
            max_search_rounds: config.maxSearchRounds,
            max_results_per_round: config.maxResultsPerRound,
            providers: config.providers,
            openalex_mailto: config.openalexMailto,
            crossref_mailto: config.crossrefMailto,
            semantic_scholar_api_key: config.semanticScholarApiKey,
          },
          (env) => {
            // 切换了线程就不再更新
            if (activeIdRef.current !== threadId) return;
            setActiveThread((prev) => (prev ? applyEvent(prev, env) : prev));
            setStatusText(env.message || STATUS_LABEL.running);
            if (env.type === "searching_done" || env.type === "papers") {
              setActiveTab(0);
              setResearchOpen(true);
            }
            if (env.type === "report") {
              setActiveTab(1);
              setResearchOpen(true);
            }
          },
          controller.signal,
        );
      } catch (err: unknown) {
        const e = err as Error & { code?: string };
        if (e?.name === "AbortError") {
          setActiveThread((prev) =>
            prev
              ? {
                  ...prev,
                  status: "cancelled",
                  messages: [
                    ...prev.messages,
                    {
                      role: "assistant",
                      content: "已停止当前检索。",
                      timestamp: new Date().toISOString(),
                      kind: "text",
                    },
                  ],
                }
              : prev,
          );
          setStatusText("已停止当前检索");
        } else {
          setActiveThread((prev) =>
            prev
              ? {
                  ...prev,
                  status: "error",
                  messages: [
                    ...prev.messages,
                    {
                      role: "assistant",
                      content: e?.message ? `❌ ${e.message}` : "❌ 网络或后端异常",
                      timestamp: new Date().toISOString(),
                      kind: "text",
                    },
                  ],
                  last_error: e?.message ?? "出错",
                }
              : prev,
          );
          setStatusText(e?.message || "出错");
        }
      } finally {
        abortRef.current = null;
        setIsSearching(false);
        if (activeIdRef.current === threadId) {
          try {
            const detail = await getThread(threadId);
            setActiveThread(detail);
            setStatusText(STATUS_LABEL[detail.status]);
          } catch (err) {
            console.warn("刷新线程详情失败", err);
          }
        }
        await refreshThreads();
      }
    },
    [isSearching, activeThread, config, refreshThreads],
  );

  // ---------- 停止检索（真正取消后端） ----------
  const handleStopSearch = useCallback(() => {
    abortRef.current?.abort();
    if (activeThreadId) {
      cancelThread(activeThreadId).catch(() => {});
    }
  }, [activeThreadId]);

  // ---------- 导出 ----------
  const handleExport = useCallback(
    async (type: ExportType) => {
      if (!activeThread) return;
      const exportNames: Record<ExportType, string> = {
        chat: "对话记录",
        md: "文献 Markdown",
        csv: "文献 CSV",
        json: "文献 JSON",
        report: "总结报告",
      };
      const name = exportNames[type];

      if (type === "chat" && activeThread.messages.length === 0) {
        notify("先发起一次检索，再导出对话记录", "warning");
        return;
      }
      if (["md", "csv", "json"].includes(type) && activeThread.papers.length === 0) {
        setActiveTab(0);
        setResearchOpen(true);
        notify("先完成一次检索，再导出文献结果", "warning");
        return;
      }
      if (type === "report" && !activeThread.report.trim()) {
        setActiveTab(1);
        setResearchOpen(true);
        notify("报告生成后才能导出", "warning");
        return;
      }

      setStatusText(`正在导出 ${type.toUpperCase()}...`);
      try {
        const result = await exportThread(activeThread.id, type);
        const blob = await downloadExport(result.filename);
        const dlUrl = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = dlUrl;
        a.download = result.filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(dlUrl);
        setStatusText(result.status || "导出成功");
        notify(`${name}已导出`, "success");
      } catch (err) {
        console.error(err);
        const e = err as Error;
        setStatusText("导出失败");
        notify(e?.message || "导出失败，请检查后端服务", "error");
      }
    },
    [activeThread, notify],
  );

  // ---------- 复制报告 ----------
  const handleCopyReport = useCallback(async () => {
    if (!activeThread || !activeThread.report.trim()) {
      notify("报告生成后才能复制", "warning");
      return;
    }
    try {
      await navigator.clipboard.writeText(activeThread.report);
      setStatusText("总结报告已复制");
      notify("总结报告已复制到剪贴板", "success");
    } catch (err) {
      console.error(err);
      notify("复制失败，请检查剪贴板权限", "error");
    }
  }, [activeThread, notify]);

  const handleCopyMessage = useCallback(
    async (content: string) => {
      try {
        await navigator.clipboard.writeText(content);
        notify("消息已复制", "success");
      } catch (err) {
        console.error(err);
        notify("复制失败，请检查剪贴板权限", "error");
      }
    },
    [notify],
  );

  const handleEditMessage = useCallback(
    async (message: ChatMessage, visualIndex: number, content: string) => {
      if (!activeThread) return;
      if (isSearching) {
        notify("检索运行中，完成或停止后再编辑消息", "warning");
        return;
      }
      const nextContent = content.trim();
      if (!nextContent) {
        notify("消息内容不能为空", "warning");
        return;
      }
      try {
        if (typeof message.persisted_index === "number") {
          const updated = await updateThreadMessage(
            activeThread.id,
            message.persisted_index,
            nextContent,
          );
          setActiveThread(updated);
          refreshThreads();
        } else {
          setActiveThread((prev) =>
            prev
              ? {
                  ...prev,
                  messages: prev.messages.map((m, i) =>
                    i === visualIndex ? { ...m, content: nextContent } : m,
                  ),
                }
              : prev,
          );
        }
        notify("消息已更新", "success");
      } catch (err) {
        console.error(err);
        notify((err as Error)?.message || "编辑失败", "error");
      }
    },
    [activeThread, isSearching, notify, refreshThreads],
  );

  const handleDeleteMessage = useCallback(
    async (message: ChatMessage, visualIndex: number) => {
      if (!activeThread) return;
      if (isSearching) {
        notify("检索运行中，完成或停止后再删除消息", "warning");
        return;
      }
      if (!confirm("删除这条消息？")) return;
      try {
        if (typeof message.persisted_index === "number") {
          const updated = await deleteThreadMessage(activeThread.id, message.persisted_index);
          setActiveThread(updated);
          refreshThreads();
        } else {
          setActiveThread((prev) =>
            prev
              ? {
                  ...prev,
                  messages: prev.messages.filter((_, i) => i !== visualIndex),
                }
              : prev,
          );
        }
        notify("消息已删除", "success");
      } catch (err) {
        console.error(err);
        notify((err as Error)?.message || "删除失败", "error");
      }
    },
    [activeThread, isSearching, notify, refreshThreads],
  );

  // ---------- 保存配置 ----------
  const handleConfigSave = useCallback(
    async (next: AppConfig) => {
      localStorage.setItem("arxiv_agent_provider", next.provider);
      localStorage.setItem("arxiv_agent_endpoint", next.endpoint);
      localStorage.setItem("arxiv_agent_model_name", next.modelName);
      localStorage.setItem("arxiv_agent_max_search_rounds", String(next.maxSearchRounds));
      localStorage.setItem("arxiv_agent_max_results_per_round", String(next.maxResultsPerRound));
      localStorage.setItem("arxiv_agent_providers", next.providers.join(","));
      localStorage.setItem("arxiv_agent_openalex_mailto", next.openalexMailto);
      localStorage.setItem("arxiv_agent_crossref_mailto", next.crossrefMailto);
      localStorage.setItem("arxiv_agent_has_api_key", next.apiKey ? "1" : "0");
      try {
        await saveApiKey(next.apiKey);
      } catch (err) {
        console.warn("保存 API Key 到安全存储失败，回退内存", err);
        notify("API Key 未能写入系统安全存储，本次仅保存在内存", "warning");
      }
      // Semantic Scholar Key 是敏感字段，走 secrets bridge（与主 Key 策略一致）
      try {
        await saveSecret("semantic_scholar_api_key", next.semanticScholarApiKey);
      } catch (err) {
        console.warn("保存 Semantic Scholar Key 到安全存储失败，回退内存", err);
        notify("Semantic Scholar Key 未能写入系统安全存储，本次仅保存在内存", "warning");
      }
      setConfig(next);
      setSettingsOpen(false);
      notify("配置已保存", "success");
    },
    [notify],
  );

  // ---------- 渲染 ----------
  const chatHistory: ChatMessage[] = activeThread?.messages ?? [];
  const papersList: Paper[] = activeThread?.papers ?? [];
  const reportMd = activeThread?.report ?? "";
  const canExportReport = Boolean(reportMd.trim());

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box
        sx={{
          height: "100vh",
          width: "100vw",
          overflow: "hidden",
          // 透明：让 Win11 Mica / macOS vibrancy 透出。
          bgcolor: "transparent",
          color: "#f5f5f5",
        }}
      >
        <Box
          sx={{
            height: "100%",
            width: "100%",
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              lg: sidebarOpen ? "300px minmax(0, 1fr)" : "minmax(0, 1fr)",
            },
            overflow: "hidden",
            border: "1px solid rgba(255,255,255,0.10)",
            borderRadius: 0,
            // Win11 Mica sits behind this tint. Keep the original dark tone
            // without a full-window backdrop blur; the native material is cheaper.
            bgcolor: "rgba(8,9,12,0.82)",
          }}
        >
          {sidebarOpen && (
            <AppSidebar
              threads={threads}
              activeThreadId={activeThreadId}
              isSearching={isSearching}
              onSelect={handleSelectThread}
              onNewThread={handleNewThread}
              onRename={handleRenameThread}
              onDelete={handleDeleteThread}
              onSettings={() => setSettingsOpen(true)}
              onOpenResearch={() => setResearchOpen(true)}
            />
          )}

          <Box
            sx={{
              minWidth: 0,
              minHeight: 0,
              height: "100%",
              display: "flex",
              flexDirection: "column",
              bgcolor: "rgba(5,5,7,0.42)",
              overflow: "hidden",
            }}
          >
            <ChatHeader
              title={activeThread?.title || "新对话"}
              statusText={statusText}
              isSearching={isSearching}
              threadStatus={activeThread?.status ?? "idle"}
              sidebarOpen={sidebarOpen}
              researchOpen={researchOpen}
              papersCount={papersList.length}
              canExportReport={canExportReport}
              onToggleSidebar={() => setSidebarOpen((open) => !open)}
              onOpenResearch={() => setResearchOpen(true)}
              onSettings={() => setSettingsOpen(true)}
              onExport={() => handleExport("report")}
            />

            <Box
              sx={{
                minHeight: 0,
                flex: 1,
                display: "flex",
                overflow: "hidden",
                position: "relative",
              }}
            >
              <Box sx={{ minWidth: 0, minHeight: 0, flex: 1, display: "flex", overflow: "hidden" }}>
                <CustomThread
                  chatHistory={chatHistory}
                  isSearching={isSearching}
                  statusText={statusText}
                  modelName={config.modelName}
                  hasApiKey={apiKeyReady && Boolean(config.apiKey)}
                  systemHealth={systemHealth}
                  onSettings={() => setSettingsOpen(true)}
                  handleSendQuery={handleSendQuery}
                  handleStopSearch={handleStopSearch}
                  onCopyMessage={handleCopyMessage}
                  onEditMessage={handleEditMessage}
                  onDeleteMessage={handleDeleteMessage}
                />
              </Box>

              {researchOpen && (
                <ResearchPanel
                  papersList={papersList}
                  reportMd={reportMd}
                  evidence={activeThread?.evidence ?? []}
                  activeTab={activeTab}
                  setActiveTab={setActiveTab}
                  hasChat={chatHistory.length > 0}
                  hasReport={canExportReport}
                  onClose={() => setResearchOpen(false)}
                  onExport={handleExport}
                  onCopyReport={handleCopyReport}
                />
              )}
            </Box>
          </Box>
        </Box>
      </Box>

      <SettingsDialog
        open={settingsOpen}
        config={config}
        apiKeyReady={apiKeyReady}
        setConfig={setConfig}
        onClose={() => setSettingsOpen(false)}
        onSave={handleConfigSave}
        onClearKey={async () => {
          try {
            await clearApiKey();
          } catch {
            /* ignore */
          }
          setConfig((prev) => ({ ...prev, apiKey: "" }));
          localStorage.removeItem("arxiv_agent_has_api_key");
          notify("API Key 已清除", "info");
        }}
      />

      <Snackbar
        open={!!toast}
        autoHideDuration={3500}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        {toast ? (
          <Alert
            onClose={() => setToast(null)}
            severity={toast.severity}
            variant="filled"
            sx={{ borderRadius: 2 }}
          >
            {toast.message}
          </Alert>
        ) : undefined}
      </Snackbar>

      <SetupWizard
        open={setupOpen}
        onClose={() => setSetupOpen(false)}
        config={config}
        setConfig={setConfig}
      />
    </ThemeProvider>
  );
}

// ===================== 侧栏：真线程列表 =====================

const AppSidebar = ({
  threads,
  activeThreadId,
  isSearching,
  onSelect,
  onNewThread,
  onRename,
  onDelete,
  onSettings,
  onOpenResearch,
}: {
  threads: ThreadMeta[];
  activeThreadId: string | null;
  isSearching: boolean;
  onSelect: (id: string) => void;
  onNewThread: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onSettings: () => void;
  onOpenResearch: () => void;
}) => {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  const commitRename = (id: string) => {
    const t = editingTitle.trim();
    if (t) onRename(id, t);
    setEditingId(null);
  };

  return (
    <Box
      className="app-drag"
      sx={{
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        borderRight: "1px solid rgba(255,255,255,0.06)",
        bgcolor: "rgba(7,7,10,0.44)",
        p: 1.5,
        gap: 1.5,
        overflow: "hidden",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.25, px: 0.5 }}>
        <Box
          sx={{
            width: 30,
            height: 30,
            borderRadius: 1.5,
            bgcolor: "#f5f5f5",
            color: "#050505",
            display: "grid",
            placeItems: "center",
            fontWeight: 800,
            fontSize: 15,
          }}
        >
          A
        </Box>
        <Box sx={{ minWidth: 0 }}>
          <Typography sx={{ fontSize: 18, fontWeight: 760, letterSpacing: 0 }}>
            ArxivAgent
          </Typography>
          <Typography sx={{ fontSize: 12, color: "rgba(255,255,255,0.48)" }}>
            研究工作台
          </Typography>
        </Box>
      </Box>

      <Button
        className="app-no-drag"
        fullWidth
        onClick={onNewThread}
        startIcon={<AddIcon />}
        disabled={isSearching}
        sx={{
          height: 46,
          justifyContent: "flex-start",
          px: 2,
          borderRadius: 2,
          bgcolor: "rgba(255,255,255,0.10)",
          color: "#f5f5f5",
          fontSize: 15,
          "&:hover": { bgcolor: "rgba(255,255,255,0.14)" },
        }}
      >
        新建检索
      </Button>

      <Box className="app-no-drag" sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
        <Typography
          sx={{ px: 1, mb: 0.5, fontSize: 11, fontWeight: 700, color: "rgba(255,255,255,0.38)" }}
        >
          会话历史
        </Typography>
        {threads.length === 0 ? (
          <Typography sx={{ px: 1.5, py: 2, fontSize: 12.5, color: "rgba(255,255,255,0.4)" }}>
            暂无线程
          </Typography>
        ) : (
          <List dense disablePadding>
            {threads.map((t) => {
              const isActive = t.id === activeThreadId;
              const isEditing = editingId === t.id;
              return (
                <ListItemButton
                  key={t.id}
                  selected={isActive}
                  onClick={() => !isEditing && onSelect(t.id)}
                  onDoubleClick={() => {
                    setEditingId(t.id);
                    setEditingTitle(t.title);
                  }}
                  sx={{
                    borderRadius: 1.5,
                    mb: 0.25,
                    py: 0.75,
                    px: 1.25,
                    bgcolor: isActive ? "rgba(255,255,255,0.08)" : "transparent",
                    "&:hover": { bgcolor: "rgba(255,255,255,0.05)" },
                  }}
                >
                  {isEditing ? (
                    <TextField
                      autoFocus
                      size="small"
                      fullWidth
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      onBlur={() => commitRename(t.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitRename(t.id);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                      sx={{ mr: 1 }}
                    />
                  ) : (
                    <ListItemText
                      primary={
                        <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
                          <Typography
                            noWrap
                            sx={{ fontSize: 13.5, fontWeight: 650, flex: 1, minWidth: 0 }}
                          >
                            {t.title || "新对话"}
                          </Typography>
                          {t.papers_count > 0 && (
                            <Chip
                              label={t.papers_count}
                              size="small"
                              sx={{
                                height: 18,
                                fontSize: 10,
                                bgcolor: "rgba(255,255,255,0.08)",
                                color: "rgba(255,255,255,0.7)",
                              }}
                            />
                          )}
                        </Box>
                      }
                      secondary={
                        <Typography
                          noWrap
                          sx={{ fontSize: 11.5, color: "rgba(255,255,255,0.42)" }}
                        >
                          {STATUS_LABEL[t.status]}
                          {t.has_report ? " · 有报告" : ""}
                        </Typography>
                      }
                    />
                  )}
                  <Box sx={{ display: "flex" }}>
                    <Tooltip title="重命名">
                      <IconButton
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingId(t.id);
                          setEditingTitle(t.title);
                        }}
                        sx={{ color: "rgba(255,255,255,0.4)" }}
                      >
                        <EditIcon sx={{ fontSize: 15 }} />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="删除">
                      <IconButton
                        size="small"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(t.id);
                        }}
                        sx={{ color: "rgba(255,255,255,0.4)" }}
                      >
                        <DeleteIcon sx={{ fontSize: 15 }} />
                      </IconButton>
                    </Tooltip>
                  </Box>
                </ListItemButton>
              );
            })}
          </List>
        )}
      </Box>

      <Box className="app-no-drag" sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
        <Button
          fullWidth
          startIcon={<ArticleIcon />}
          onClick={onOpenResearch}
          sx={sidebarUtilityButtonSx}
        >
          研究资料
        </Button>
        <Button
          fullWidth
          startIcon={<SettingsIcon />}
          onClick={onSettings}
          sx={sidebarUtilityButtonSx}
        >
          设置
        </Button>
      </Box>
    </Box>
  );
};

const sidebarUtilityButtonSx = {
  justifyContent: "flex-start",
  minHeight: 36,
  px: 1,
  color: "rgba(255,255,255,0.72)",
  bgcolor: "rgba(255,255,255,0.04)",
  borderRadius: 1.5,
  fontSize: 12,
  "&:hover": { bgcolor: "rgba(255,255,255,0.08)" },
};

// ===================== 顶栏 =====================

const ChatHeader = ({
  title,
  statusText,
  isSearching,
  threadStatus,
  sidebarOpen,
  researchOpen,
  papersCount,
  canExportReport,
  onToggleSidebar,
  onOpenResearch,
  onSettings,
  onExport,
}: {
  title: string;
  statusText: string;
  isSearching: boolean;
  threadStatus: ThreadStatus;
  sidebarOpen: boolean;
  researchOpen: boolean;
  papersCount: number;
  canExportReport: boolean;
  onToggleSidebar: () => void;
  onOpenResearch: () => void;
  onSettings: () => void;
  onExport: () => void;
}) => (
  <Box
    component="header"
    className="app-drag"
    sx={{
      height: 58,
      flexShrink: 0,
      pl: { xs: 2, md: 3 },
      pr: { xs: 1, md: 1.25 },
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      borderBottom: "1px solid rgba(255,255,255,0.05)",
      bgcolor: "rgba(5,5,7,0.38)",
      backdropFilter: "blur(18px)",
      WebkitBackdropFilter: "blur(18px)",
    }}
  >
    <Box sx={{ display: "flex", alignItems: "center", gap: 2, minWidth: 0 }}>
      <Tooltip title={sidebarOpen ? "隐藏侧栏" : "显示侧栏"}>
        <IconButton
          className="app-no-drag"
          onClick={onToggleSidebar}
          sx={{
            color: "#f2f2f2",
            borderRadius: 1.5,
            display: { xs: "none", lg: "inline-flex" },
          }}
        >
          <ViewSidebarIcon />
        </IconButton>
      </Tooltip>
      <Box sx={{ minWidth: 0 }}>
        <Typography noWrap sx={{ fontSize: 19, fontWeight: 760, maxWidth: 360 }}>
          {title}
        </Typography>
        <Typography noWrap sx={{ fontSize: 12, color: "rgba(255,255,255,0.42)" }}>
          {isSearching ? statusText : STATUS_LABEL[threadStatus]}
        </Typography>
      </Box>
    </Box>

    <Box className="app-no-drag" sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
      <Chip
        size="small"
        label={isSearching ? "Running" : STATUS_LABEL[threadStatus]}
        sx={{
          display: { xs: "none", sm: "inline-flex" },
          height: 28,
          borderRadius: 99,
          color: isSearching ? "#f5c451" : "#d7d7d7",
          bgcolor: "rgba(255,255,255,0.06)",
          border: "1px solid rgba(255,255,255,0.08)",
        }}
      />
      <Tooltip title="文献与报告">
        <span>
          <IconButton
            onClick={onOpenResearch}
            disabled={researchOpen}
            sx={{
              color: researchOpen ? "rgba(255,255,255,0.28)" : "#f2f2f2",
              borderRadius: 1.5,
              position: "relative",
            }}
          >
            <ArticleIcon />
            {papersCount > 0 && (
              <Box
                component="span"
                sx={{
                  position: "absolute",
                  top: 6,
                  right: 5,
                  minWidth: 16,
                  height: 16,
                  px: 0.35,
                  borderRadius: 99,
                  display: "grid",
                  placeItems: "center",
                  bgcolor: "#f5f5f5",
                  color: "#050505",
                  fontSize: 10,
                  fontWeight: 800,
                }}
              >
                {papersCount}
              </Box>
            )}
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title="导出报告">
        <span>
          <IconButton
            onClick={onExport}
            disabled={!canExportReport}
            sx={{
              color: canExportReport ? "#f2f2f2" : "rgba(255,255,255,0.28)",
              borderRadius: 1.5,
            }}
          >
            <IosShareIcon />
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title="设置">
        <IconButton onClick={onSettings} sx={{ color: "#f2f2f2", borderRadius: 1.5 }}>
          <SettingsIcon />
        </IconButton>
      </Tooltip>
      <WindowControls />
    </Box>
  </Box>
);

const WindowControls = () => {
  const controls = window.arxivAgentDesktop?.windowControls;
  if (!controls) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", ml: 0.5 }}>
      <IconButton
        aria-label="最小化窗口"
        onClick={controls.minimize}
        sx={windowControlSx}
      >
        <MinimizeIcon sx={{ fontSize: 16 }} />
      </IconButton>
      <IconButton
        aria-label="最大化或还原窗口"
        onClick={controls.toggleMaximize}
        sx={windowControlSx}
      >
        <CropSquareIcon sx={{ fontSize: 14 }} />
      </IconButton>
      <IconButton
        aria-label="关闭窗口"
        onClick={controls.close}
        sx={{
          ...windowControlSx,
          "&:hover": { bgcolor: "rgba(239,68,68,0.82)", color: "#fff" },
        }}
      >
        <CloseIcon sx={{ fontSize: 17 }} />
      </IconButton>
    </Box>
  );
};

const windowControlSx = {
  width: 34,
  height: 30,
  borderRadius: 1.25,
  color: "rgba(255,255,255,0.72)",
  "&:hover": { bgcolor: "rgba(255,255,255,0.10)", color: "#fff" },
};

// ===================== 对话线程 =====================

const CustomThread = ({
  chatHistory,
  isSearching,
  statusText,
  modelName,
  hasApiKey,
  systemHealth,
  onSettings,
  handleSendQuery,
  handleStopSearch,
  onCopyMessage,
  onEditMessage,
  onDeleteMessage,
}: {
  chatHistory: ChatMessage[];
  isSearching: boolean;
  statusText: string;
  modelName: string;
  hasApiKey: boolean;
  systemHealth: ConfigHealth | null;
  onSettings: () => void;
  handleSendQuery: (text: string) => Promise<void>;
  handleStopSearch: () => void;
  onCopyMessage: (content: string) => void;
  onEditMessage: (message: ChatMessage, visualIndex: number, content: string) => Promise<void>;
  onDeleteMessage: (message: ChatMessage, visualIndex: number) => Promise<void>;
}) => {
  const runtime = useExternalStoreRuntime({
    isRunning: isSearching,
    messages: chatHistory,
    onNew: async (message) => {
      const textParts = message.content.filter(
        (part): part is TextMessagePart => part.type === "text",
      );
      const userText = textParts.map((part) => part.text).join("\n").trim();
      await handleSendQuery(userText);
    },
    convertMessage: (m: ChatMessage, idx: number): ThreadMessageLike => {
      const role = m.role === "user" ? "user" : "assistant";
      const message = {
        id: `msg-${idx}`,
        role,
        content: m.content,
      } as ThreadMessageLike;
      if (role === "assistant") {
        return {
          ...message,
          status: { type: "complete", reason: "stop" },
        } as ThreadMessageLike;
      }
      return message;
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="aui-thread-root flex h-full min-h-0 w-full flex-col bg-transparent text-neutral-100">
        {chatHistory.length === 0 ? (
          <ThreadEmptyState
            isSearching={isSearching}
            modelName={modelName}
            statusText={statusText}
            hasApiKey={hasApiKey}
            systemHealth={systemHealth}
            onSettings={onSettings}
            handleSendQuery={handleSendQuery}
            handleStopSearch={handleStopSearch}
          />
        ) : (
          <>
            <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-4 md:px-10 md:pt-8">
              <div className="mx-auto flex w-full max-w-[880px] flex-col gap-5">
                {chatHistory.map((msg, index) => (
                  <ThreadMessage
                    key={msg.id || `${index}-${msg.role}`}
                    msg={msg}
                    index={index}
                    disabled={isSearching}
                    onCopy={onCopyMessage}
                    onEdit={onEditMessage}
                    onDelete={onDeleteMessage}
                  />
                ))}
              </div>
            </ThreadPrimitive.Viewport>

              <div className="sticky bottom-0 z-10 shrink-0 bg-gradient-to-t from-[#050507] via-[#050507]/95 to-transparent px-4 pb-5 pt-5 md:px-10 md:pb-7">
              <ComposerPanel
                isSearching={isSearching}
                modelName={modelName}
                statusText={statusText}
                onSettings={onSettings}
                handleStopSearch={handleStopSearch}
              />
              <SuggestionChips
                isSearching={isSearching}
                handleSendQuery={handleSendQuery}
                compact
              />
            </div>
          </>
        )}
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
};

const ThreadEmptyState = ({
  isSearching,
  modelName,
  statusText,
  hasApiKey,
  systemHealth,
  onSettings,
  handleSendQuery,
  handleStopSearch,
}: {
  isSearching: boolean;
  modelName: string;
  statusText: string;
  hasApiKey: boolean;
  systemHealth: ConfigHealth | null;
  onSettings: () => void;
  handleSendQuery: (text: string) => Promise<void>;
  handleStopSearch: () => void;
}) => (
  <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto">
    <div className="mx-auto flex min-h-full w-full max-w-[1040px] flex-col items-center justify-center px-5 py-12">
      <div className="mb-9 flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/55">
        <AutoAwesomeIcon sx={{ fontSize: 15 }} />
        ArxivAgent research assistant
      </div>
      <h1 className="mb-8 text-center text-[34px] font-[760] leading-tight tracking-normal text-neutral-100 md:text-[42px]">
        今天想检索什么论文？
      </h1>
      <ComposerPanel
        isSearching={isSearching}
        modelName={modelName}
        statusText={statusText}
        onSettings={onSettings}
        handleStopSearch={handleStopSearch}
        large
      />
      <SuggestionChips isSearching={isSearching} handleSendQuery={handleSendQuery} />
      <SystemStatusBar
        hasApiKey={hasApiKey}
        model={modelName}
        health={systemHealth}
        onSettings={onSettings}
      />
    </div>
  </ThreadPrimitive.Viewport>
);

/** 第一屏底部紧凑系统状态条：让用户一眼看到后端/Key/模型是否就绪。 */
const SystemStatusBar = ({
  hasApiKey,
  model,
  health,
  onSettings,
}: {
  hasApiKey: boolean;
  model: string;
  health: ConfigHealth | null;
  onSettings: () => void;
}) => {
  const backendOk = health !== null; // 拿到响应即视为后端可达
  return (
    <Box
      sx={{
        mt: 4,
        maxWidth: 720,
        width: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 1,
        flexWrap: "wrap",
        p: 1.25,
        borderRadius: 2,
        bgcolor: "rgba(255,255,255,0.025)",
        border: "1px solid rgba(255,255,255,0.08)",
      }}
    >
      <StatusDot ok={backendOk} label={backendOk ? "后端就绪" : "后端未连接"} />
      <StatusDot ok={hasApiKey} label={hasApiKey ? "API Key 已配置" : "未配置 API Key"} />
      {model && (
        <Chip
          label={model}
          size="small"
          sx={{ height: 20, fontSize: 10, bgcolor: "rgba(255,255,255,0.06)", color: "rgba(255,255,255,0.65)" }}
        />
      )}
      {!hasApiKey && (
        <Button size="small" onClick={onSettings} sx={{ fontSize: 11, textTransform: "none" }}>
          去配置 →
        </Button>
      )}
    </Box>
  );
};

const StatusDot = ({ ok, label }: { ok: boolean; label: string }) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
    <Box
      sx={{
        width: 7,
        height: 7,
        borderRadius: "50%",
        bgcolor: ok ? "#4ade80" : "#f87171",
        boxShadow: ok ? "0 0 8px rgba(74,222,128,0.5)" : "none",
      }}
    />
    <Typography sx={{ fontSize: 11, color: "rgba(255,255,255,0.6)" }}>{label}</Typography>
  </Box>
);

const ComposerPanel = ({
  isSearching,
  modelName,
  statusText,
  onSettings,
  handleStopSearch,
  large = false,
}: {
  isSearching: boolean;
  modelName: string;
  statusText: string;
  onSettings: () => void;
  handleStopSearch: () => void;
  large?: boolean;
}) => (
  <ComposerPrimitive.Root
    className={`mx-auto w-full max-w-[860px] rounded-[28px] border border-white/10 bg-[#111111] p-4 shadow-[0_18px_70px_rgba(0,0,0,0.35)] transition-colors focus-within:border-white/20 ${
      large ? "min-h-[150px]" : "min-h-[126px]"
    }`}
  >
    <ComposerPrimitive.Input
      placeholder="描述你的论文检索需求..."
      className="min-h-[58px] w-full resize-none bg-transparent px-1 text-[18px] leading-7 text-neutral-100 outline-none placeholder:text-neutral-500 md:text-[19px]"
    />

    <div className="mt-3 flex items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={onSettings}
          className="flex min-w-0 items-center gap-2 rounded-full px-2.5 py-1.5 text-sm font-semibold text-neutral-200 transition-colors hover:bg-white/10"
          aria-label="打开模型与检索设置"
        >
          <SearchIcon sx={{ fontSize: 18 }} />
          <span className="max-w-[150px] truncate">{modelName || DEFAULT_MODEL}</span>
          <KeyboardArrowDownIcon sx={{ fontSize: 18, color: "rgba(255,255,255,0.46)" }} />
        </button>
        <span className="hidden max-w-[260px] truncate text-xs text-neutral-500 md:inline">
          {statusText}
        </span>
      </div>

      <div className="flex items-center gap-2">
        {isSearching ? (
          <button
            type="button"
            onClick={handleStopSearch}
            className="grid size-10 place-items-center rounded-full bg-neutral-200 text-neutral-950 transition-colors hover:bg-white"
            aria-label="Stop search"
          >
            <StopIcon sx={{ fontSize: 18 }} />
          </button>
        ) : (
          <ComposerPrimitive.Send asChild>
            <button
              type="submit"
              className="grid size-10 place-items-center rounded-full bg-neutral-200 text-neutral-950 transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="Send message"
            >
              <ArrowUpwardIcon sx={{ fontSize: 20 }} />
            </button>
          </ComposerPrimitive.Send>
        )}
      </div>
    </div>
  </ComposerPrimitive.Root>
);

const SuggestionChips = ({
  isSearching,
  handleSendQuery,
  compact = false,
}: {
  isSearching: boolean;
  handleSendQuery: (text: string) => Promise<void>;
  compact?: boolean;
}) => (
  <div
    className={`mx-auto flex max-w-[860px] flex-wrap items-center justify-center gap-2 ${
      compact ? "mt-3" : "mt-7"
    }`}
  >
    {suggestionPrompts.map(({ label, icon: Icon, prompt }) => (
      <button
        key={label}
        type="button"
        onClick={() => handleSendQuery(prompt)}
        disabled={isSearching}
        className="flex h-11 items-center gap-2 rounded-full border border-white/10 bg-black/10 px-4 text-[15px] font-semibold text-neutral-200 transition-colors hover:border-white/18 hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45"
      >
        <Icon sx={{ fontSize: 19 }} />
        {label}
      </button>
    ))}
  </div>
);

// ===================== 单条消息 =====================

const ThreadMessage = ({
  msg,
  index,
  disabled,
  onCopy,
  onEdit,
  onDelete,
}: {
  msg: ChatMessage;
  index: number;
  disabled: boolean;
  onCopy: (content: string) => void;
  onEdit: (message: ChatMessage, visualIndex: number, content: string) => Promise<void>;
  onDelete: (message: ChatMessage, visualIndex: number) => Promise<void>;
}) => {
  const isUser = msg.role === "user";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(msg.content);

  useEffect(() => {
    if (!editing) setDraft(msg.content);
  }, [editing, msg.content]);

  const canEdit = msg.kind === "text" || msg.kind === "report";
  const actions = (
    <MessageActions
      canEdit={canEdit}
      editDisabled={disabled}
      deleteDisabled={disabled}
      onCopy={() => onCopy(msg.content)}
      onEdit={() => {
        setDraft(msg.content);
        setEditing(true);
      }}
      onDelete={() => onDelete(msg, index)}
    />
  );

  const saveEdit = async () => {
    const next = draft.trim();
    if (!next) return;
    if (next !== msg.content.trim()) {
      await onEdit(msg, index, next);
    }
    setEditing(false);
  };

  if (editing) {
    return (
      <div className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}>
        <div
          className={`w-full max-w-[760px] rounded-[22px] border p-3 shadow-sm ${
            isUser
              ? "border-white/14 bg-neutral-200 text-neutral-950"
              : "border-white/10 bg-[#101010] text-neutral-100"
          }`}
        >
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className={`min-h-[120px] w-full resize-y rounded-2xl border px-3 py-2 text-[15px] leading-7 outline-none ${
              isUser
                ? "border-black/10 bg-white/70 text-neutral-950 placeholder:text-neutral-500"
                : "border-white/10 bg-black/20 text-neutral-100 placeholder:text-neutral-500"
            }`}
            autoFocus
          />
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="grid size-8 place-items-center rounded-full text-neutral-500 transition-colors hover:bg-black/10 hover:text-neutral-900 dark:hover:bg-white/10 dark:hover:text-white"
              aria-label="取消编辑"
            >
              <CancelIcon sx={{ fontSize: 18 }} />
            </button>
            <button
              type="button"
              onClick={saveEdit}
              disabled={!draft.trim()}
              className="grid size-8 place-items-center rounded-full bg-neutral-900 text-white transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="保存编辑"
            >
              <CheckCircleIcon sx={{ fontSize: 18 }} />
            </button>
          </div>
        </div>
      </div>
    );
  }

  // 思考/状态消息用可折叠展示
  const isCollapsible = !isUser && (msg.kind === "thinking" || msg.kind === "status");

  if (!isUser && msg.kind === "error") {
    return (
      <div className="group w-full max-w-[860px]">
        <div className="rounded-[18px] border border-red-400/20 bg-red-950/20 p-4 text-red-100">
          <div className="mb-2 flex items-center justify-between gap-3">
            <div className="text-sm font-bold text-red-200">运行出错</div>
            {actions}
          </div>
          <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-black/30 p-3 text-xs leading-5 text-red-100/85">
            {msg.content}
          </pre>
        </div>
      </div>
    );
  }

  if (isCollapsible) {
    return (
      <div className="group w-full max-w-[760px]">
        <Accordion
          disableGutters
          elevation={0}
          sx={{
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: "16px !important",
            background: "rgba(255,255,255,0.035)",
            color: "#e8e8e8",
            "&:before": { display: "none" },
          }}
        >
          <AccordionSummary
            expandIcon={<ExpandMoreIcon sx={{ fontSize: "1rem", color: "#aaa" }} />}
            sx={{ minHeight: 42 }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, width: "100%", pr: 1 }}>
              <Typography
                variant="body2"
                sx={{ flex: 1, fontWeight: 700, color: "rgba(255,255,255,0.72)" }}
              >
                {msg.kind === "thinking" ? "Agent 思考" : "Agent 步骤"}
              </Typography>
              <Box
                className="app-no-drag"
                onClick={(event) => event.stopPropagation()}
                onFocus={(event) => event.stopPropagation()}
              >
                {actions}
              </Box>
            </Box>
          </AccordionSummary>
          <AccordionDetails sx={{ pt: 0, pb: 2, px: 2.5 }}>
            <div
              className="markdown-body text-sm leading-relaxed text-neutral-400"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
            />
          </AccordionDetails>
        </Accordion>
      </div>
    );
  }

  // 报告消息：醒目渲染
  if (!isUser && msg.kind === "report") {
    return (
      <div className="group w-full max-w-[760px]">
        <div
          className="rounded-[22px] border border-white/8 bg-[#0e0e0e] px-4 py-3 text-[15px] leading-7 text-neutral-100"
        >
          <div className="mb-1 flex justify-end">{actions}</div>
          <div
            className="markdown-body"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className={`group flex w-full ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[760px] rounded-[22px] border px-4 py-3 text-[15px] leading-7 shadow-sm ${
          isUser
            ? "border-white/10 bg-neutral-200 text-neutral-950"
            : "border-white/8 bg-[#101010] text-neutral-100"
        }`}
      >
        <div className={`mb-1 flex ${isUser ? "justify-start" : "justify-end"}`}>{actions}</div>
        <div className="markdown-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }} />
      </div>
    </div>
  );
};

const MessageActions = ({
  canEdit,
  editDisabled,
  deleteDisabled,
  onCopy,
  onEdit,
  onDelete,
}: {
  canEdit: boolean;
  editDisabled: boolean;
  deleteDisabled: boolean;
  onCopy: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) => (
  <div className="app-no-drag flex items-center gap-1 opacity-70 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
    <Tooltip title="复制">
      <span>
        <button
          type="button"
          onClick={onCopy}
          className={messageActionButtonClass}
          aria-label="复制消息"
        >
          <ContentCopyIcon sx={{ fontSize: 15 }} />
        </button>
      </span>
    </Tooltip>
    {canEdit && (
      <Tooltip title={editDisabled ? "运行中不可编辑" : "编辑"}>
        <span>
          <button
            type="button"
            onClick={onEdit}
            disabled={editDisabled}
            className={messageActionButtonClass}
            aria-label="编辑消息"
          >
            <EditIcon sx={{ fontSize: 15 }} />
          </button>
        </span>
      </Tooltip>
    )}
    <Tooltip title={deleteDisabled ? "运行中不可删除" : "删除"}>
      <span>
        <button
          type="button"
          onClick={onDelete}
          disabled={deleteDisabled}
          className={`${messageActionButtonClass} hover:text-red-300`}
          aria-label="删除消息"
        >
          <DeleteIcon sx={{ fontSize: 15 }} />
        </button>
      </span>
    </Tooltip>
  </div>
);

const messageActionButtonClass =
  "grid size-7 place-items-center rounded-full opacity-65 transition hover:bg-white/10 hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-35";

// ===================== 研究资料面板 =====================

const ResearchPanel = ({
  papersList,
  reportMd,
  evidence,
  activeTab,
  setActiveTab,
  hasChat,
  hasReport,
  onClose,
  onExport,
  onCopyReport,
}: {
  papersList: Paper[];
  reportMd: string;
  evidence: EvidenceChunk[];
  activeTab: number;
  setActiveTab: (tab: number) => void;
  hasChat: boolean;
  hasReport: boolean;
  onClose: () => void;
  onExport: (type: ExportType) => void;
  onCopyReport: () => void;
}) => (
  <Box
    sx={{
      position: { xs: "absolute", md: "relative" },
      inset: { xs: 0, md: "auto" },
      zIndex: { xs: 30, md: "auto" },
      width: { xs: "100%", md: 410, xl: 460 },
      maxWidth: { xs: "100%", md: "42vw" },
      minWidth: { md: 360 },
      height: "100%",
      minHeight: 0,
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
      borderLeft: "1px solid rgba(255,255,255,0.08)",
      bgcolor: "rgba(12,12,15,0.74)",
      backdropFilter: "blur(22px)",
      WebkitBackdropFilter: "blur(22px)",
    }}
  >
    <Box
      sx={{
        height: 58,
        px: 2,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <Box>
        <Typography sx={{ fontSize: 16, fontWeight: 760 }}>研究资料</Typography>
        <Typography sx={{ fontSize: 11.5, color: "rgba(255,255,255,0.42)" }}>
          {papersList.length ? `${papersList.length} 篇候选文献` : "等待检索结果"}
        </Typography>
      </Box>
      <IconButton onClick={onClose} sx={{ color: "#f2f2f2" }}>
        <CloseIcon />
      </IconButton>
    </Box>

    <Tabs
      value={activeTab}
      onChange={(_, val) => setActiveTab(val)}
      variant="fullWidth"
      sx={{
        minHeight: 44,
        borderTop: "1px solid rgba(255,255,255,0.06)",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        "& .MuiTab-root": { minHeight: 44, fontSize: 13, color: "rgba(255,255,255,0.55)" },
        "& .Mui-selected": { color: "#fff !important" },
        "& .MuiTabs-indicator": { bgcolor: "#fff" },
      }}
    >
      <Tab label={`文献 (${papersList.length})`} />
      <Tab label="报告" />
    </Tabs>

    <Box
      className="research-panel-scroll app-no-drag"
      sx={{
        flex: 1,
        minHeight: 0,
        overflowY: "auto",
        overscrollBehavior: "contain",
        scrollbarGutter: "stable",
        p: 2,
      }}
    >
      {activeTab === 0 && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          {papersList.length === 0 ? (
            <Box sx={{ p: 4, textAlign: "center", color: "rgba(255,255,255,0.45)" }}>
              <Typography variant="body2">
                <em>暂无检索结果。开始一次检索后，文献卡片会在这里出现。</em>
              </Typography>
            </Box>
          ) : (
            papersList.map((paper, i) => (
              <PaperCard
                key={`${paper.link || paper.arxiv_id || i}-${i}`}
                paper={paper}
                index={i}
              />
            ))
          )}
        </Box>
      )}

      {activeTab === 1 && (
        <Box>
          {!hasReport ? (
            <Box sx={{ p: 4, textAlign: "center", color: "rgba(255,255,255,0.45)" }}>
              <Typography variant="body2">
                <em>最终文献推荐总结报告尚未生成。</em>
              </Typography>
            </Box>
          ) : (
            <Box>
              <Box sx={{ display: "flex", justifyContent: "flex-end", mb: 1 }}>
                <Button size="small" startIcon={<ContentCopyIcon />} onClick={onCopyReport}>
                  复制报告
                </Button>
              </Box>
              <Box
                className="markdown-body"
                sx={{
                  p: 2,
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 2,
                  bgcolor: "rgba(255,255,255,0.03)",
                  fontSize: "0.9rem",
                  lineHeight: 1.7,
                  color: "rgba(255,255,255,0.78)",
                  "& h1, & h2, & h3": { color: "#fff", mt: 2, mb: 1, fontWeight: 760 },
                  "& h3": { borderBottom: "1px solid rgba(255,255,255,0.08)", pb: 0.5 },
                  // 引用证据徽标样式（来自 markdown.ts transformCitations）
                  "& .aui-citation": {
                    display: "inline-flex",
                    alignItems: "center",
                    mx: 0.25,
                    px: 0.6,
                    py: 0.1,
                    borderRadius: 1,
                    fontSize: "0.78rem",
                    fontWeight: 600,
                    bgcolor: "rgba(99,179,237,0.16)",
                    color: "#9cc8f5",
                    border: "1px solid rgba(99,179,237,0.4)",
                    cursor: "default",
                  },
                }}
                dangerouslySetInnerHTML={{ __html: renderMarkdown(reportMd) }}
              />
              {evidence.length > 0 && (
                <EvidenceList reportMd={reportMd} evidence={evidence} />
              )}
            </Box>
          )}
        </Box>
      )}
    </Box>

    <Box
      sx={{
        p: 1.5,
        borderTop: "1px solid rgba(255,255,255,0.08)",
        display: "flex",
        flexWrap: "wrap",
        gap: 1,
      }}
    >
      <Button
        size="small"
        variant="outlined"
        startIcon={<ArticleIcon />}
        disabled={!hasChat}
        onClick={() => onExport("chat")}
      >
        对话
      </Button>
      <Button
        size="small"
        variant="outlined"
        startIcon={<MenuBookIcon />}
        disabled={papersList.length === 0}
        onClick={() => onExport("md")}
      >
        MD
      </Button>
      <Button
        size="small"
        variant="outlined"
        startIcon={<FolderZipIcon />}
        disabled={papersList.length === 0}
        onClick={() => onExport("csv")}
      >
        CSV
      </Button>
      <Button
        size="small"
        variant="outlined"
        disabled={papersList.length === 0}
        onClick={() => onExport("json")}
      >
        JSON
      </Button>
      <Button
        size="small"
        variant="outlined"
        disabled={!hasReport}
        onClick={() => onExport("report")}
      >
        报告
      </Button>
    </Box>
  </Box>
);

const PaperCard = ({ paper, index }: { paper: Paper; index: number }) => (
  <Card
    sx={{
      bgcolor: "rgba(255,255,255,0.025)",
      border: "1px solid rgba(255,255,255,0.08)",
      borderRadius: 2,
      "&:hover": { borderColor: "rgba(255,255,255,0.16)" },
    }}
  >
    <CardContent sx={{ p: 2, "&:last-child": { pb: 2 } }}>
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1, mb: 1 }}>
        <Typography
          sx={{
            flex: 1,
            fontSize: 14,
            fontWeight: 700,
            lineHeight: 1.4,
            color: "#f0f0f0",
          }}
        >
          {index + 1}. {paper.title || "无标题"}
        </Typography>
        {paper.link && (
          <IconButton
            size="small"
            href={paper.link}
            target="_blank"
            rel="noreferrer"
            sx={{ color: "rgba(255,255,255,0.5)" }}
          >
            <OpenInNewIcon sx={{ fontSize: 16 }} />
          </IconButton>
        )}
      </Box>

      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mb: 1 }}>
        <Chip label={paper.source || "arxiv"} size="small" sx={paperMetaChipSx} />
        {paper.published && (
          <Chip label={paper.published.slice(0, 10)} size="small" sx={paperMetaChipSx} />
        )}
        {paper.citation_count > 0 && (
          <Chip label={`引用 ${paper.citation_count}`} size="small" sx={paperMetaChipSx} />
        )}
      </Box>

      {paper.authors.length > 0 && (
        <Typography
          variant="caption"
          sx={{ color: "rgba(255,255,255,0.5)", display: "block", mb: 0.5 }}
        >
          {paper.authors.slice(0, 4).join(", ")}
          {paper.authors.length > 4 ? " 等" : ""}
        </Typography>
      )}
      {paper.categories.length > 0 && (
        <Typography variant="caption" sx={{ color: "rgba(255,255,255,0.4)", display: "block" }}>
          {paper.categories.slice(0, 3).join(" · ")}
        </Typography>
      )}

      {paper.abstract && (
        <Typography
          sx={{
            mt: 1,
            borderLeft: "2px solid rgba(255,255,255,0.14)",
            pl: 1.25,
            color: "rgba(255,255,255,0.56)",
            fontSize: "0.82rem",
            lineHeight: 1.55,
          }}
        >
          {paper.abstract.slice(0, 180)}
          {paper.abstract.length > 180 ? "..." : ""}
        </Typography>
      )}
    </CardContent>
  </Card>
);

const paperMetaChipSx = {
  height: 22,
  bgcolor: "rgba(255,255,255,0.06)",
  color: "rgba(255,255,255,0.72)",
  fontWeight: 600,
};

// ===================== 证据列表（报告引用的正文切片） =====================

const EvidenceList = ({
  reportMd,
  evidence,
}: {
  reportMd: string;
  evidence: EvidenceChunk[];
}) => {
  // 只展示报告里实际被引用的 evidence（按标题+分块匹配）
  const cited = extractCitations(reportMd)
    .map((ref) => ({ ref, chunk: matchEvidence(ref, evidence) }))
    .filter((c) => c.chunk);
  if (cited.length === 0) return null;

  return (
    <Box sx={{ mt: 2 }}>
      <Typography sx={{ fontSize: 12, fontWeight: 700, color: "rgba(255,255,255,0.6)", mb: 1 }}>
        引用证据（{cited.length}）
      </Typography>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {cited.map(({ ref, chunk }, i) => (
          <Box
            key={`${ref.paperTitle}-${ref.chunkIndex}-${i}`}
            sx={{
              p: 1.25,
              borderRadius: 1.5,
              bgcolor: "rgba(99,179,237,0.06)",
              border: "1px solid rgba(99,179,237,0.18)",
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.5, flexWrap: "wrap" }}>
              <Typography sx={{ fontSize: 12.5, fontWeight: 700, color: "#cfe3f7", flex: 1, minWidth: 0 }} noWrap>
                {chunk!.paper_title}
              </Typography>
              <Chip label={`分块 ${chunk!.chunk_index}`} size="small" sx={evidenceChipSx} />
              {chunk!.retrieval_sources.map((s) => (
                <Chip key={s} label={s} size="small" sx={evidenceChipSx} />
              ))}
            </Box>
            <Typography sx={{ fontSize: 12, lineHeight: 1.55, color: "rgba(255,255,255,0.7)" }}>
              {chunk!.text}
            </Typography>
          </Box>
        ))}
      </Box>
    </Box>
  );
};

const evidenceChipSx = {
  height: 18,
  fontSize: 10,
  bgcolor: "rgba(255,255,255,0.08)",
  color: "rgba(255,255,255,0.7)",
};

// ===================== 设置弹窗（含测试连接） =====================

const SettingsDialog = ({
  open,
  config,
  apiKeyReady,
  setConfig,
  onClose,
  onSave,
  onClearKey,
}: {
  open: boolean;
  config: AppConfig;
  apiKeyReady: boolean;
  setConfig: React.Dispatch<React.SetStateAction<AppConfig>>;
  onClose: () => void;
  onSave: (next: AppConfig) => void;
  onClearKey: () => void;
}) => {
  const [health, setHealth] = useState<ConfigHealth | null>(null);
  const [testing, setTesting] = useState(false);

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
        openalex_mailto: config.openalexMailto,
        crossref_mailto: config.crossrefMailto,
        semantic_scholar_api_key: config.semanticScholarApiKey,
        ping_llm: true,
      });
      setHealth({ ...result, encryption_available: hasSecretsBridge() ? true : null });
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
        encryption_available: hasSecretsBridge() ? true : null,
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xs"
      fullWidth
      scroll="paper"
      slotProps={{
        paper: {
          sx: {
            maxHeight: "calc(100vh - 48px)",
            borderRadius: 2,
            bgcolor: "rgba(18,18,20,0.92)",
            backdropFilter: "blur(22px)",
          },
        },
      }}
    >
      <DialogTitle sx={{ fontWeight: 760 }}>配置中心</DialogTitle>
      <DialogContent dividers sx={{ overflowY: "auto", overscrollBehavior: "contain" }}>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2, py: 1 }}>
          {/* —— LLM 供应商（选预设自动带出端点 + 默认模型） —— */}
          <FormControl size="small" fullWidth>
            <InputLabel>LLM 供应商</InputLabel>
            <Select
              value={config.provider}
              label="LLM 供应商"
              onChange={(e) => {
                const prov = String(e.target.value);
                const preset = getPreset(prov);
                // 选预设：自动带出端点 + 默认模型，但保留用户已填的 API Key
                setConfig((prev) => ({
                  ...prev,
                  provider: prov,
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

          {/* 当前预设的提示 + 获取 Key 文档链接 */}
          {(() => {
            const preset = getPreset(config.provider);
            return (
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 0.5,
                  px: 1.25,
                  py: 1,
                  borderRadius: 1.5,
                  bgcolor: "rgba(255,255,255,0.04)",
                  border: "1px solid rgba(255,255,255,0.08)",
                }}
              >
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

          {/* API Key */}
          <Box>
            <TextField
              label="API Key"
              size="small"
              type="password"
              fullWidth
              value={config.apiKey}
              onChange={(e) => setConfig((prev) => ({ ...prev, apiKey: e.target.value }))}
              placeholder={getPreset(config.provider).keyHint}
            />
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                mt: 0.5,
              }}
            >
              <Typography variant="caption" sx={{ color: "rgba(255,255,255,0.45)" }}>
                {hasSecretsBridge()
                  ? "存储于系统安全存储 (safeStorage)"
                  : "安全存储不可用，仅保存在内存（开发模式）"}
              </Typography>
              {config.apiKey && (
                <Button size="small" color="error" onClick={onClearKey}>
                  清除
                </Button>
              )}
            </Box>
          </Box>

          {/* 端点：预设带出但仍可改（custom 时手填） */}
          <TextField
            label="API 端点 (Base URL)"
            size="small"
            fullWidth
            value={config.endpoint}
            onChange={(e) => setConfig((prev) => ({ ...prev, endpoint: e.target.value }))}
            placeholder="https://..."
          />

          {/* 模型：预设有候选模型时用下拉，否则纯文本 */}
          {(() => {
            const preset = getPreset(config.provider);
            if (preset.models.length > 0) {
              // 当前值不在候选里（用户自定义过）时，把它也加进选项
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
                  onChange={(_e, val) =>
                    setConfig((prev) => ({ ...prev, modelName: val ?? "" }))
                  }
                  onInputChange={(_e, val) =>
                    setConfig((prev) => ({ ...prev, modelName: val }))
                  }
                  options={options}
                  renderInput={(params) => (
                    <TextField {...params} label="模型名称 (Model)" placeholder="选择或输入" />
                  )}
                />
              );
            }
            return (
              <TextField
                label="模型名称 (Model Name)"
                size="small"
                fullWidth
                value={config.modelName}
                onChange={(e) => setConfig((prev) => ({ ...prev, modelName: e.target.value }))}
                placeholder="例如 gpt-4o-mini"
              />
            );
          })()}

          <Divider sx={{ my: 0.5 }}>
            <Chip label="Agent 检索配置" size="small" />
          </Divider>

          <TextField
            label="最大检索迭代轮次"
            size="small"
            type="number"
            fullWidth
            value={config.maxSearchRounds}
            onChange={(e) =>
              setConfig((prev) => ({
                ...prev,
                maxSearchRounds: parseInt(e.target.value, 10) || 3,
              }))
            }
          />

          <TextField
            label="每轮最大返回结果数"
            size="small"
            type="number"
            fullWidth
            value={config.maxResultsPerRound}
            onChange={(e) =>
              setConfig((prev) => ({
                ...prev,
                maxResultsPerRound: parseInt(e.target.value, 10) || 10,
              }))
            }
          />

          <Typography variant="body2" sx={{ fontWeight: 700, color: "text.secondary", mt: 0.5 }}>
            启用文献检索源
          </Typography>
          <FormGroup row sx={{ gap: 1 }}>
            {["arxiv", "openalex", "crossref", "semantic_scholar"].map((prov) => (
              <FormControlLabel
                key={prov}
                control={
                  <Checkbox
                    size="small"
                    checked={config.providers.includes(prov)}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setConfig((prev) => {
                        let updated = checked
                          ? Array.from(new Set([...prev.providers, prov]))
                          : prev.providers.filter((p) => p !== prov);
                        if (updated.length === 0) updated = ["arxiv"];
                        return { ...prev, providers: updated };
                      });
                    }}
                  />
                }
                label={prov === "semantic_scholar" ? "S.Scholar" : prov}
              />
            ))}
          </FormGroup>

          <TextField
            label="OpenAlex Mailto (建议)"
            size="small"
            fullWidth
            value={config.openalexMailto}
            onChange={(e) => setConfig((prev) => ({ ...prev, openalexMailto: e.target.value }))}
            placeholder="填写邮箱以使用 polite pool"
          />

          <TextField
            label="Crossref Mailto (建议)"
            size="small"
            fullWidth
            value={config.crossrefMailto}
            onChange={(e) => setConfig((prev) => ({ ...prev, crossrefMailto: e.target.value }))}
            placeholder="填写邮箱获得更稳定访问"
          />

          <TextField
            label="Semantic Scholar API Key"
            size="small"
            type="password"
            fullWidth
            value={config.semanticScholarApiKey}
            onChange={(e) =>
              setConfig((prev) => ({ ...prev, semanticScholarApiKey: e.target.value }))
            }
            placeholder="可选，留空使用环境变量"
          />

          <Divider sx={{ my: 0.5 }}>
            <Chip label="连接测试" size="small" />
          </Divider>

          <Button
            variant="outlined"
            size="small"
            startIcon={testing ? <RefreshIcon /> : <CheckCircleIcon />}
            onClick={runTest}
            disabled={testing || !apiKeyReady}
          >
            {testing ? "测试中..." : "测试连接"}
          </Button>
          {testing && <LinearProgress />}
          {health && (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
              <HealthRow
                ok={health.api_key_configured}
                label={`API Key${
                  health.api_key_source === "env"
                    ? "（来自环境变量）"
                    : health.api_key_source === "request"
                      ? "（本次输入）"
                      : "（未配置）"
                }`}
              />
              <HealthRow
                ok={health.llm_reachable !== false}
                label={
                  health.llm_reachable === null
                    ? "模型（未测试）"
                    : health.llm_reachable
                      ? `模型可达 (${health.model})`
                      : `模型不可达: ${health.llm_detail}`
                }
              />
              {health.providers.map((p) => (
                <HealthRow
                  key={p.name}
                  ok={p.ok}
                  label={`${p.name}: ${p.detail || (p.ok ? "ok" : "失败")}`}
                />
              ))}
              {health.data_dir && (
                <Typography variant="caption" sx={{ color: "rgba(255,255,255,0.4)", mt: 0.5 }}>
                  数据目录: {health.data_dir}
                </Typography>
              )}
            </Box>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button variant="contained" onClick={() => onSave(config)}>
          保存配置
        </Button>
      </DialogActions>
    </Dialog>
  );
};

const HealthRow = ({ ok, label }: { ok: boolean; label: string }) => (
  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
    {ok ? (
      <CheckCircleIcon sx={{ fontSize: 16, color: "#4ade80" }} />
    ) : (
      <CancelIcon sx={{ fontSize: 16, color: "#f87171" }} />
    )}
    <Typography variant="caption" sx={{ color: "rgba(255,255,255,0.7)" }}>
      {label}
    </Typography>
  </Box>
);

export default App;
