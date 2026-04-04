"""
ArXiv 论文检索 Agent - Gradio UI
支持流式输出、实时检索状态展示、多格式导出
"""
import json
import gradio as gr
from datetime import datetime

from core.agent import ArxivAgent, EventType, AgentEvent
from core.memory import Memory
from core import exporter
import config

# ===================== 全局状态 =====================
_agent: ArxivAgent = None
_current_papers: list[dict] = []  # 当前轮次的论文
_all_papers: list[dict] = []      # 所有相关论文


def get_agent(api_key: str = None) -> ArxivAgent:
    """获取或创建 Agent 实例"""
    global _agent
    if _agent is None:
        _agent = ArxivAgent(api_key=api_key)
    elif api_key:
        _agent.api_key = api_key
    return _agent


# ===================== 核心交互逻辑 =====================

def run_search(user_query: str, api_key: str, chat_history: list):
    """
    运行 Agent 对话/检索流程（generator，支持流式输出和多轮对话）
    Yields: (chat_history, status_text, papers_md, final_md)
    """
    global _current_papers, _all_papers

    if not user_query.strip():
        yield chat_history, "⚠️ 请输入检索需求", "", ""
        return

    effective_key = api_key.strip() if api_key.strip() else config.DEEPSEEK_API_KEY
    if not effective_key:
        yield chat_history, "⚠️ 请提供 DeepSeek API Key", "", ""
        return

    agent = get_agent(effective_key)
    # 不再每次 reset — 保留多轮对话上下文

    # 添加用户消息到 UI 对话
    chat_history = chat_history or []
    chat_history.append({"role": "user", "content": user_query})

    # 用于累积 assistant 消息
    step_buffer = ""           # 当前步骤标题
    thinking_buffer = ""       # LLM 流式内容
    report_buffer = ""         # 报告流式内容
    chat_response_buffer = ""  # 多轮对话回复流式内容
    status_text = ""
    papers_md = ""
    final_md = ""

    # 使用 agent.chat() 支持多轮对话
    for event in agent.chat(user_query):
        et = event.event_type

        if et == EventType.STEP_START:
            # 新步骤开始 — 将之前的思考内容刷新到对话
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""

            step_buffer = event.step_name or ""
            status_text = event.content
            if event.content:
                chat_history.append({"role": "assistant",
                                     "content": event.content})
            yield chat_history, status_text, papers_md, final_md

        elif et == EventType.THINKING:
            thinking_buffer += event.content
            # 每累积一些内容就更新展示
            display = _format_thinking(step_buffer, thinking_buffer)
            # 更新最后一条或追加
            if (chat_history and chat_history[-1]["role"] == "assistant"
                    and chat_history[-1].get("_thinking")):
                chat_history[-1]["content"] = display
            else:
                chat_history.append({"role": "assistant",
                                     "content": display,
                                     "_thinking": True})
            yield chat_history, status_text, papers_md, final_md

        elif et == EventType.SEARCH_START:
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""
            status_text = event.content
            chat_history.append({"role": "assistant", "content": event.content})
            yield chat_history, status_text, papers_md, final_md

        elif et == EventType.SEARCH_DONE:
            status_text = event.content
            papers = event.data.get("papers", []) if event.data else []
            _current_papers = papers
            papers_md = _format_papers_table(papers, event.round_num)
            chat_history.append({"role": "assistant", "content": event.content})
            yield chat_history, status_text, papers_md, final_md

        elif et == EventType.REVIEW:
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""
            relevant = event.data.get("relevant_papers", []) if event.data else []
            review = event.data.get("review", {}) if event.data else {}
            summary = review.get("review_summary", "")
            quality = review.get("overall_quality", "N/A")
            should_refine = review.get("should_refine", False)

            review_msg = (
                f"📋 **审核结果** (质量: {quality})\n\n"
                f"{summary}\n\n"
                f"筛选出 **{len(relevant)}** 篇相关论文。"
            )
            if should_refine:
                reason = review.get("refine_reason", "")
                review_msg += f"\n\n🔄 建议优化: {reason}"

            _all_papers.extend(relevant)
            chat_history.append({"role": "assistant", "content": review_msg})
            status_text = f"审核完成 | 质量: {quality}"
            yield chat_history, status_text, papers_md, final_md

        elif et == EventType.REFINE:
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""
            refine_data = event.data.get("refine", {}) if event.data else {}
            new_query = refine_data.get("arxiv_query", "")
            changes = refine_data.get("changes_made", [])
            changes_str = "\n".join(f"  - {c}" for c in changes) if changes else ""

            refine_msg = f"🔄 **策略优化完成**\n\n新检索式: `{new_query}`"
            if changes_str:
                refine_msg += f"\n\n调整:\n{changes_str}"

            chat_history.append({"role": "assistant", "content": refine_msg})
            yield chat_history, "策略已优化，准备下一轮检索...", papers_md, final_md

        elif et == EventType.REPORT:
            report_buffer += event.content
            final_md = report_buffer
            # 流式更新报告到对话
            if (chat_history and chat_history[-1]["role"] == "assistant"
                    and chat_history[-1].get("_report")):
                chat_history[-1]["content"] = f"📊 **最终报告**\n\n{report_buffer}"
            else:
                chat_history.append({"role": "assistant",
                                     "content": f"📊 **最终报告**\n\n{report_buffer}",
                                     "_report": True})
            yield chat_history, "正在生成报告...", papers_md, final_md

        elif et == EventType.CHAT_RESPONSE:
            # 多轮对话的流式回复
            chat_response_buffer += event.content
            if (chat_history and chat_history[-1]["role"] == "assistant"
                    and chat_history[-1].get("_chat_reply")):
                chat_history[-1]["content"] = chat_response_buffer
            else:
                chat_history.append({"role": "assistant",
                                     "content": chat_response_buffer,
                                     "_chat_reply": True})
            status_text = "💬 正在回复..."
            yield chat_history, status_text, papers_md, final_md

        elif et == EventType.DONE:
            if thinking_buffer:
                _flush_thinking(chat_history, step_buffer, thinking_buffer)
                thinking_buffer = ""
            done_data = event.data or {}
            if done_data.get("type") == "chat":
                # 多轮对话回复完成
                status_text = "✅ 回复完成"
            else:
                final_papers = done_data.get("final_papers", [])
                _all_papers = final_papers
                final_md = done_data.get("report", final_md)
                status_text = f"✅ 检索完成！共推荐 {len(final_papers)} 篇论文。"
            yield chat_history, status_text, papers_md, final_md

        elif et == EventType.ERROR:
            chat_history.append({"role": "assistant", "content": event.content})
            status_text = "❌ 出错"
            yield chat_history, status_text, papers_md, final_md


