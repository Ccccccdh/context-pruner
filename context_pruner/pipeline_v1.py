"""Context-Pruner v1 入口：语义分层解析 + 自适应混合评分 + 阶段调度 + 压缩。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from .compressors import archive_reference, merge, summarize, truncate
from .evaluator import evaluate
from .evaluator_v1 import AdaptiveScorer
from .evaluator import _cosine
from .parser_v1 import ContextClassifier, parse_context_semantic
from .scheduler import detect_phase
from .scheduler_v1 import schedule_adaptive
from .semantic import ExtractiveSummarizer, LocalHashEmbedding
from .types import (
    BudgetPressure,
    CompressedResult,
    CompressionAction,
    ContextBudget,
    ContextItem,
    ContextStatus,
    ContextType,
    TaskPhase,
)


class ContextPrunerV1:
    """v1 压缩器：使用语义分类和自适应重要性评分。"""

    def __init__(
        self,
        scorer=None,
        classifier: ContextClassifier | None = None,
        embed_fn=None,
        self_info_fn=None,
        ppl_fn=None,
        summarizer=None,
        local_semantics: bool = True,
    ):
        if embed_fn is None and local_semantics:
            embed_fn = LocalHashEmbedding()
        self.scorer = scorer or AdaptiveScorer(
            embed_fn=embed_fn,
            self_info_fn=self_info_fn,
            ppl_fn=ppl_fn,
        )
        self.classifier = classifier
        self.embed_fn = embed_fn
        self.summarizer = ExtractiveSummarizer() if summarizer is None else summarizer
        self.sim_fn = (
            lambda first, second: _cosine(embed_fn(first), embed_fn(second))
            if embed_fn is not None
            else None
        )

    def compress(
        self,
        turns: list[dict],
        task_state: str = "",
        budget: ContextBudget | None = None,
    ) -> CompressedResult:
        chunks = parse_context_semantic(
            turns,
            classifier=self.classifier,
            embed_fn=self.embed_fn,
        )
        phase = detect_phase(turns)
        chunks = evaluate(chunks, task_state, self.scorer)
        tokens_before = sum(chunk.token_count for chunk in chunks)
        pressure = budget.pressure(tokens_before) if budget else BudgetPressure.NORMAL
        chunks = schedule_adaptive(chunks, phase, pressure=pressure)
        result = _apply(chunks, phase, self.summarizer, self.sim_fn)
        result.budget_pressure = pressure
        result.budget_target_tokens = budget.target_tokens if budget else None
        if budget is not None and result.tokens_after > int(budget.target_tokens or 0):
            result = _enforce_budget(result, budget)
        return result


def _apply(chunks, phase: TaskPhase, summarizer, sim_fn=None) -> CompressedResult:
    tokens_before = sum(c.token_count for c in chunks)
    out = []
    # read 证据每轮都从不可变事件重建为一个累计任务记忆。不能只收集本轮被
    # ARCHIVE 的证据，否则先前已压缩的事实会在后续轮次逐渐从工作上下文消失。
    memory_sources = [c for c in chunks if _is_evidence(c)]
    for c in chunks:
        if _is_evidence(c):
            continue
        if c.action == CompressionAction.TRUNCATE:
            out.append(truncate(c))
        elif c.action == CompressionAction.SUMMARIZE:
            out.append(summarize(c, summarizer))
        elif c.action == CompressionAction.ARCHIVE:
            out.append(archive_reference(c))
        else:
            out.append(c)
    if memory_sources:
        out.append(_build_task_memory(memory_sources, summarizer))
    out = merge(out, sim_fn=sim_fn)
    tokens_after = sum(c.token_count for c in out)
    return CompressedResult(
        phase=phase,
        chunks=out,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )


def _enforce_budget(result: CompressedResult, budget: ContextBudget) -> CompressedResult:
    """逐步归档低价值块；硬约束块始终保留并显式报告无法满足的预算。"""
    chunks = list(result.chunks)
    archived = list(result.archived_chunks)
    target = int(budget.target_tokens or 0)

    candidates = sorted(
        range(len(chunks)),
        key=lambda index: (
            _protected(chunks[index]),
            chunks[index].importance or 0.0,
            chunks[index].source_turn,
        ),
    )
    for index in candidates:
        if sum(chunk.token_count for chunk in chunks) <= target:
            break
        chunk = chunks[index]
        if _protected(chunk) or chunk.action == CompressionAction.ARCHIVE:
            continue
        chunks[index] = archive_reference(chunk)

    # 如果引用本身仍使预算超限，移除最旧引用；原始来源仍在旁路归档中可检索。
    while sum(chunk.token_count for chunk in chunks) > target:
        removable = [
            (index, chunk)
            for index, chunk in enumerate(chunks)
            if chunk.action == CompressionAction.ARCHIVE and not _protected(chunk)
        ]
        if not removable:
            break
        index, chunk = min(removable, key=lambda pair: pair[1].source_turn)
        archived.append(chunk)
        del chunks[index]

    tokens_after = sum(chunk.token_count for chunk in chunks)
    return replace(
        result,
        chunks=chunks,
        archived_chunks=archived,
        tokens_after=tokens_after,
        budget_pressure=budget.pressure(result.tokens_before),
        budget_target_tokens=target,
        hard_budget_exceeded=tokens_after > budget.hard_limit_tokens,
    )


def _protected(chunk) -> bool:
    if chunk.metadata.get("current_user_turn"):
        return True
    if chunk.metadata.get("structured_memory"):
        return True
    if chunk.ctype in {ContextType.SYSTEM, ContextType.TASK_STATE}:
        return True
    text = chunk.text.lower()
    if any(
        marker in text
        for marker in ("必须", "不得", "禁止", "只能", "硬约束", "system instruction")
    ):
        return True
    if chunk.role != "user":
        return False
    padded = f" {text} "
    # A past-tense status ("never verified") is not an instruction. Archived
    # notes often contain this phrase and must remain eligible for eviction.
    # Keep a separate imperative "Never ..." in the same message protected.
    padded = re.sub(
        r"\bnever\s+(?:been\s+)?(?:verified|validated|approved|confirmed)\b",
        "unverified",
        padded,
    )
    return any(
        marker in padded
        for marker in (
            " remember ",
            " must ",
            " must not ",
            " do not ",
            " never ",
            " hard constraint",
            " required ",
        )
    )


def _is_evidence(chunk) -> bool:
    text = chunk.text.lstrip().lower()
    return chunk.ctype == ContextType.TOOL_INTERACTION and (
        text.startswith("verified tool result")
        or text.startswith("观察：read 结果：")
        or text.startswith("read 结果：")
        or text.startswith("观察：search 证据：")
        or text.startswith("search 证据：")
        or text.startswith("观察：mutation 结果：")
        or text.startswith("mutation 结果：")
        or text.startswith("观察：verification 结果：")
        or text.startswith("verification 结果：")
        or chunk.role in {"tool", "function"}
    )


def _build_task_memory(chunks, summarizer) -> ContextItem:
    selected, state_metadata = _select_current_workspace_evidence(chunks)
    source_ids = tuple(
        dict.fromkeys(
            source_id
            for chunk in selected
            for source_id in chunk.source_event_ids
        )
    )
    facts: list[tuple[str, str]] = []
    for chunk in selected:
        text = (
            chunk.text
            .replace("VERIFIED TOOL RESULT", "tool result")
            .replace("Verified Tool Result", "tool result")
            .replace("verified tool result", "tool result")
            .replace("观察：read 结果：", "")
            .replace("read 结果：", "")
            .replace("观察：search 证据：", "")
            .replace("search 证据：", "")
            .replace("观察：mutation 结果：", "mutation 结果：")
            .replace("观察：verification 结果：", "verification 结果：")
            .strip()
        )
        # 对超长单条工具输出做局部摘要，但保证每个来源至少贡献一条记录；
        # 对整个证据集合一次性摘要会偏向少数高分句并遗漏其他文档。
        # 真实工作区的带行号文件片段通常包含紧凑代码或测试断言，按普通长文本
        # 在 180 字处摘要会丢掉后半段契约并诱发重复读取。保留中小型文件片段，
        # 只有明显过长时才做抽取式摘要；旧知识库文本仍沿用较低阈值。
        summarize_threshold = 1_200 if text.startswith("文件=") else 180
        if summarizer is not None and len(text) > summarize_threshold:
            text = summarizer(text)
        doc_ids = _workspace_paths(chunk.text)
        doc_id = next(iter(doc_ids), "")
        if not doc_id:
            action = chunk.metadata.get("action") or {}
            if str(action.get("name", "")).strip() == "read":
                doc_id = str((action.get("args") or {}).get("doc_id", "")).strip()
        source_label = f"doc_id={doc_id}" if doc_id else f"event={chunk.source_event_ids[0]}"
        if text and text not in {fact for _, fact in facts}:
            facts.append((source_label, text))
    memory_text = "\n".join(f"- [{source}] {fact}" for source, fact in facts)
    digest = hashlib.sha256("|".join(source_ids).encode("utf-8")).hexdigest()[:12]
    return ContextItem(
        chunk_id=f"memory:{digest}",
        ctype=ContextType.LONG_TERM_MEMORY,
        text=(
            "[已验证工具证据；每行来自成功工具结果、read 或带文件行号的 search，可直接据此作答，无需重新读取或遍历候选]\n"
            "[工作区状态规则：mutation 后的文件版本取代此前 read/search；只有最新 workspace_epoch 的 verification 才有效]\n"
            f"read 结果：\n{memory_text}"
        ),
        source="derived:task_memory",
        role="user",
        source_turn=max((chunk.source_turn for chunk in selected), default=0),
        source_event_ids=source_ids,
        status=ContextStatus.COMPRESSED,
        action=CompressionAction.SUMMARIZE,
        importance=1.0,
        metadata={
            "structured_memory": True,
            "memory_kind": "evidence_facts",
            "source_count": len(source_ids),
            "trusted_tool_evidence": True,
            **state_metadata,
        },
    )


def _workspace_event_kind(text: str) -> str:
    lowered = text.lstrip().lower()
    if lowered.startswith(("观察：mutation 结果：", "mutation 结果：")):
        return "mutation"
    if lowered.startswith(("观察：verification 结果：", "verification 结果：")):
        return "verification"
    if lowered.startswith(("观察：read 结果：", "read 结果：")):
        return "read"
    if lowered.startswith(("观察：search 证据：", "search 证据：")):
        return "search"
    return "other"


def _workspace_paths(text: str) -> set[str]:
    """Extract normalized workspace paths from read/mutation/search evidence."""
    paths = {
        match.group(1).strip().replace("\\", "/")
        for match in re.finditer(r"文件=([^;\n，]+)", text)
    }
    for line in text.splitlines():
        match = re.match(r"\s*(?:观察：)?([^\s:]+):\d+:\s", line)
        if match:
            paths.add(match.group(1).strip().replace("\\", "/"))
    return {path for path in paths if path}


def _workspace_epoch(text: str) -> int | None:
    match = re.search(r"workspace_epoch=(\d+)", text)
    return int(match.group(1)) if match else None


def _select_current_workspace_evidence(chunks):
    """Remove facts invalidated by later mutations and stale test results.

    This is deterministic event-time invalidation.  It deliberately does not ask
    an LLM whether an old code snippet is still true.
    """
    ordered = sorted(chunks, key=lambda item: item.source_turn)
    latest_mutation_turn: dict[str, int] = {}
    latest_mutation_index: dict[str, int] = {}
    latest_epoch = 0
    for index, chunk in enumerate(ordered):
        if _workspace_event_kind(chunk.text) != "mutation":
            continue
        latest_epoch = max(latest_epoch, _workspace_epoch(chunk.text) or 0)
        for path in _workspace_paths(chunk.text):
            latest_mutation_turn[path] = chunk.source_turn
            latest_mutation_index[path] = index

    verification_indices = [
        index
        for index, chunk in enumerate(ordered)
        if _workspace_event_kind(chunk.text) == "verification"
    ]
    latest_verification_index = verification_indices[-1] if verification_indices else None
    superseded: list[str] = []
    selected = []
    verification_status = "NOT_RUN"
    for index, chunk in enumerate(ordered):
        kind = _workspace_event_kind(chunk.text)
        paths = _workspace_paths(chunk.text)
        stale = False
        if kind in {"read", "search"}:
            stale = any(chunk.source_turn < latest_mutation_turn.get(path, -1) for path in paths)
        elif kind == "mutation":
            stale = any(latest_mutation_index.get(path) != index for path in paths)
        elif kind == "verification":
            epoch = _workspace_epoch(chunk.text)
            stale = index != latest_verification_index or (
                epoch is not None and epoch < latest_epoch
            )
            if index == latest_verification_index:
                if stale:
                    verification_status = "STALE"
                elif "verification 结果：pass" in chunk.text.lower():
                    verification_status = "PASS"
                else:
                    verification_status = "FAIL"
        if stale:
            superseded.extend(chunk.source_event_ids)
        else:
            selected.append(chunk)
    return selected, {
        "workspace_state_aware": True,
        "workspace_mutation_epoch": latest_epoch,
        "latest_verification_status": verification_status,
        "stale_evidence_filtered_count": len(set(superseded)),
        "superseded_source_event_ids": list(dict.fromkeys(superseded)),
    }
