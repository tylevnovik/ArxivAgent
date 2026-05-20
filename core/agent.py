"""
Agent 主循环逻辑
实现：理解需求 → 构造检索式 → 执行检索 → 审核结果 → 决策是否迭代
支持：多轮对话、检索错误智能恢复
"""
import json
import re
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Generator, Optional

import config
from core import llm, arxiv_search
from core.arxiv_search import SearchResult, SearchErrorType
from core.memory import Memory
from core.search_service import SearchService


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

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None,
                 max_search_rounds: int = None, max_results_per_round: int = None,
                 providers: list[str] = None, provider_settings: dict[str, str] = None):
        self.api_key = api_key or config.DEEPSEEK_API_KEY
        self.base_url = base_url or config.DEEPSEEK_BASE_URL
        self.model = model or config.DEEPSEEK_MODEL
        self.max_search_rounds = max_search_rounds or config.MAX_SEARCH_ROUNDS
        self.max_results_per_round = max_results_per_round or config.MAX_RESULTS_PER_ROUND
        self.memory = Memory()
        self.system_prompt = llm.load_prompt("system.txt")
        self._has_searched = False  # 是否已经进行过检索
        self.provider_names = providers
        self.provider_settings = provider_settings or {}
        self.search_service = SearchService(
            providers=self.provider_names,
            provider_settings=self.provider_settings,
        )

    def update_config(self, api_key: str = None, base_url: str = None, model: str = None,
                      max_search_rounds: int = None, max_results_per_round: int = None,
                      providers: list[str] = None, provider_settings: dict[str, str] = None):
        """更新 Agent 运行配置"""
        if api_key:
            self.api_key = api_key
        if base_url:
            self.base_url = base_url
        if model:
            self.model = model
        if max_search_rounds is not None:
            self.max_search_rounds = max_search_rounds
        if max_results_per_round is not None:
            self.max_results_per_round = max_results_per_round
        if providers is not None:
            self.provider_names = providers
        if provider_settings is not None:
            self.provider_settings = provider_settings
        if providers is not None or provider_settings is not None:
            self.search_service = SearchService(
                providers=self.provider_names,
                provider_settings=self.provider_settings,
            )

    def reset(self):
        """完全重置 Agent 状态"""
        self.memory.reset()
        self._has_searched = False

    def _stream_chat(self, messages: list):
        """代理流式对话调用，支持动态参数"""
        return llm.stream_chat(messages, api_key=self.api_key, base_url=self.base_url, model=self.model)

    @property
    def has_context(self) -> bool:
        """是否有历史上下文（用于判断是否做多轮对话处理）"""
        return self._has_searched or any(
            msg.get("role") == "assistant" for msg in self.memory.conversation
        )

    @staticmethod
    def quick_intent(
        user_message: str,
        has_context: bool = False,
        previous_query: str = "",
    ) -> Optional[dict]:
        """本地轻量意图识别，拦截明确闲聊和上下文修正，避免无意义检索。"""
        text = re.sub(r"\s+", " ", (user_message or "").strip())
        if not text:
            return None

        lower = text.lower()
        has_search_marker = bool(re.search(
            r"(论文|文献|检索|搜索|查|找|推荐|arxiv|paper|papers|survey|综述|"
            r"研究|方法|模型|算法|数据集|benchmark|最新|近年|20\d{2})",
            lower,
        ))

        short_chat_patterns = [
            r"^(你好|您好|哈喽|嗨|在吗|早上好|晚上好|下午好)[。！？!,.，\s]*$",
            r"^(hi|hello|hey)[!,.，。\s]*$",
            r"^(谢谢|感谢|辛苦了|好的|好|ok|收到|明白|再见|拜拜)[。！？!,.，\s]*$",
            r"^(你是谁|你能做什么|怎么用)[。！？!,.，\s]*$",
        ]
        if not has_search_marker and any(re.match(p, lower) for p in short_chat_patterns):
            context_hint = "也可以继续基于刚才的结果追问或让我重新筛选。" if has_context else "把主题、年份、方向或关键词发给我，我就能开始检索。"
            return {
                "intent": "general_chat",
                "analysis": "识别为闲聊，不需要启动论文检索。",
                "needs_search": False,
                "search_query": "",
                "response": f"你好！我在。{context_hint}",
            }

        if has_context:
            refine_marker = bool(re.search(
                r"(改成|改为|换成|不是|而是|只要|不要|排除|限定|侧重|偏向|"
                r"重新|再搜|再找|补充|更多|更近|更新|修正|纠正)",
                text,
            ))
            discuss_marker = bool(re.search(
                r"(第\s*\d+\s*篇|哪篇|这篇|比较|总结|解释|讲了什么|为什么|"
                r"适合|区别|优缺点|怎么理解)",
                text,
            ))
            if refine_marker and not discuss_marker:
                base = previous_query or "上一轮检索需求"
                return {
                    "intent": "refine_search",
                    "analysis": "识别为对上一轮检索需求的修正，需要重新检索。",
                    "needs_search": True,
                    "search_query": f"原始需求：{base}\n用户修正：{text}",
                    "response": "",
                }

        return None

    # ===================== 多轮对话入口 =====================

    def chat(self, user_message: str) -> Generator[AgentEvent, None, None]:
        """
        多轮对话入口。自动判断用户意图：
        - 首次对话 or 新检索需求 → 走完整检索流程
        - 追问/细化 → 带上下文的检索
        - 讨论结果/闲聊 → 直接 LLM 回复
        """
        had_context = self.has_context
        self.memory.add_conversation("user", user_message)

        quick_intent = self.quick_intent(
            user_message,
            has_context=had_context,
            previous_query=self.memory.user_query,
        )
        if quick_intent:
            yield AgentEvent(
                EventType.STEP_START,
                step_name="意图识别",
                content=f"💡 {quick_intent.get('analysis', '')}",
            )
            if not quick_intent.get("needs_search", True):
                response_text = quick_intent.get("response", "")
                self.memory.add_conversation("assistant", response_text)
                yield AgentEvent(EventType.CHAT_RESPONSE, content=response_text)
                yield AgentEvent(
                    EventType.DONE,
                    step_name="完成",
                    content="✅ 回复完成",
                    data={"type": "chat"},
                )
                return

            search_query = quick_intent.get("search_query") or user_message
            if quick_intent.get("intent") == "new_search":
                self.memory.search_rounds.clear()
                self.memory.final_papers.clear()
                self.memory.final_report = ""
            yield from self._run_search(search_query)
            return

        # 首轮也先做意图识别，避免把闲聊误判为完整检索。
        yield AgentEvent(EventType.STEP_START, step_name="理解意图",
                         content="🤔 正在判断这是闲聊、检索还是修正...")

        intent_result, intent_text = yield from self._step_followup_intent(user_message)

        if not intent_result:
            # 意图识别失败时，保守地按检索处理。
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
            keywords = query_result.get("keywords", [])
            provider_query = user_query
            if keywords:
                provider_query = " ".join(str(k) for k in keywords)
            strategy = query_result.get("strategy", "")
            sort_by = query_result.get("sort_by", "relevance")
            max_results = min(query_result.get("max_results", 10), self.max_results_per_round)

            # ===== 迭代检索循环 =====
            for round_num in range(1, self.max_search_rounds + 1):

                # --- 执行检索（含错误恢复） ---
                papers, search_success = yield from self._execute_search_with_recovery(
                    provider_query, arxiv_query, max_results, sort_by, round_num
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
                reviewed_papers = review_result.get("relevant_papers")
                relevant_indices = {
                    p["index"] for p in reviewed_papers or []
                    if isinstance(p, dict) and "index" in p
                }
                if reviewed_papers is None:
                    relevant_papers = papers
                else:
                    relevant_papers = [
                        papers[i] for i in relevant_indices if i < len(papers)
                    ]

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

                if not should_refine or round_num >= self.max_search_rounds:
                    if round_num >= self.max_search_rounds and should_refine:
                        yield AgentEvent(EventType.STEP_START,
                                         content=f"⚠️ 已达到最大检索轮次 ({self.max_search_rounds})，停止迭代。")
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
                                      self.max_results_per_round)

                    yield AgentEvent(EventType.REFINE, round_num=round_num,
                                     step_name="策略已优化",
                                     content=refine_text,
                                     data={"refine": refine_result})
                else:
                    yield AgentEvent(EventType.STEP_START,
                                     content="⚠️ 优化策略解析失败，使用原始检索式继续。")

            # ===== 生成最终报告 =====
            final_papers = self.memory.get_all_relevant_papers()

            # --- 启动 PDF 下载与 RAG 建库 ---
            if final_papers:
                yield AgentEvent(EventType.STEP_START, step_name="解析正文",
                                 content=f"📥 正在获取并解析 {len(final_papers)} 篇论文的正文进行 RAG 分析...")
                all_chunks = []
                from core import pdf_parser
                for p in final_papers:
                    pdf_link = p.get("pdf_link", "")
                    title = p.get("title", "无标题")
                    if pdf_link:
                        chunks = pdf_parser.process_paper_pdf(title, pdf_link)
                        all_chunks.extend(chunks)
                
                if all_chunks:
                    from core.rag import TFIDFRetriever
                    self.retriever = TFIDFRetriever()
                    self.retriever.build_index(all_chunks)
                    yield AgentEvent(EventType.STEP_START, step_name="解析正文",
                                     content=f"📝 正文解析完成，共构建 {len(all_chunks)} 个文本分块的本地 RAG 索引。")
                else:
                    self.retriever = None
                    yield AgentEvent(EventType.STEP_START, step_name="解析正文",
                                     content="⚠️ 未能获取到论文正文，将降级为仅依据摘要生成报告。")
            else:
                self.retriever = None

            yield AgentEvent(EventType.STEP_START, step_name="生成报告",
                             content="📊 正在生成最终检索报告...")

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
        self, natural_query: str, arxiv_query: str,
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
            providers = self.search_service.provider_labels()

            yield AgentEvent(EventType.SEARCH_START, round_num=round_num,
                             step_name="执行检索",
                             content=(
                                 f"🔍 第 {round_num} 轮检索中{retry_label}...\n"
                                 f"检索源: {providers}\n"
                                 f"检索式: `{current_query}`"
                             ))

            search_result: SearchResult = self.search_service.search(
                arxiv_query=current_query,
                natural_query=natural_query,
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

            if error.error_type == SearchErrorType.RATE_LIMIT:
                yield AgentEvent(
                    EventType.ERROR,
                    content="⏳ 已启用的检索源当前触发限流，请稍后再试。应用已停止改写检索式，避免继续消耗 LLM 调用。",
                )
                return [], False

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
                    natural_query, current_query, error_msg, round_num
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
        for token in self._stream_chat(messages):
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
        for token in self._stream_chat(messages):
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
        for token in self._stream_chat(messages):
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
        
        # 检索正文切片 (RAG)
        rag_context = ""
        if hasattr(self, "retriever") and self.retriever and self.retriever.chunks:
            retrieved = self.retriever.retrieve(user_query, top_k=6)
            if retrieved:
                rag_context = "\n\n".join(
                    f"【文献: {r['paper_title']} | 分块 {r['chunk_index']}】\n{r['text']}"
                    for r in retrieved
                )
        if not rag_context:
            rag_context = "（未提供正文切片，请仅使用已有摘要信息生成报告）"

        prompt_content = llm.load_prompt(
            "summary.txt",
            user_query=user_query,
            full_search_memory=self.memory.get_search_memory_text(),
            final_papers=papers_text,
            rag_context=rag_context,
        )
        messages = llm.build_messages(self.system_prompt, prompt_content)

        full_text = ""
        for token in self._stream_chat(messages):
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
        for token in self._stream_chat(messages):
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
        for token in self._stream_chat(messages):
            full_text += token
            yield AgentEvent(EventType.THINKING, content=token)

        try:
            result = llm.parse_json_response(full_text)
            return result, full_text
        except ValueError as e:
            yield AgentEvent(EventType.ERROR, content=f"⚠️ 解析意图识别结果失败: {e}")
            return None, full_text

    def _stream_followup_reply(self, user_message: str):
        """流式生成多轮对话回复（不涉及新检索，支持正文 RAG 回答）"""
        conv_text = self._format_conversation_history()
        papers_text = arxiv_search.format_papers_for_llm(self.memory.get_all_relevant_papers())

        # 检索正文切片 (RAG)
        rag_context = ""
        if hasattr(self, "retriever") and self.retriever and self.retriever.chunks:
            retrieved = self.retriever.retrieve(user_message, top_k=6)
            if retrieved:
                rag_context = "\n\n".join(
                    f"【文献: {r['paper_title']} | 分块 {r['chunk_index']}】\n{r['text']}"
                    for r in retrieved
                )

        rag_section = ""
        if rag_context:
            rag_section = (
                f"与用户追问相关的论文正文切片 (如果可用，请优先在此部分寻找具体解答，并注明是根据哪篇论文的哪个章节/片段)：\n"
                f"{rag_context}\n\n"
            )

        user_content = (
            f"用户的消息: {user_message}\n\n"
            f"对话历史:\n{conv_text}\n\n"
            f"之前推荐的论文摘要:\n{papers_text}\n\n"
            f"{rag_section}"
            f"请直接用中文回复用户的问题。不需要检索，只需要基于已有信息回答。"
        )
        messages = llm.build_messages(self.system_prompt, user_content)

        full_text = ""
        for token in self._stream_chat(messages):
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
