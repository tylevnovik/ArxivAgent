/**
 * Markdown 渲染 + DOMPurify 安全加固（集中一处）。
 *
 * 引用证据：把报告里的 【正文: 标题 | 分块 N】 标记在 markdown→html 阶段
 * 转成带 data-cite 属性的 <span class="aui-citation">，视觉上突出（蓝色徽标）。
 * 点击交互通过 EvidencePanel 下方的证据列表承载（避免 dangerouslySetInnerHTML
 * 内无法挂 React 组件的限制）。
 */
import { marked } from "marked";
import DOMPurify from "dompurify";

// 把 【正文: 标题 | 分块 N】 转成可点击的 data-cite span（视觉徽标）
const CITATION_RE = /【正文:\s*([^|】]+?)\s*\|\s*分块\s*([0-9]+)\s*】/g;

function transformCitations(markdown: string): string {
  return markdown.replace(
    CITATION_RE,
    (_m, title: string, chunk: string) =>
      `<span class="aui-citation" data-cite-title="${escapeAttr(title.trim())}" data-cite-chunk="${escapeAttr(
        chunk.trim(),
      )}" title="正文证据：${escapeAttr(title.trim())} · 分块 ${escapeAttr(
        chunk.trim(),
      )}">📖 ${escapeHtml(title.trim().slice(0, 16))}${
        title.trim().length > 16 ? "…" : ""
      } · #${escapeHtml(chunk.trim())}</span>`,
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function escapeAttr(s: string): string {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

marked.setOptions({ gfm: true, breaks: true });

// DOMPurify 加固配置：禁止脚本/iframe/表单，限制 href 协议
const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    // 基础文本
    "p", "br", "hr", "span", "div",
    // 标题
    "h1", "h2", "h3", "h4", "h5", "h6",
    // 列表
    "ul", "ol", "li",
    // 强调
    "strong", "em", "del", "blockquote", "code", "pre",
    // 链接/图片
    "a", "img",
    // 表格
    "table", "thead", "tbody", "tr", "th", "td",
  ],
  ALLOWED_ATTR: [
    "href", "src", "alt", "title", "class", "target", "rel",
    // 引用证据 data-* 属性
    "data-cite-title", "data-cite-chunk",
  ],
  ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|data:image\/|\/)/i,
  FORBID_ATTR: ["style", "onerror", "onload", "onclick", "onmouseover"],
  RETURN_TRUSTED_TYPE: false,
};

/**
 * 渲染 markdown 为已 sanitize 的 HTML。
 * 引用标记被转成 <span class="aui-citation">。
 */
export function renderMarkdown(markdown: string): string {
  const withCitations = transformCitations(markdown || "");
  const rawHtml = marked.parse(withCitations) as string;
  return DOMPurify.sanitize(rawHtml, PURIFY_CONFIG) as string;
}
