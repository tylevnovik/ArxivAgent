import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("renders basic markdown", () => {
    const html = renderMarkdown("# Title\n\ntext");
    expect(html).toContain("<h1>Title</h1>");
    expect(html).toContain("text");
  });

  it("strips <script> tags (XSS hardening)", () => {
    const html = renderMarkdown("<script>alert(1)</script>text");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("alert(1)");
  });

  it("strips <iframe> tags", () => {
    const html = renderMarkdown('<iframe src="https://evil.com"></iframe>');
    expect(html).not.toContain("<iframe");
  });

  it("strips <form> tags", () => {
    const html = renderMarkdown('<form><input name="x"></form>');
    expect(html).not.toContain("<form");
    expect(html).not.toContain("<input");
  });

  it("blocks javascript: href", () => {
    const html = renderMarkdown('<a href="javascript:alert(1)">click</a>');
    expect(html).not.toContain("javascript:");
  });

  it("allows http/https/mailto href", () => {
    const html = renderMarkdown('<a href="https://example.com">ok</a>');
    expect(html).toContain('href="https://example.com"');
  });

  it("strips on* event attributes", () => {
    const html = renderMarkdown('<a href="https://x.com" onclick="alert(1)">x</a>');
    expect(html).not.toContain("onclick");
  });

  it("transforms 【正文…】 markers into citation spans", () => {
    const html = renderMarkdown("见【正文: Some Paper | 分块 2】。");
    expect(html).toContain("aui-citation");
    expect(html).toContain('data-cite-title="Some Paper"');
    expect(html).toContain('data-cite-chunk="2"');
  });

  it("escapes HTML in citation title", () => {
    const html = renderMarkdown("【正文: A<B | 分块 1】");
    // 尖括号应被转义，不应注入真实标签
    expect(html).toContain("&lt;B");
  });
});