def _flush_thinking(chat_history, step_name, thinking_text):
    """将思考内容整理后追加到对话"""
    # 清理原始 JSON（只展示摘要）
    summary = _extract_thinking_summary(thinking_text)
    if summary:
        # 替换掉 _thinking 标记的条目
        if (chat_history and chat_history[-1]["role"] == "assistant"
                and chat_history[-1].get("_thinking")):
            chat_history[-1] = {"role": "assistant",
                                "content": f"💭 **{step_name or 'Agent 思考'}**\n\n{summary}"}
        else:
            chat_history.append({"role": "assistant",
                                 "content": f"💭 **{step_name or 'Agent 思考'}**\n\n{summary}"})


def _format_thinking(step_name, text):
    """格式化实时思考内容"""
    # 截取末尾显示，避免太长
    display_text = text[-800:] if len(text) > 800 else text
    if len(text) > 800:
        display_text = "...\n" + display_text
    return f"💭 **{step_name or 'Agent 思考中...'}**\n\n```\n{display_text}\n```"


def _extract_thinking_summary(text):
    """从 LLM 思考输出中提取关键信息摘要"""
    try:
        data = json.loads(text[text.find('{'):text.rfind('}') + 1])
        parts = []
        if "understanding" in data:
            parts.append(f"**理解**: {data['understanding']}")
        if "arxiv_query" in data:
            parts.append(f"**检索式**: `{data['arxiv_query']}`")
        if "strategy" in data:
            parts.append(f"**策略**: {data['strategy']}")
        if "optimization_analysis" in data:
            parts.append(f"**优化分析**: {data['optimization_analysis']}")
        if "review_summary" in data:
            parts.append(f"**审核**: {data['review_summary']}")
        if "overall_quality" in data:
            parts.append(f"**质量**: {data['overall_quality']}")
        if parts:
            return "\n\n".join(parts)
    except (json.JSONDecodeError, ValueError):
        pass
    # JSON 解析失败则截取前 300 字
    return text[:300] + ("..." if len(text) > 300 else "")


