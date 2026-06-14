import { describe, expect, it } from "vitest";
import {
  extractCitations,
  matchEvidence,
  splitByCitations,
} from "./citations-core";
import type { EvidenceChunk } from "./api";

const evidence: EvidenceChunk[] = [
  {
    paper_title: "Attention Is All You Need",
    arxiv_id: "1706.03762",
    chunk_index: "0",
    text: "We propose the Transformer.",
    retrieval_sources: ["dense", "bm25"],
    dense_score: 0.8,
    bm25_score: 2.1,
    hybrid_score: 0.001,
    rerank_score: 0,
    score: 0.001,
  },
  {
    paper_title: "BERT",
    arxiv_id: "1810.04805",
    chunk_index: "3",
    text: "Pre-training of deep bidirectional transformers.",
    retrieval_sources: ["dense"],
    dense_score: 0.7,
    bm25_score: 0,
    hybrid_score: 0,
    rerank_score: 0,
    score: 0,
  },
];

describe("extractCitations", () => {
  it("extracts 【正文: 标题 | 分块 N】 markers", () => {
    const text = "见【正文: Attention Is All You Need | 分块 0】。";
    expect(extractCitations(text)).toEqual([
      { paperTitle: "Attention Is All You Need", chunkIndex: "0" },
    ]);
  });

  it("extracts multiple markers", () => {
    const text = "a【正文: BERT | 分块 3】b【正文: GPT | 分块 1】c";
    expect(extractCitations(text)).toHaveLength(2);
    expect(extractCitations(text)[0]).toEqual({ paperTitle: "BERT", chunkIndex: "3" });
  });

  it("returns empty for plain text", () => {
    expect(extractCitations("无引用的纯文本")).toEqual([]);
  });
});

describe("matchEvidence", () => {
  it("matches by exact title + chunk", () => {
    const ref = { paperTitle: "Attention Is All You Need", chunkIndex: "0" };
    expect(matchEvidence(ref, evidence)?.arxiv_id).toBe("1706.03762");
  });

  it("matches truncated title (LLM may shorten)", () => {
    const ref = { paperTitle: "Attention Is All", chunkIndex: "0" };
    expect(matchEvidence(ref, evidence)?.arxiv_id).toBe("1706.03762");
  });

  it("returns undefined when no match", () => {
    const ref = { paperTitle: "Unknown Paper", chunkIndex: "9" };
    expect(matchEvidence(ref, evidence)).toBeUndefined();
  });
});

describe("splitByCitations", () => {
  it("splits text into plain + citation segments", () => {
    const text = "前【正文: BERT | 分块 3】后";
    const segs = splitByCitations(text, evidence);
    expect(segs[0]).toEqual({ kind: "text", text: "前" });
    expect(segs[1].kind).toBe("citation");
    if (segs[1].kind === "citation") {
      expect(segs[1].ref).toEqual({ paperTitle: "BERT", chunkIndex: "3" });
      expect(segs[1].evidence?.arxiv_id).toBe("1810.04805");
    }
    expect(segs[2]).toEqual({ kind: "text", text: "后" });
  });

  it("marks unmatched citations with undefined evidence", () => {
    const text = "x【正文: Ghost | 分块 5】y";
    const segs = splitByCitations(text, []);
    expect(segs[1].kind).toBe("citation");
    if (segs[1].kind === "citation") expect(segs[1].evidence).toBeUndefined();
  });
});
