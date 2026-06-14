/**
 * 报告引用证据的渲染组件（CitationChip / EvidencePopover）。
 *
 * 纯函数解析在 ./citations-core.ts（无 React 依赖，便于单测）。
 * 这里只保留需要 React/MUI 的渲染部分。
 */
import { Fragment, useMemo, useState } from "react";
import { Box, Chip, Popover, Typography, Link } from "@mui/material";
import type { EvidenceChunk } from "./api";
import {
  type CitationRef,
  splitByCitations,
} from "./citations-core";

// 重新导出纯函数，方便外部统一从 citations 导入
export { extractCitations, matchEvidence, splitByCitations } from "./citations-core";
export type { CitationRef, RenderSegment } from "./citations-core";

/** 单个可点击引用 chip + 弹层。 */
export function CitationChip({
  ref,
  evidence,
}: {
  ref: CitationRef;
  evidence?: EvidenceChunk;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const label = `📖 ${ref.paperTitle.slice(0, 18)}${ref.paperTitle.length > 18 ? "…" : ""} · #${ref.chunkIndex}`;

  return (
    <>
      <Chip
        size="small"
        label={label}
        onClick={(e) => setAnchor(e.currentTarget)}
        clickable={Boolean(evidence)}
        sx={{
          mx: 0.25,
          height: 20,
          fontSize: 11,
          bgcolor: evidence ? "rgba(99,179,237,0.16)" : "rgba(255,255,255,0.06)",
          color: evidence ? "#9cc8f5" : "rgba(255,255,255,0.5)",
          border: evidence
            ? "1px solid rgba(99,179,237,0.4)"
            : "1px solid rgba(255,255,255,0.12)",
          cursor: evidence ? "pointer" : "default",
          "&:hover": evidence
            ? { bgcolor: "rgba(99,179,237,0.26)" }
            : {},
        }}
      />
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
        transformOrigin={{ vertical: "top", horizontal: "center" }}
        slotProps={{
          paper: {
            sx: {
              maxWidth: 460,
              p: 2,
              bgcolor: "#121212",
              border: "1px solid rgba(255,255,255,0.14)",
              borderRadius: 2,
            },
          },
        }}
      >
        {evidence ? (
          <Box>
            <Typography sx={{ fontWeight: 760, fontSize: 14, mb: 0.5 }}>
              {evidence.paper_title}
            </Typography>
            <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", mb: 1 }}>
              <Chip label={`分块 ${evidence.chunk_index}`} size="small" sx={metaChip} />
              {evidence.retrieval_sources.map((s) => (
                <Chip key={s} label={s} size="small" sx={metaChip} />
              ))}
              {evidence.hybrid_score > 0 && (
                <Chip
                  label={`hybrid ${evidence.hybrid_score.toFixed(4)}`}
                  size="small"
                  sx={metaChip}
                />
              )}
            </Box>
            <Typography
              sx={{
                fontSize: 13,
                lineHeight: 1.6,
                color: "rgba(255,255,255,0.78)",
                whiteSpace: "pre-wrap",
              }}
            >
              {evidence.text}
            </Typography>
            {evidence.arxiv_id && (
              <Link
                href={`https://arxiv.org/abs/${evidence.arxiv_id}`}
                target="_blank"
                rel="noreferrer"
                sx={{ fontSize: 12, mt: 1, display: "inline-block" }}
              >
                打开原文 ↗
              </Link>
            )}
          </Box>
        ) : (
          <Typography sx={{ fontSize: 13, color: "rgba(255,255,255,0.6)" }}>
            引用：{ref.paperTitle}（分块 {ref.chunkIndex}）
            <br />
            <em>未找到对应正文切片（可能仅基于摘要生成）。</em>
          </Typography>
        )}
      </Popover>
    </>
  );
}

const metaChip = {
  height: 20,
  fontSize: 10,
  bgcolor: "rgba(255,255,255,0.06)",
  color: "rgba(255,255,255,0.7)",
};

/**
 * 把含引用的文本渲染成 inline 段（用于报告段落内联展示）。
 * 返回 Fragment，文本段是纯字符串，引用段是 CitationChip。
 */
export function RenderTextWithCitations({
  text,
  evidence,
}: {
  text: string;
  evidence: EvidenceChunk[];
}) {
  const segments = useMemo(() => splitByCitations(text, evidence), [text, evidence]);
  return (
    <>
      {segments.map((seg, i) => {
        if (seg.kind === "text") return <Fragment key={i}>{seg.text}</Fragment>;
        return <CitationChip key={i} ref={seg.ref} evidence={seg.evidence} />;
      })}
    </>
  );
}