def _format_papers_table(papers: list[dict], round_num: int = 0) -> str:
    """将论文列表格式化为 Markdown 展示"""
    if not papers:
        return f"### 第 {round_num} 轮检索结果\n\n暂无结果"

    lines = [f"### 第 {round_num} 轮检索结果（共 {len(papers)} 篇）\n"]
    for i, p in enumerate(papers):
        title = p.get("title", "无标题")
        authors = ", ".join(p.get("authors", [])[:3])
        if len(p.get("authors", [])) > 3:
            authors += " 等"
        date = p.get("published", "")[:10]
        link = p.get("link", "")
        cats = ", ".join(p.get("categories", [])[:2])
        abstract = p.get("abstract", "")[:150]

        lines.append(f"**{i+1}. [{title}]({link})**")
        lines.append(f"   📅 {date} | 👤 {authors} | 🏷️ {cats}")
        lines.append(f"   > {abstract}...")
        lines.append("")

    return "\n".join(lines)


# ===================== 导出功能 =====================

def export_conversation(chat_history):
    """导出完整的UI主对话记录(包括中间思路和最终报告)"""
    if not chat_history:
        return None, "⚠️ 暂无对话记录可导出"
        
    lines = [
        "# ArXiv Agent 完整交互记录",
        f"**导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        ""
    ]
    
    for msg in chat_history:
        role_label = "🧑 用户" if msg.get("role") == "user" else "🤖 助手"
        lines.append(f"### {role_label}")
        lines.append("")
        
        content_val = msg.get("content", "")
        if isinstance(content_val, list) or isinstance(content_val, tuple):
            text_parts = []
            for part in content_val:
                if isinstance(part, dict) and "text" in part:
                    text_parts.append(part.get("text", ""))
                else:
                    text_parts.append(str(part))
            lines.append("".join(text_parts))
        else:
            lines.append(str(content_val))
            
        lines.append("")
        lines.append("---")
        lines.append("")
        
    content = "\n".join(lines)
    filepath = exporter.save_export(
        content, f"完整对话记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    return filepath, f"✅ 对话已导出: {filepath}"


def export_results_md(api_key: str):
    """导出检索结果 Markdown"""
    agent = get_agent(api_key)
    if not agent.memory.search_rounds:
        return None, "⚠️ 暂无检索结果可导出"
    content = exporter.export_search_results_md(agent.memory)
    filepath = exporter.save_export(
        content, f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    return filepath, f"✅ 检索结果已导出: {filepath}"


def export_results_csv(api_key: str):
    """导出检索结果 CSV"""
    agent = get_agent(api_key)
    if not agent.memory.search_rounds:
        return None, "⚠️ 暂无检索结果可导出"
    content = exporter.export_search_results_csv(agent.memory)
    filepath = exporter.save_export(
        content, f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    return filepath, f"✅ CSV 已导出: {filepath}"


def export_results_json(api_key: str):
    """导出检索结果 JSON"""
    agent = get_agent(api_key)
    if not agent.memory.search_rounds:
        return None, "⚠️ 暂无检索结果可导出"
    content = exporter.export_search_results_json(agent.memory)
    filepath = exporter.save_export(
        content, f"检索结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    return filepath, f"✅ JSON 已导出: {filepath}"


def export_report(api_key: str):
    """导出最终报告"""
    agent = get_agent(api_key)
    content = exporter.export_final_report(agent.memory)
    if not content or content.strip() == "":
        return None, "⚠️ 暂无报告可导出"
    filepath = exporter.save_export(
        content, f"最终报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    return filepath, f"✅ 报告已导出: {filepath}"


def clear_all():
    """清除所有状态"""
    global _agent, _current_papers, _all_papers
    if _agent:
        _agent.reset()
    _current_papers = []
    _all_papers = []
    return [], "已清除所有数据", "", ""


# ===================== 构建 Gradio UI =====================

def build_ui():
    """构建 Gradio 界面"""

    custom_css = """
    /* ===== 全局主题 ===== */
    .gradio-container {
        max-width: 1400px !important;
        margin: 0 auto !important;
        font-family: 'Inter', 'Noto Sans SC', system-ui, -apple-system, sans-serif !important;
    }

    /* ===== 顶部标题区 ===== */
    .app-header {
        background: linear-gradient(135deg, #0f0c29, #302b63, #24243e) !important;
        border-radius: 16px !important;
        padding: 24px 32px !important;
        margin-bottom: 16px !important;
        color: white !important;
        text-align: center !important;
        box-shadow: 0 8px 32px rgba(48, 43, 99, 0.4) !important;
    }
    .app-header h1 {
        margin: 0 !important;
        font-size: 1.8em !important;
        font-weight: 700 !important;
        background: linear-gradient(90deg, #67e8f9, #a78bfa, #f472b6) !important;
        -webkit-background-clip: text !important;
        -webkit-text-fill-color: transparent !important;
    }
    .app-header p {
        margin: 6px 0 0 0 !important;
        color: #a5b4fc !important;
        font-size: 0.95em !important;
    }

    /* ===== 聊天区域 ===== */
    .chat-panel {
        border: 1px solid rgba(99, 102, 241, 0.2) !important;
        border-radius: 12px !important;
        background: rgba(15, 12, 41, 0.02) !important;
    }

    /* ===== 状态栏 ===== */
    .status-bar textarea {
        background: linear-gradient(90deg, #1e1b4b, #312e81) !important;
        color: #c7d2fe !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 500 !important;
        padding: 10px 16px !important;
        font-size: 0.9em !important;
    }

    /* ===== 结果面板 ===== */
    .results-panel {
        border: 1px solid rgba(99, 102, 241, 0.15) !important;
        border-radius: 12px !important;
        min-height: 300px !important;
    }

    /* ===== 按钮样式 ===== */
    .export-btn {
        border-radius: 8px !important;
        font-weight: 500 !important;
        transition: all 0.3s ease !important;
    }
    .primary-btn {
        background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
        border: none !important;
        color: white !important;
        font-weight: 600 !important;
        border-radius: 10px !important;
        box-shadow: 0 4px 15px rgba(99, 102, 241, 0.35) !important;
    }
    .primary-btn:hover {
        box-shadow: 0 6px 20px rgba(99, 102, 241, 0.5) !important;
        transform: translateY(-1px) !important;
    }
    .danger-btn {
        background: linear-gradient(135deg, #ef4444, #dc2626) !important;
        border: none !important;
        color: white !important;
    }

    /* ===== Tab 标签 ===== */
    .tab-nav button {
        font-weight: 500 !important;
        border-radius: 8px 8px 0 0 !important;
    }
    .tab-nav button.selected {
        background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
        color: white !important;
    }
    """

    with gr.Blocks(
        title="ArXiv 论文检索 Agent",
        css=custom_css,
        theme=gr.themes.Soft(
            primary_hue="indigo",
            secondary_hue="violet",
            neutral_hue="slate",
            font=[
                gr.themes.GoogleFont("Inter"),
                gr.themes.GoogleFont("Noto Sans SC"),
                "system-ui",
                "sans-serif",
            ],
        ),
    ) as demo:

        # ---------- 顶部标题 ----------
        gr.HTML("""
        <div class="app-header">
            <h1>🔍 ArXiv 论文检索 Agent</h1>
            <p>由 DeepSeek 大模型驱动 · 自然语言检索 · 智能迭代优化 · 实时流式展示</p>
        </div>
        """)

        # ---------- API Key ----------
        with gr.Row():
            api_key_input = gr.Textbox(
                label="🔑 DeepSeek API Key",
                placeholder="留空则使用默认配置",
                type="password",
                scale=3,
            )
            status_bar = gr.Textbox(
                label="📡 Agent 状态",
                value="就绪，等待输入...",
                interactive=False,
                scale=2,
                elem_classes=["status-bar"],
            )

        # ---------- 主体区域：左对话 + 右结果 ----------
        with gr.Row(equal_height=True):
            # ---- 左侧：对话 ----
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    label="💬 对话",
                    height=520,
                    buttons=["copy", "copy_all"],
                    elem_classes=["chat-panel"],
                    avatar_images=(None, None),
                )
                with gr.Row():
                    user_input = gr.Textbox(
                        label="输入检索需求或追问",
                        placeholder="例如：最近关于大语言模型推理能力的论文 / 只看2024年以后的 / 第3篇讲了什么",
                        scale=5,
                        lines=1,
                    )
                    send_btn = gr.Button(
                        "🚀 发送",
                        variant="primary",
                        scale=1,
                        elem_classes=["primary-btn"],
                    )

            # ---- 右侧：结果 Tabs ----
            with gr.Column(scale=2):
                with gr.Tabs():
                    with gr.Tab("📑 检索结果"):
                        papers_display = gr.Markdown(
                            value="*等待检索...*",
                            elem_classes=["results-panel"],
                        )
                    with gr.Tab("📊 最终报告"):
                        report_display = gr.Markdown(
                            value="*检索完成后将在此展示最终报告*",
                            elem_classes=["results-panel"],
                        )

        # ---------- 底部：导出 & 清除 ----------
        with gr.Row():
            export_conv_btn = gr.Button("💾 导出对话", elem_classes=["export-btn"])
            export_md_btn = gr.Button("📄 导出结果(MD)", elem_classes=["export-btn"])
            export_csv_btn = gr.Button("📊 导出结果(CSV)", elem_classes=["export-btn"])
            export_json_btn = gr.Button("🗂️ 导出结果(JSON)", elem_classes=["export-btn"])
            export_report_btn = gr.Button("📋 导出报告", elem_classes=["export-btn"])
            clear_btn = gr.Button("🗑️ 清除全部", elem_classes=["danger-btn"])

        with gr.Row():
            export_file = gr.File(label="📥 下载导出文件", visible=True)
            export_status = gr.Textbox(label="导出状态", interactive=False)

        # ==================== 事件绑定 ====================

        # 发送检索
        send_btn.click(
            fn=run_search,
            inputs=[user_input, api_key_input, chatbot],
            outputs=[chatbot, status_bar, papers_display, report_display],
        ).then(
            fn=lambda: "",
            outputs=[user_input],
        )

        # Enter 键发送
        user_input.submit(
            fn=run_search,
            inputs=[user_input, api_key_input, chatbot],
            outputs=[chatbot, status_bar, papers_display, report_display],
        ).then(
            fn=lambda: "",
            outputs=[user_input],
        )

        # 导出按钮
        export_conv_btn.click(
            fn=export_conversation,
            inputs=[chatbot],
            outputs=[export_file, export_status],
        )
        export_md_btn.click(
            fn=export_results_md,
            inputs=[api_key_input],
            outputs=[export_file, export_status],
        )
        export_csv_btn.click(
            fn=export_results_csv,
            inputs=[api_key_input],
            outputs=[export_file, export_status],
        )
        export_json_btn.click(
            fn=export_results_json,
            inputs=[api_key_input],
            outputs=[export_file, export_status],
        )
        export_report_btn.click(
            fn=export_report,
            inputs=[api_key_input],
            outputs=[export_file, export_status],
        )

        # 清除
        clear_btn.click(
            fn=clear_all,
            outputs=[chatbot, status_bar, papers_display, report_display],
        )

    return demo


# ===================== 入口 =====================
if __name__ == "__main__":
    demo = build_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
    )
