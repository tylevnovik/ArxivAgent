"""
Agent 主循环逻辑
实现：理解需求 → 构造检索式 → 执行检索 → 审核结果 → 决策是否迭代
支持：多轮对话、检索错误智能恢复
"""
import json
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Generator, Optional

import config
from core import llm, arxiv_search
from core.arxiv_search import SearchResult, SearchErrorType
from core.memory import Memory


class EventType(Enum):
    """Agent 事件类型"""
    STEP_START = "step_start"       # 步骤开始
    THINKING = "thinking"           # LLM 思考中（流式 token）
    SEARCH_START = "search_start"   # 开始检索
    SEARCH_DONE = "search_done"     # 检索完成
    REVIEW = "review"               # 审核结果
    REFINE = "refine"               # 优化策略
    REPORT = "report"               # 生成报告（流式）
    CHAT_RESPONSE = "chat_response" # 多轮对话回复（流式）
    DONE = "done"                   # 全部完成
    ERROR = "error"                 # 错误


@dataclass
class AgentEvent:
    """Agent 输出事件"""
    event_type: EventType
    content: str = ""           # 文本内容
    data: Optional[dict] = None # 结构化数据
    round_num: int = 0          # 当前轮次
    step_name: str = ""         # 步骤名称



class ArxivAgent:
    """ArXiv 论文检索 Agent（支持多轮对话）"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.DEEPSEEK_API_KEY
        self.memory = Memory()
        self.system_prompt = llm.load_prompt("system.txt")
        self._has_searched = False  # 是否已经进行过检索

    def reset(self):
        """完全重置 Agent 状态"""
        self.memory.reset()
        self._has_searched = False

    @property
    def has_context(self) -> bool:
        """是否有历史上下文（用于判断是否做多轮对话处理）"""
        return self._has_searched or len(self.memory.conversation) > 0

    # ===================== 多轮对话入口 =====================

    def chat(self, user_message: str) -> Generator[AgentEvent, None, None]:
        """
        多轮对话入口。自动判断用户意图：
        - 首次对话 or 新检索需求 → 走完整检索流程
        - 追问/细化 → 带上下文的检索
        - 讨论结果/闲聊 → 直接 LLM 回复
        """
        self.memory.add_conversation("user", user_message)

        if not self.has_context:
            # 首次对话，直接执行检索
            yield from self._run_search(user_message)
            return

        # 有历史上下文，先做意图识别
        yield AgentEvent(EventType.STEP_START, step_name="理解意图",
                         content="🤔 正在理解您的追问...")

        intent_result, intent_text = yield from self._step_followup_intent(user_message)

        if not intent_result:
            # 意图识别失败，当作新检索处理
            yield from self._run_search(user_message)
            return

        intent = intent_result.get("intent", "new_search")
        needs_search = intent_result.get("needs_search", True)
        analysis = intent_result.get("analysis", "")

        yield AgentEvent(EventType.STEP_START, step_name="意图分析",
                         content=f"💡 {analysis}")

        if not needs_search:
            # 不需要检索（讨论结果或闲聊）
            response_text = intent_result.get("response", "")
            if response_text:
                self.memory.add_conversation("assistant", response_text)
                yield AgentEvent(EventType.CHAT_RESPONSE, content=response_text)
                yield AgentEvent(EventType.DONE, step_name="完成",
                                 content="✅ 回复完成",
                                 data={"type": "chat"})
            else:
                # response 为空，使用流式生成
                yield from self._stream_followup_reply(user_message)
            return

        # 需要检索：使用整合后的检索需求
        search_query = intent_result.get("search_query", user_message)

        if intent in ("new_search",):
            # 全新检索，清除旧的检索轮次（但保留对话历史）
            self.memory.search_rounds.clear()
            self.memory.final_papers.clear()
            self.memory.final_report = ""

        yield from self._run_search(search_query)

    # ===================== 完整检索流程 =====================

    def _run_search(self, user_query: str) -> Generator[AgentEvent, None, None]:
        """
        完整检索流程（generator），逐步 yield 事件。
        流程：理解需求 → [检索 → 审核 → (优化)] × N → 生成报告
        """
        self.memory.set_user_query(user_query)

        try:
            # ===== 第一步：理解需求并构造检索式 =====
            yield AgentEvent(EventType.STEP_START, step_name="理解需求",
                             content="📝 正在分析您的检索需求...")

            query_result, thinking_text = yield from self._step_parse_query(
                user_query, round_num=1
            )

            if not query_result:
                yield AgentEvent(EventType.ERROR, content="❌ 无法理解检索需求，请重新描述。")
                return

            arxiv_query = query_result.get("arxiv_query", "")
            strategy = query_result.get("strategy", "")
            sort_by = query_result.get("sort_by", "relevance")
            max_results = min(query_result.get("max_results", 10), config.MAX_RESULTS_PER_ROUND)

            # ===== 迭代检索循环 =====
            for round_num in range(1, config.MAX_SEARCH_ROUNDS + 1):

                # --- 执行检索（含错误恢复） ---
                papers, search_success = yield from self._execute_search_with_recovery(
                    user_query, arxiv_query, max_results, sort_by, round_num
                )

                if not search_success:
                    yield AgentEvent(EventType.ERROR,
                                    content="❌ 多次尝试后检索仍然失败，请尝试换一种方式描述您的需求。")
                    return

                self._has_searched = True

                yield AgentEvent(EventType.SEARCH_DONE, round_num=round_num,
                                 step_name="检索完成",
                                 content=f"✅ 第 {round_num} 轮检索完成，获得 {len(papers)} 篇论文。",
                                 data={"papers": papers})

                if not papers:
                    yield AgentEvent(EventType.STEP_START, round_num=round_num,
                                    content="⚠️ 本轮未检索到论文，将尝试优化检索策略。")

                # --- 审核结果 ---
                yield AgentEvent(EventType.STEP_START, step_name="审核结果",
                                 round_num=round_num,
                                 content=f"🧐 正在审核第 {round_num} 轮检索结果...")

                review_result, review_text = yield from self._step_review(
                    user_query, arxiv_query, papers, round_num
                )

                if not review_result:
                    review_result = {
                        "review_summary": "审核过程出错，保留所有结果",
                        "relevant_papers": [],
                        "overall_quality": 0.5,
                        "should_refine": False,
                        "refine_reason": "",
                        "refine_suggestions": [],
                    }

                # 从审核中获取相关论文
                relevant_indices = {
                    p["index"] for p in review_result.get("relevant_papers", [])
                    if isinstance(p, dict) and "index" in p
                }
                relevant_papers = [
                    papers[i] for i in relevant_indices if i < len(papers)
                ] if relevant_indices else papers

                # 记录本轮
                self.memory.add_search_round(
                    query=arxiv_query,
                    strategy=strategy,
                    results=papers,
                    review=review_result,
                    relevant_papers=relevant_papers,
                )

                yield AgentEvent(EventType.REVIEW, round_num=round_num,
                                 step_name="审核完成",
                                 content=review_text,
                                 data={"review": review_result,
                                       "relevant_papers": relevant_papers})

                # --- 决策：是否需要继续优化 ---
                should_refine = review_result.get("should_refine", False)
                quality = review_result.get("overall_quality", 1.0)

                if not should_refine or round_num >= config.MAX_SEARCH_ROUNDS:
                    if round_num >= config.MAX_SEARCH_ROUNDS and should_refine:
                        yield AgentEvent(EventType.STEP_START,
                                         content=f"⚠️ 已达到最大检索轮次 ({config.MAX_SEARCH_ROUNDS})，停止迭代。")
                    break

                # --- 优化检索策略 ---
                yield AgentEvent(EventType.STEP_START, step_name="优化策略",
                                 round_num=round_num,
                                 content=f"🔄 检索质量 ({quality:.1%}) 不够理想，正在优化检索策略...")

                refine_result, refine_text = yield from self._step_refine(
                    user_query, arxiv_query, review_result, round_num
                )

                if refine_result:
                    arxiv_query = refine_result.get("arxiv_query", arxiv_query)
                    strategy = refine_result.get("changes_made", [])
                    strategy = "；".join(strategy) if isinstance(strategy, list) else str(strategy)
                    sort_by = refine_result.get("sort_by", sort_by)
                    max_results = min(refine_result.get("max_results", max_results),
                                      config.MAX_RESULTS_PER_ROUND)

                    yield AgentEvent(EventType.REFINE, round_num=round_num,
                                     step_name="策略已优化",
                                     content=refine_text,
                                     data={"refine": refine_result})
                else:
                    yield AgentEvent(EventType.STEP_START,
                                     content="⚠️ 优化策略解析失败，使用原始检索式继续。")

            # ===== 生成最终报告 =====
            yield AgentEvent(EventType.STEP_START, step_name="生成报告",
                             content="📊 正在生成最终检索报告...")

            final_papers = self.memory.get_all_relevant_papers()
            report_text = yield from self._step_report(user_query, final_papers)

            self.memory.set_final_results(final_papers, report_text)
            self.memory.add_conversation("assistant", report_text)

            yield AgentEvent(EventType.DONE, step_name="完成",
                             content="✅ 检索完成！",
                             data={"final_papers": final_papers,
                                   "report": report_text})

        except Exception as e:
            tb = traceback.format_exc()
            yield AgentEvent(EventType.ERROR,
                             content=f"❌ Agent 运行出错: {e}\n{tb}")

    # ===================== 带错误恢复的检索 =====================

    def _execute_search_with_recovery(
        self, user_query: str, arxiv_query: str,
        max_results: int, sort_by: str, round_num: int
    ) -> Generator[AgentEvent, None, tuple[list[dict], bool]]:
        """
        执行检索，如果失败则通过 LLM 分析错误并修正检索式重试。
        Returns: (papers, success)
        """
        current_query = arxiv_query

        for recovery_attempt in range(config.MAX_ERROR_RECOVERY + 1):
            is_retry = recovery_attempt > 0
            retry_label = f"（重试 {recovery_attempt}）" if is_retry else ""

            yield AgentEvent(EventType.SEARCH_START, round_num=round_num,
                             step_name="执行检索",
                             content=f"🔍 第 {round_num} 轮检索中{retry_label}...\n检索式: `{current_query}`")

            search_result: SearchResult = arxiv_search.search(
                query=current_query,
                max_results=max_results,
                sort_by=sort_by,
            )

            # 检索成功且有结果
            if search_result.success and search_result.papers:
                return search_result.papers, True

            # 有错误或结果为空
            error = search_result.error
            if error is None:
                # 不应该发生，但安全处理
                return search_result.papers, search_result.success

            error_msg = str(error)
            yield AgentEvent(EventType.STEP_START, round_num=round_num,
                             content=f"⚠️ 检索出现问题: {error_msg}")

            # 判断是否可恢复
            if not error.recoverable:
                yield AgentEvent(EventType.ERROR,
                                content=f"❌ 不可恢复的检索错误: {error_msg}")
                return [], False

            # 结果为空但检索本身成功 — 也需要 LLM 调整
            if error.error_type == SearchErrorType.EMPTY_RESULT and search_result.success:
                if recovery_attempt >= config.MAX_ERROR_RECOVERY:
                    # 最后一次，返回空结果让后续流程处理
                    yield AgentEvent(EventType.STEP_START,
                                     content="⚠️ 多次检索均无结果，将尝试其他策略。")
                    return [], True

            # 尝试 LLM 错误恢复
            if recovery_attempt < config.MAX_ERROR_RECOVERY:
                yield AgentEvent(EventType.STEP_START, step_name="错误恢复",
                                 round_num=round_num,
                                 content=f"🔧 正在分析错误并调整检索策略（第 {recovery_attempt + 1} 次恢复）...")

                recovery_result, recovery_text = yield from self._step_error_recovery(
                    user_query, current_query, error_msg, round_num
                )

                if recovery_result and recovery_result.get("should_retry", False):
                    new_query = recovery_result.get("arxiv_query", "")
                    if new_query and new_query != current_query:
                        fix_strategy = recovery_result.get("fix_strategy", "")
                        yield AgentEvent(EventType.STEP_START, round_num=round_num,
                                         content=f"🔧 **错误恢复策略**: {fix_strategy}\n新检索式: `{new_query}`")
                        current_query = new_query
                        continue
                    else:
                        yield AgentEvent(EventType.STEP_START,
                                         content="⚠️ LLM 未能生成有效的修正检索式。")
                else:
                    yield AgentEvent(EventType.STEP_START,
                                     content="⚠️ 错误恢复分析未能给出可重试的方案。")

        # 所有恢复尝试都失败
        return [], False

    # ======================== 各步骤实现 ========================

    def _step_parse_query(self, user_query: str, round_num: int):
        """理解需求 + 构造检索式"""
        prompt_content = llm.load_prompt(
            "query_parse.txt",
            user_query=user_query,
            search_memory=self.memory.get_search_memory_text(),
        )
        messages = llm.build_messages(self.system_prompt, prompt_content)

        full_text = ""
        for token in llm.stream_chat(messages, self.api_key):
            full_text += token
            yield AgentEvent(EventType.THINKING, content=token, round_num=round_num)

        try:
            result = llm.parse_json_response(full_text)
            return result, full_text
        except ValueError as e:
            yield AgentEvent(EventType.ERROR, content=f"⚠️ 解析需求理解结果失败: {e}")
            return None, full_text

    def _step_review(self, user_query: str, arxiv_query: str,
                     papers: list[dict], round_num: int):
        """审核检索结果"""
        papers_text = arxiv_search.format_papers_for_llm(papers)
        prompt_content = llm.load_prompt(
            "result_review.txt",
            user_query=user_query,
            arxiv_query=arxiv_query,
            search_results=papers_text,
            search_memory=self.memory.get_search_memory_text(),
        )
        messages = llm.build_messages(self.system_prompt, prompt_content)

        full_text = ""
        for token in llm.stream_chat(messages, self.api_key):
            full_text += token
            yield AgentEvent(EventType.THINKING, content=token, round_num=round_num)

        try:
            result = llm.parse_json_response(full_text)
            return result, full_text
        except ValueError as e:
            yield AgentEvent(EventType.ERROR, content=f"⚠️ 解析审核结果失败: {e}")
            return None, full_text

    def _step_refine(self, user_query: str, previous_query: str,
                     review_result: dict, round_num: int):
        """优化检索策略"""
        prompt_content = llm.load_prompt(
            "refine_query.txt",
            user_query=user_query,
            previous_query=previous_query,
            review_feedback=json.dumps(review_result, ensure_ascii=False),
            search_memory=self.memory.get_search_memory_text(),
        )
        messages = llm.build_messages(self.system_prompt, prompt_content)

        full_text = ""
        for token in llm.stream_chat(messages, self.api_key):
            full_text += token
            yield AgentEvent(EventType.THINKING, content=token, round_num=round_num)

        try:
            result = llm.parse_json_response(full_text)
            return result, full_text
        except ValueError as e:
            yield AgentEvent(EventType.ERROR, content=f"⚠️ 解析优化策略失败: {e}")
            return None, full_text

    def _step_report(self, user_query: str, final_papers: list[dict]):
        """生成最终报告"""
        papers_text = arxiv_search.format_papers_for_llm(final_papers)
        prompt_content = llm.load_prompt(
            "summary.txt",
            user_query=user_query,
            full_search_memory=self.memory.get_search_memory_text(),
            final_papers=papers_text,
        )
        messages = llm.build_messages(self.system_prompt, prompt_content)

        full_text = ""
        for token in llm.stream_chat(messages, self.api_key):
            full_text += token
            yield AgentEvent(EventType.REPORT, content=token)

        return full_text

    def _step_error_recovery(self, user_query: str, failed_query: str,
                             error_message: str, round_num: int):
        """LLM 分析检索错误并生成修正方案"""
        prompt_content = llm.load_prompt(
            "error_recovery.txt",
            user_query=user_query,
            failed_query=failed_query,
            error_message=error_message,
            search_memory=self.memory.get_search_memory_text(),
        )
        messages = llm.build_messages(self.system_prompt, prompt_content)

        full_text = ""
        for token in llm.stream_chat(messages, self.api_key):
            full_text += token
            yield AgentEvent(EventType.THINKING, content=token, round_num=round_num)

        try:
            result = llm.parse_json_response(full_text)
            return result, full_text
        except ValueError as e:
            yield AgentEvent(EventType.ERROR, content=f"⚠️ 解析错误恢复方案失败: {e}")
            return None, full_text

    def _step_followup_intent(self, user_message: str):
        """分析多轮对话中的用户意图"""
        # 构建对话历史文本
        conv_text = self._format_conversation_history()
        papers_text = arxiv_search.format_papers_for_llm(self.memory.get_all_relevant_papers())

        prompt_content = llm.load_prompt(
            "followup_chat.txt",
            user_message=user_message,
            conversation_history=conv_text,
            search_memory=self.memory.get_search_memory_text(),
            recommended_papers=papers_text,
        )
        messages = llm.build_messages(self.system_prompt, prompt_content)

        full_text = ""
        for token in llm.stream_chat(messages, self.api_key):
            full_text += token
            yield AgentEvent(EventType.THINKING, content=token)

        try:
            result = llm.parse_json_response(full_text)
            return result, full_text
        except ValueError as e:
            yield AgentEvent(EventType.ERROR, content=f"⚠️ 解析意图识别结果失败: {e}")
            return None, full_text

    def _stream_followup_reply(self, user_message: str):
        """流式生成多轮对话回复（不涉及检索）"""
        conv_text = self._format_conversation_history()
        papers_text = arxiv_search.format_papers_for_llm(self.memory.get_all_relevant_papers())

        user_content = (
            f"用户的消息: {user_message}\n\n"
            f"对话历史:\n{conv_text}\n\n"
            f"之前推荐的论文:\n{papers_text}\n\n"
            f"请直接用中文回复用户的问题。不需要检索，只需要基于已有信息回答。"
        )
        messages = llm.build_messages(self.system_prompt, user_content)

        full_text = ""
        for token in llm.stream_chat(messages, self.api_key):
            full_text += token
            yield AgentEvent(EventType.CHAT_RESPONSE, content=token)

        self.memory.add_conversation("assistant", full_text)
        yield AgentEvent(EventType.DONE, step_name="完成",
                         content="✅ 回复完成",
                         data={"type": "chat"})

    def _format_conversation_history(self, max_turns: int = 10) -> str:
        """格式化最近的对话历史"""
        recent = self.memory.conversation[-max_turns * 2:] if self.memory.conversation else []
        if not recent:
            return "（无对话历史）"

        lines = []
        for msg in recent:
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"][:300]
            if len(msg["content"]) > 300:
                content += "..."
            lines.append(f"[{role}]: {content}")

        return "\n".join(lines)
