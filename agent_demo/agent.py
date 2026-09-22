"""ReAct Agent 循环：思考 → 工具调用 → 观察 → 重复。"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from context_pruner import (
    ArchiveStore,
    ContextBudget,
    ContextEventKind,
    ContextPruner,
    ContextPrunerV1,
    LifecycleEvent,
)

from .env import KnowledgeBase, Task
from .history import HistoryManager
from .llm import LLMClient, MockLLM
from .utils import count_tokens

DEFAULT_SYSTEM_PROMPT = (
    "你是一个用于实验评测的长程任务 Agent。你必须通过调用工具收集信息，最终给出答案。"
    "一旦当前证据足以回答任务，立即输出 final_answer，不要为了读完所有文档而继续搜索或读取无关资料。"
    "每轮只允许输出一个 JSON 对象，不要在同一轮连续输出多个工具调用。请严格输出 JSON："
    '{"thought": "...", "action": {"name": "工具名", "args": {...}}}'
    ' 或 {"thought": "...", "final_answer": "..."}。'
    "输出 JSON 后必须立即停止；不得自行续写 <system>、工具返回、观察或后续轮次。"
)

FINALIZATION_PROMPT = (
    "这是本任务的最后一次模型调用。禁止继续调用任何工具；"
    "必须立即根据当前已有证据输出且只输出一个包含 final_answer 的合法 JSON 对象。"
)


@dataclass
class TurnRecord:
    step: int
    tokens_in: int
    full_tokens_in: int
    tokens_out: int
    reply: str
    action: dict | None
    observation: str | None
    latency: float
    kirr: float | None = None
    archive_count: int = 0
    context_reply: str = ""
    reply_sanitized: bool = False
    dropped_output_tokens: int = 0
    tool_ledger_tokens: int = 0


@dataclass
class RunRecord:
    task: Task
    method: str
    success: bool
    final_answer: str | None
    partial_score: float = 0.0
    final_kirr: float = 0.0
    recovery_count: int = 0
    recovery_events: list[dict] = field(default_factory=list)
    compression_overhead_tokens: int = 0
    compression_overhead_input_tokens: int = 0
    compression_overhead_output_tokens: int = 0
    archive_count: int = 0
    recovered_tokens: int = 0
    recovery_precision: float = 0.0
    recovery_recall: float = 0.0
    recovery_amplification: float = 0.0
    constraint_adherence: float = 1.0
    tool_correctness: float = 1.0
    parse_error_count: int = 0
    tool_error_count: int = 0
    repeated_action_count: int = 0
    post_sufficiency_tool_call_count: int = 0
    max_turn_failure: bool = False
    budget_pressure_count: int = 0
    budget_violation_count: int = 0
    checkpoint_count: int = 0
    sanitized_output_count: int = 0
    sanitized_output_tokens: int = 0
    method_config: dict = field(default_factory=dict)
    pricing: dict = field(default_factory=dict)
    lifecycle_records: list = field(default_factory=list)
    runtime_events: list = field(default_factory=list)
    turns: list[TurnRecord] = field(default_factory=list)

    @property
    def tokens_in_total(self) -> int:
        return sum(t.tokens_in for t in self.turns)

    @property
    def tokens_out_total(self) -> int:
        return sum(t.tokens_out for t in self.turns)

    @property
    def avg_tokens_in_per_turn(self) -> float:
        return self.tokens_in_total / max(1, len(self.turns))

    @property
    def tool_ledger_tokens_total(self) -> int:
        return sum(turn.tool_ledger_tokens for turn in self.turns)

    @property
    def full_context_tokens_total(self) -> int:
        return sum(turn.full_tokens_in for turn in self.turns)

    @property
    def gross_input_tokens_saved(self) -> int:
        return max(0, self.full_context_tokens_total - self.tokens_in_total)

    @property
    def net_input_tokens_saved(self) -> int:
        return self.gross_input_tokens_saved - self.compression_overhead_tokens

    @property
    def net_input_savings_rate(self) -> float:
        return self.net_input_tokens_saved / max(1, self.full_context_tokens_total)

    @property
    def peak_context_tokens(self) -> int:
        return max((turn.tokens_in for turn in self.turns), default=0)

    @property
    def total_latency(self) -> float:
        return sum(turn.latency for turn in self.turns)

    @property
    def p95_turn_latency(self) -> float:
        return _percentile([turn.latency for turn in self.turns], 0.95)

    @property
    def estimated_agent_cost(self) -> float:
        return (
            self.tokens_in_total * float(self.pricing.get("input_per_million", 0.0))
            + self.tokens_out_total * float(self.pricing.get("output_per_million", 0.0))
        ) / 1_000_000

    @property
    def estimated_compression_cost(self) -> float:
        return (
            self.compression_overhead_tokens
            * float(self.pricing.get("compressor_per_million", 0.0))
            / 1_000_000
        )

    @property
    def estimated_total_cost(self) -> float:
        return self.estimated_agent_cost + self.estimated_compression_cost


class ReActAgent:
    def __init__(
        self,
        client: LLMClient,
        env: KnowledgeBase,
        max_turns: int = 10,
        method: str = "none",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        window_steps: int = 4,
        pricing: dict | None = None,
        temperature: float = 0.0,
        context_budget: ContextBudget | None = None,
        archive_store: ArchiveStore | None = None,
    ):
        self.client = client
        self.env = env
        self.max_turns = max_turns
        self.method = method
        preset = resolve_method(method)
        self.effective_method = preset["effective_method"]
        self.lifecycle_enabled = preset["lifecycle_enabled"]
        self.adaptive_enabled = preset["adaptive_enabled"]
        self.recovery_enabled = preset["recovery_enabled"]
        self.method_config = preset
        self.pricing = dict(pricing or {})
        self.temperature = temperature
        self.context_budget = context_budget
        self.archive_store = archive_store
        self.window_steps = window_steps
        self.system_prompt = system_prompt + f"\n可用工具：{env.tool_schema()}"
        if self.effective_method == "pruner_v1":
            self.pruner = ContextPrunerV1()
        elif self.effective_method in {"pruner", "pruner_v0"}:
            self.pruner = ContextPruner()
        else:
            self.pruner = None
        self.summarizer = None
        if self.effective_method == "summary" and not _is_mock_client(client):
            self.summarizer = self._summarize

    def run(self, task: Task) -> RunRecord:
        if hasattr(self.client, "reset"):
            self.client.reset()

        history = HistoryManager(
            self.system_prompt,
            task.question,
            method=self.effective_method,
            window_steps=self.window_steps,
            pruner=self.pruner,
            summarizer=self.summarizer,
            lifecycle_enabled=self.lifecycle_enabled,
            context_budget=self.context_budget,
            archive_store=self.archive_store,
        )
        history.record_boundary(
            ContextEventKind.TASK_STARTED,
            {"task_id": task.task_id, "method": self.method},
        )
        turns: list[TurnRecord] = []
        final_answer: str | None = None
        recovery_events: list[dict] = []
        repeated_action_events = 0
        post_sufficiency_tool_calls = 0
        parse_error_count = 0
        tool_error_count = 0
        sanitized_output_count = 0
        sanitized_output_tokens = 0
        actions_executed: list[dict] = []
        last_search_candidates: list[str] = []

        for step in range(self.max_turns):
            full_tokens_in = history.full_tokens_in()
            history.apply_compression()
            messages = history.build_messages()
            tokens_in = history.tokens_in()
            tool_ledger = _tool_ledger_message(
                actions_executed,
                last_search_candidates,
                all_doc_ids=list(self.env.docs),
            )
            tool_ledger_tokens = 0
            if tool_ledger:
                messages.append({"role": "system", "content": tool_ledger})
                tool_ledger_tokens = count_tokens(tool_ledger)
                # 工具账本是两组 Agent 共用的运行时状态，实际输入与完整历史口径都计费。
                tokens_in += tool_ledger_tokens
                full_tokens_in += tool_ledger_tokens
            finalization_forced = step == self.max_turns - 1
            if finalization_forced:
                messages.append({"role": "system", "content": FINALIZATION_PROMPT})
                finalization_tokens = count_tokens(FINALIZATION_PROMPT)
                tokens_in += finalization_tokens
                full_tokens_in += finalization_tokens
            kirr = _kirr(messages, task.golden_facts)
            history.record_boundary(
                ContextEventKind.MODEL_INPUT,
                {
                    "step": step,
                    "tokens": tokens_in,
                    "archive_count": history.archive_count,
                    "finalization_forced": finalization_forced,
                    "tool_ledger_tokens": tool_ledger_tokens,
                },
            )

            start = time.perf_counter()
            reply = self.client.complete(messages, temperature=self.temperature)
            latency = time.perf_counter() - start
            tokens_out = count_tokens(reply)

            parsed = _parse_output(reply)
            context_reply, reply_sanitized, dropped_output_tokens = _sanitize_model_reply(
                reply, parsed
            )
            if reply_sanitized:
                sanitized_output_count += 1
                sanitized_output_tokens += dropped_output_tokens
            action: dict | None = None
            observation: str | None = None
            guard_blocked = False

            if parsed is not None and "final_answer" in parsed:
                final_answer = str(parsed["final_answer"]).strip()
                history.add(
                    "assistant",
                    context_reply,
                    kind=ContextEventKind.MODEL_OUTPUT,
                    metadata={
                        "step": step,
                        "final": True,
                        "reply_sanitized": reply_sanitized,
                        "dropped_output_tokens": dropped_output_tokens,
                    },
                )
                turns.append(
                    TurnRecord(
                        step, tokens_in, full_tokens_in, tokens_out, reply,
                        None, None, latency, kirr, history.archive_count,
                        context_reply, reply_sanitized, dropped_output_tokens,
                        tool_ledger_tokens,
                    )
                )
                break

            history.add(
                "assistant",
                context_reply,
                kind=ContextEventKind.MODEL_OUTPUT,
                metadata={
                    "step": step,
                    "final": False,
                    "reply_sanitized": reply_sanitized,
                    "dropped_output_tokens": dropped_output_tokens,
                },
            )

            if parsed is not None and isinstance(parsed.get("action"), dict):
                action = parsed["action"]
                # 仅作为离线评测指标；kirr/golden_facts 从不写入模型消息。
                if kirr is not None and kirr >= 1.0:
                    post_sufficiency_tool_calls += 1
                guard_reason = _redundant_action_reason(
                    action,
                    actions_executed,
                    candidate_ids=last_search_candidates,
                    all_doc_ids=list(self.env.docs),
                )
                guard_blocked = guard_reason is not None
                history.record_boundary(
                    ContextEventKind.TOOL_CALL,
                    {
                        "step": step,
                        "action": action,
                        "blocked_as_repeat": guard_blocked,
                        "guard_reason": guard_reason,
                    },
                )
                if guard_blocked:
                    repeated_action_events += 1
                    observation = _repeat_guard_observation(
                        action,
                        actions_executed,
                        self.env,
                        reason=guard_reason,
                        candidate_ids=last_search_candidates,
                    )
                else:
                    actions_executed.append(action)
                    observation, search_candidates = self._execute(action)
                    if search_candidates is not None:
                        last_search_candidates = search_candidates
                    if str(observation).startswith("错误"):
                        tool_error_count += 1
            else:
                parse_error_count += 1
                observation = "格式错误：输出必须是合法 JSON（action 或 final_answer）。"
                history.record_boundary(
                    ContextEventKind.PARSE_ERROR,
                    {"step": step, "reply": reply},
                )
                fault_config = _active_fault_config(self.client)
                if fault_config and fault_config.get("drop_terms"):
                    history.inject_context_loss(list(fault_config["drop_terms"]))
                recovery_query = f"{task.question}\n{reply}"
                should_recover = bool(
                    fault_config and fault_config.get("drop_terms")
                ) or _signals_missing_evidence(reply)
                if self.recovery_enabled and should_recover and history.recover(
                    "parse_failed",
                    query=recovery_query,
                ):
                    recovery_events.append(_recovery_event(history, step, "parse_failed"))

            observation_kind = (
                ContextEventKind.TOOL_ERROR
                if guard_blocked or str(observation).startswith("错误")
                else ContextEventKind.TOOL_RESULT
            )
            history.add(
                "user",
                f"观察：{observation}",
                kind=observation_kind,
                metadata={"step": step, "action": action},
            )
            turns.append(
                TurnRecord(
                    step, tokens_in, full_tokens_in, tokens_out, reply,
                    action, observation, latency, kirr, history.archive_count,
                    context_reply, reply_sanitized, dropped_output_tokens,
                    tool_ledger_tokens,
                )
            )

        success = _evaluate(task, final_answer)
        partial_score = _partial_score(task, final_answer)
        kirr_values = [t.kirr for t in turns if t.kirr is not None]
        final_kirr = kirr_values[-1] if kirr_values else 0.0
        recovery_precision, recovery_recall, recovery_amplification = _recovery_quality(
            recovery_events,
            task.recovery_targets,
        )
        history.record_boundary(
            ContextEventKind.TASK_FINISHED,
            {
                "success": success,
                "turns": len(turns),
                "recovery_count": history.recovery_count,
            },
        )
        return RunRecord(
            task=task,
            method=self.method,
            success=success,
            final_answer=final_answer,
            partial_score=partial_score,
            final_kirr=final_kirr,
            recovery_count=history.recovery_count,
            recovery_events=recovery_events,
            compression_overhead_tokens=history.compression_overhead_tokens,
            compression_overhead_input_tokens=history.compression_overhead_input_tokens,
            compression_overhead_output_tokens=history.compression_overhead_output_tokens,
            archive_count=history.archive_count,
            recovered_tokens=history.recovered_tokens,
            recovery_precision=recovery_precision,
            recovery_recall=recovery_recall,
            recovery_amplification=recovery_amplification,
            constraint_adherence=_constraint_adherence(task, final_answer),
            tool_correctness=_tool_correctness(task, actions_executed),
            parse_error_count=parse_error_count,
            tool_error_count=tool_error_count,
            repeated_action_count=repeated_action_events,
            post_sufficiency_tool_call_count=post_sufficiency_tool_calls,
            max_turn_failure=final_answer is None and len(turns) >= self.max_turns,
            budget_pressure_count=sum(
                record.event == LifecycleEvent.BUDGET_PRESSURE
                for record in history.lifecycle_records
            ),
            budget_violation_count=sum(
                record.event == LifecycleEvent.BUDGET_VIOLATION
                for record in history.lifecycle_records
            ),
            checkpoint_count=history.checkpoint_count,
            sanitized_output_count=sanitized_output_count,
            sanitized_output_tokens=sanitized_output_tokens,
            method_config=dict(self.method_config),
            pricing=dict(self.pricing),
            lifecycle_records=history.lifecycle_records,
            runtime_events=history.runtime_events,
            turns=turns,
        )

    def _execute(self, action: Any) -> tuple[str, list[str] | None]:
        name = str(action.get("name", "")).strip()
        args = action.get("args") or {}
        if name == "search":
            candidates = self.env.search(str(args.get("keyword", "")))
            candidate_ids = [str(item) for item in candidates]
            coverage = (
                "已覆盖当前知识库"
                if _candidate_set_is_exhaustive(candidate_ids, list(self.env.docs))
                else "部分候选，可继续换关键词检索"
            )
            return f"search 结果（{coverage}）：{candidates}", candidate_ids
        if name == "read":
            return f"read 结果：{self.env.read(str(args.get('doc_id', '')))}", None
        return f"错误：未知工具 {name}", None

    def _summarize(self, text: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是上下文压缩器。请把下面的历史记录压缩成不超过 200 字的摘要，"
                    "保留关键事实、数字、结论、约束和错误信息。只输出摘要正文。"
                ),
            },
            {"role": "user", "content": text},
        ]
        return self.client.complete(messages, temperature=0.0).strip()


def _parse_output(reply: str) -> dict | None:
    """读取回复中的第一个完整 JSON 对象，每轮只交给 Agent 一个动作。"""
    reply = reply.strip()
    if reply.startswith("```"):
        parts = reply.split("```")
        reply = parts[1].removeprefix("json").strip() if len(parts) > 1 else reply
    decoder = json.JSONDecoder()
    for index, char in enumerate(reply):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(reply[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _sanitize_model_reply(reply: str, parsed: dict | None) -> tuple[str, bool, int]:
    """工作上下文只接收首个合法 JSON；原始回复仍由 TurnRecord 留作审计。"""
    if parsed is None:
        normalized = reply.strip()
    else:
        normalized = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    sanitized = parsed is not None and _has_extra_model_content(reply)
    dropped_tokens = max(0, count_tokens(reply) - count_tokens(normalized))
    return normalized, sanitized, dropped_tokens


def _has_extra_model_content(reply: str) -> bool:
    """区分正常 JSON 格式差异与真正的前后缀续写。"""
    candidate = reply.strip()
    outside_fence = ""
    if candidate.startswith("```"):
        parts = candidate.split("```")
        if len(parts) > 1:
            candidate = parts[1].removeprefix("json").strip()
            outside_fence = "".join(parts[2:]).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(candidate):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(candidate[index:])
        except json.JSONDecodeError:
            continue
        return bool(candidate[:index].strip() or candidate[index + end :].strip() or outside_fence)
    return False


def _redundant_action_reason(
    action: dict,
    executed_actions: list[dict],
    *,
    candidate_ids: list[str] | None = None,
    all_doc_ids: list[str] | None = None,
) -> str | None:
    """识别不会产生新信息的确定性工具调用。"""
    name = str(action.get("name", "")).strip()
    args = action.get("args") or {}
    if name == "read":
        doc_id = str(args.get("doc_id", ""))
        if any(
            str(item.get("name", "")).strip() == "read"
            and str((item.get("args") or {}).get("doc_id", "")) == doc_id
            for item in executed_actions
        ):
            return "already_read"
    if name == "search":
        signature = _action_signature(action)
        if any(_action_signature(item) == signature for item in executed_actions):
            return "duplicate_search"
        if executed_actions and str(executed_actions[-1].get("name", "")).strip() == "search":
            return "search_without_read"
        searched_before = any(
            str(item.get("name", "")).strip() == "search"
            for item in executed_actions
        )
        if searched_before and _candidate_set_is_exhaustive(candidate_ids, all_doc_ids):
            return "search_exhaustive_candidates"
    return None


def _candidate_set_is_exhaustive(
    candidate_ids: list[str] | None,
    all_doc_ids: list[str] | None,
) -> bool:
    """仅在工具返回值已覆盖当前知识库时声明候选穷尽。"""
    universe = {str(item) for item in (all_doc_ids or []) if str(item)}
    candidates = {str(item) for item in (candidate_ids or []) if str(item)}
    return bool(universe) and universe.issubset(candidates)


def _signals_missing_evidence(reply: str) -> bool:
    """只在模型明确报告证据缺失时尝试旁路恢复。"""
    text = str(reply).lower()
    markers = (
        "缺少证据",
        "缺少信息",
        "信息不足",
        "无法确认",
        "无法确定",
        "未找到",
        "missing evidence",
        "insufficient information",
    )
    return any(marker in text for marker in markers)


def _tool_ledger_message(
    executed_actions: list[dict],
    last_search_candidates: list[str],
    *,
    all_doc_ids: list[str] | None = None,
    max_ids: int = 20,
) -> str:
    """生成不参与历史压缩的最小工具进度账本。"""
    if not executed_actions:
        return ""
    read_ids = list(
        dict.fromkeys(
            str(item.get("args", {}).get("doc_id", ""))
            for item in executed_actions
            if str(item.get("name", "")).strip() == "read"
            and str(item.get("args", {}).get("doc_id", ""))
        )
    )
    read_state = _bounded_id_list(read_ids, max_ids) if read_ids else ""
    candidates = list(dict.fromkeys(str(item) for item in last_search_candidates))
    candidate_state = _bounded_id_list(candidates, max_ids) if candidates else ""
    unread_ids = [item for item in candidates if item not in set(read_ids)]
    unread_state = _bounded_id_list(unread_ids, max_ids) if unread_ids else ""
    search_state = (
        "options 已覆盖当前知识库，再次 search 不会发现新文档；"
        if _candidate_set_is_exhaustive(candidates, all_doc_ids)
        else "options 是部分候选，若任务字段仍缺失可继续 search；"
        if candidates
        else ""
    )
    return (
        f"工具状态：read=[{read_state}]; unread=[{unread_state}]; "
        f"options=[{candidate_state}]（选项非待办）。"
        f"{search_state}"
        "禁止重读；options 已覆盖知识库时禁止继续 search，必须从 unread 选择 read；"
        "若原任务各项已有具体证据，立即 final_answer。"
    )


def _requested_answer_terms(question: str) -> list[str]:
    """Extract explicit deliverable labels from the visible user question.

    This deliberately uses only the visible question, never golden facts or answer
    constraints.  Besides acronyms such as RPO/RTO, it recognizes Chinese list
    facets (for example ``保存年限、存储模式和最终审批角色``).  Generic measurement
    suffixes are removed so a semantically complete answer may say ``保留策略``
    instead of repeating ``保留份数`` verbatim.
    """
    text = str(question).strip()
    terms = [
        match.group(0)
        for match in re.finditer(
            r"(?<![A-Za-z0-9])(?:[A-Z][A-Z0-9_-]{1,})(?![A-Za-z0-9])",
            text,
        )
    ]
    # 显式“字段”问题已用稳定标签定义交付合同；继续抽取后续中文说明会把
    # “来源文件”“忽略草案”等执行约束误判为答案字段。
    if terms and "字段" in text:
        return list(dict.fromkeys(terms))
    # The deliverable list normally follows the last possessive ``的``.  For
    # imperative questions without one, remove a leading task verb instead.
    tail = text.rsplit("的", 1)[-1]
    tail = re.sub(
        r"^(?:请|综合资料)?(?:总结|说明|找出|给出|列出|确认|查询|回答|读取资料并回答)",
        "",
        tail,
    )
    for facet in re.split(r"[、，,；;]|以及|并且|和|与", tail):
        facet = facet.strip(" 。：:？?")
        if not facet or re.search(r"[A-Z][A-Z0-9_-]{1,}", facet):
            continue
        alternatives = facet.split("/")
        for item in alternatives:
            item = re.sub(r"^(?:请|分别|综合|相关|两项|三项|各项)", "", item)
            item = re.sub(r"(?:分别|各自|是什么|为何|是谁)$", "", item)
            item = re.sub(
                r"(?:频率|份数|年限|模式|角色|级别|渠道|时限|目标)$",
                "",
                item,
            )
            item = item.strip(" 。：:？?")
            if len(item) >= 2 and re.fullmatch(r"[\u4e00-\u9fff]+", item):
                terms.append(item)
    return list(dict.fromkeys(terms))


def _missing_requested_answer_terms(question: str, answer: str) -> list[str]:
    """Return visible-question terms omitted from a proposed final answer."""
    normalized_answer = str(answer).upper()
    return [
        term
        for term in _requested_answer_terms(question)
        if term.upper() not in normalized_answer
    ]


def _bounded_id_list(values: list[str], limit: int) -> str:
    shown = values[: max(1, limit)]
    suffix = f"（另有 {len(values) - len(shown)} 个）" if len(values) > len(shown) else ""
    return ", ".join(shown) + suffix


def _repeat_guard_observation(
    action: dict,
    executed_actions: list[dict],
    env: KnowledgeBase,
    reason: str | None = None,
    candidate_ids: list[str] | None = None,
) -> str:
    """阻断确定性工具的重复调用，并给模型一个可执行的下一步提示。"""
    name = str(action.get("name", "")).strip()
    read_ids = {
        str(item.get("args", {}).get("doc_id", ""))
        for item in executed_actions
        if str(item.get("name", "")).strip() == "read"
    }
    available_ids = candidate_ids if candidate_ids else list(env.docs)
    unread = [doc_id for doc_id in available_ids if doc_id not in read_ids]
    if name == "read":
        doc_id = str(action.get("args", {}).get("doc_id", ""))
        if not unread and available_ids and not _candidate_set_is_exhaustive(
            list(available_ids), list(env.docs)
        ):
            return (
                f"保护：重复 read({doc_id}) 已跳过；当前部分候选均已读取。"
                "若原任务仍有缺失字段，请用该字段名调用一次 search；"
                "若各项证据已齐全则直接给出 final_answer。"
            )
        return (
            f"保护：重复 read({doc_id}) 已跳过；该文档已读取。"
            f"请改读未读取文档：{unread}，或在证据充分时直接给出 final_answer。"
        )
    if name == "search" and reason == "search_without_read":
        return (
            "保护：上一次 search 的候选文档尚未读取，本次搜索已跳过。"
            f"下轮必须从未读取文档 {unread or list(env.docs)} 中选择一个调用 read；"
            "如果证据已经充分则直接给出 final_answer，不要继续变换关键词搜索。"
        )
    if name == "search" and reason == "search_exhaustive_candidates":
        return (
            "保护：上一次 search 已覆盖当前知识库，本次搜索不会产生新候选，已跳过。"
            f"请从未读取文档 {unread or list(env.docs)} 中选择一个调用 read；"
            "如果证据已经充分则直接给出 final_answer。"
        )
    if name == "search":
        return (
            "保护：完全相同的 search 已执行并跳过重复调用。"
            f"请从候选文档 {unread or list(env.docs)} 中选择 doc_id 调用 read，"
            "不要继续重复搜索。"
        )
    return "保护：完全相同的工具调用已执行并被跳过，请改变参数或给出 final_answer。"


def _evaluate(task: Task, final_answer: str | None) -> bool:
    """自动判分：最终答案包含全部 golden_facts 即视为成功。"""
    if not final_answer:
        return False
    return all(_fact_present(final_answer, str(fact)) for fact in task.golden_facts)


def _partial_score(task: Task, final_answer: str | None) -> float:
    """部分得分：命中 golden_facts 的比例。"""
    if not final_answer or not task.golden_facts:
        return 0.0
    matched = sum(1 for fact in task.golden_facts if _fact_present(final_answer, str(fact)))
    return matched / len(task.golden_facts)


def _fact_present(text: str, fact: str) -> bool:
    """有限规范化匹配，容忍标点、空白和不改变事实的中文模态词。"""
    if fact.lower() in text.lower():
        return True
    if _normalize_fact(fact) == "pass":
        normalized_text = unicodedata.normalize("NFKC", str(text)).lower()
        chinese_pass = "通过" in normalized_text and not any(
            marker in normalized_text
            for marker in ("未通过", "不通过", "没有通过", "未全部通过")
        )
        english_pass = bool(re.search(r"\b(?:pass|passed|ok)\b", normalized_text)) and not bool(
            re.search(r"\b(?:fail|failed|not\s+ok)\b", normalized_text)
        )
        return chinese_pass or english_pass
    return _normalize_fact(fact) in _normalize_fact(text)


def _normalize_fact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).lower()
    # 这些词只表达义务强度；移除后仍保留实体、数值、单位和关系词。
    normalized = re.sub(r"必须|应当|应该", "", normalized)
    return "".join(
        char
        for char in normalized
        if char.isalnum() or char in {"%", "-", ".", ":", "/", "+"}
    )


def _kirr(messages: list[dict], golden_facts: list[str]) -> float:
    """关键信息保留率：压缩后 prompt 中仍能找到的 golden_facts 比例。"""
    if not golden_facts:
        return 1.0
    text = "\n".join(str(m.get("content", "")) for m in messages).lower()
    matched = sum(1 for f in golden_facts if str(f).lower() in text)
    return matched / len(golden_facts)


def _action_signature(action: dict) -> str:
    return f"{action.get('name')}:{action.get('args')}"


def _recovery_event(
    history: HistoryManager,
    step: int,
    reason: str,
) -> dict:
    if history.lifecycle.recovery_events:
        return {**history.lifecycle.recovery_events[-1], "step": step}
    return {"step": step, "reason": reason}


def resolve_method(method: str) -> dict:
    presets = {
        "ablation_a": {
            "effective_method": "none",
            "lifecycle_enabled": False,
            "adaptive_enabled": False,
            "recovery_enabled": False,
        },
        "ablation_b": {
            "effective_method": "pruner_v0",
            "lifecycle_enabled": True,
            "adaptive_enabled": False,
            "recovery_enabled": False,
        },
        "ablation_c": {
            "effective_method": "pruner_v1",
            "lifecycle_enabled": True,
            "adaptive_enabled": True,
            "recovery_enabled": False,
        },
        "ablation_d": {
            "effective_method": "pruner_v1",
            "lifecycle_enabled": True,
            "adaptive_enabled": True,
            "recovery_enabled": True,
        },
    }
    if method in presets:
        return {"label": method, **presets[method]}
    is_pruner = method in {"pruner", "pruner_v0", "pruner_v1"}
    return {
        "label": method,
        "effective_method": method,
        "lifecycle_enabled": is_pruner,
        "adaptive_enabled": method == "pruner_v1",
        "recovery_enabled": True,
    }


def _constraint_adherence(task: Task, answer: str | None) -> float:
    required = [str(item) for item in task.answer_constraints.get("required", [])]
    forbidden = [str(item) for item in task.answer_constraints.get("forbidden", [])]
    checks = len(required) + len(forbidden)
    if checks == 0:
        return 1.0
    text = answer or ""
    passed = sum(_fact_present(text, item) for item in required) + sum(
        not _fact_present(text, item) for item in forbidden
    )
    return passed / checks


def _tool_correctness(
    task: Task,
    actions: list[dict],
    *,
    valid_tools: set[str] | None = None,
) -> float:
    expected = [str(name) for name in task.expected_tools]
    actual = [str(action.get("name", "")) for action in actions]
    valid = set(valid_tools or {"search", "read"})
    invalid_count = sum(name not in valid for name in actual)
    if not expected:
        return 1.0 if invalid_count == 0 else 0.0
    covered = sum(name in actual for name in set(expected))
    return max(0.0, (covered - invalid_count) / len(set(expected)))


def _recovery_quality(
    recovery_events: list[dict],
    targets: list[str],
) -> tuple[float, float, float]:
    if not recovery_events:
        return (0.0, 0.0, 0.0)
    texts = [
        str(text)
        for event in recovery_events
        for text in event.get("recovered_texts", [])
    ]
    if not texts or not targets:
        return (0.0, 0.0, 0.0)
    lowered_targets = [str(target).lower() for target in targets]
    relevant_items = sum(
        any(target in text.lower() for target in lowered_targets)
        for text in texts
    )
    combined = "\n".join(texts).lower()
    matched_targets = [target for target in lowered_targets if target in combined]
    precision = relevant_items / len(texts)
    recall = len(matched_targets) / len(lowered_targets)
    if not matched_targets:
        return (precision, recall, 0.0)
    recovered_tokens = sum(count_tokens(text) for text in texts)
    target_tokens = sum(count_tokens(target) for target in matched_targets)
    amplification = recovered_tokens / max(1, target_tokens)
    return (precision, recall, amplification)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _is_mock_client(client: LLMClient) -> bool:
    current = client
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, MockLLM):
            return True
        seen.add(id(current))
        current = getattr(current, "inner", None)
    return False


def _active_fault_config(client: LLMClient) -> dict | None:
    """只在本轮确实由 FaultInjectingLLM 注入故障时返回其配置。"""
    current = client
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if getattr(current, "last_fault_type", None):
            return dict(getattr(current, "config", {}) or {})
        seen.add(id(current))
        current = getattr(current, "inner", None)
    return None
