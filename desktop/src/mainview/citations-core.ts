/**
 * 引用标记的纯函数解析（无 React/MUI 依赖，便于单测）。
 *
 * 报告里形如 【正文: 论文标题 | 分块 N】 的内联标记由 LLM 生成（见 prompts/summary.txt）。
 * 这里把它解析出来，匹配到 evidence chunks。
 */
import type { EvidenceChunk } from "./api";

/** 【正文: 标题 | 分块 N】 标记的正则。标题和分块号都容错。 */
const CITATION_RE = /【正文:\s*([^|】]+?)\s*\|\s*分块\s*([0-9]+)\s*】/g;

export type CitationRef = {
  paperTitle: string;
  chunkIndex: string;
};

/** 从文本里提取所有引用标记（不去重）。 */
export function extractCitations(text: string): CitationRef[] {
  const refs: CitationRef[] = [];
  if (!text) return refs;
  let m: RegExpExecArray | null;
  const re = new RegExp(CITATION_RE.source, "g");
  while ((m = re.exec(text)) !== null) {
    refs.push({ paperTitle: m[1].trim(), chunkIndex: m[2].trim() });
  }
  return refs;
}

function normTitle(s: string): string {
  return (s || "").replace(/\s+/g, " ").trim().toLowerCase();
}

/** 给一个引用标记找到对应的 evidence chunk（按标题+分块号匹配）。 */
export function matchEvidence(
  ref: CitationRef,
  evidence: EvidenceChunk[],
): EvidenceChunk | undefined {
  const wantTitle = normTitle(ref.paperTitle);
  const wantChunk = String(ref.chunkIndex);
  // 精确匹配（标题前缀 + 分块号）；标题可能被 LLM 截断，用 startsWith 双向兜底
  return (
    evidence.find(
      (e) => normTitle(e.paper_title) === wantTitle && String(e.chunk_index) === wantChunk,
    ) ||
    evidence.find(
      (e) =>
        (normTitle(e.paper_title).startsWith(wantTitle) ||
          wantTitle.startsWith(normTitle(e.paper_title))) &&
        String(e.chunk_index) === wantChunk,
    )
  );
}

/** 把含引用标记的文本切成段：纯文本段 + 引用段。 */
export type RenderSegment =
  | { kind: "text"; text: string }
  | { kind: "citation"; ref: CitationRef; evidence?: EvidenceChunk };

export function splitByCitations(
  text: string,
  evidence: EvidenceChunk[],
): RenderSegment[] {
  if (!text) return [{ kind: "text", text: "" }];
  const segments: RenderSegment[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  const re = new RegExp(CITATION_RE.source, "g");
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) segments.push({ kind: "text", text: text.slice(last, m.index) });
    const ref: CitationRef = { paperTitle: m[1].trim(), chunkIndex: m[2].trim() };
    segments.push({ kind: "citation", ref, evidence: matchEvidence(ref, evidence) });
    last = m.index + m[0].length;
  }
  if (last < text.length) segments.push({ kind: "text", text: text.slice(last) });
  return segments;
}
