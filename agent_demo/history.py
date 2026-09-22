"""运行期上下文管理：历史存储、消息渲染、压缩适配与轻量恢复。"""

from __future__ import annotations

from context_pruner import (
    ArchiveStore,
    ContextBudget,
    ContextEventKind,
    ContextLifecycleManager,
    ContextPruner,
)

from .utils import count_tokens


class HistoryManager:
    """维护消息历史，并在发送前按 method 压缩历史部分。

    - ``full_turns`` 保存完整历史，供基线方法回退和实验对照；
    - ``turns`` 是当前真正发给 LLM 的（可能已被压缩的）历史；
    - system_prompt 与 task_text 始终原样保留。

    pruner 系列方法使用 ContextLifecycleManager 的旁路归档与选择性恢复，
    不再把完整历史整体恢复到模型上下文。
    """

    def __init__(
        self,
        system_prompt: str,
        task_text: str,
        method: str = "none",
        window_steps: int = 4,
        pruner: ContextPruner | None = None,
        summarizer=None,
        recovery_cooldown_steps: int = 2,
        lifecycle_enabled: bool = True,
        context_budget: ContextBudget | None = None,
        archive_store: ArchiveStore | None = None,
    ):
        self.system_prompt = system_prompt
        self.task_text = task_text
        self.method = method
        self.window_steps = window_steps
        self.pruner = pruner
        self.summarizer = summarizer
        self.recovery_cooldown_steps = recovery_cooldown_steps
        self.lifecycle_enabled = lifecycle_enabled
        self.context_budget = context_budget

        self.full_turns: list[dict] = []
        self.turns: list[dict] = []
        self.recovery_count = 0
        self.compression_overhead_tokens = 0
        self.compression_overhead_input_tokens = 0
        self.compression_overhead_output_tokens = 0
        self._recovery_cooldown = 0
        self.lifecycle = ContextLifecycleManager(
            task_state=task_text,
            pruner=pruner,
            summarizer=summarizer,
            archive_store=archive_store,
        )

    def add(
        self,
        role: str,
        content: str,
        *,
        kind: ContextEventKind | None = None,
        metadata: dict | None = None,
    ) -> None:
        turn = {"role": role, "content": content}
        self.full_turns.append(turn)
        self.turns.append(turn)
        if self.lifecycle_enabled:
            self.lifecycle.observe_turn(turn, kind=kind, metadata=metadata)

    def build_messages(self) -> list[dict]:
        """渲染发给 LLM 的消息（含 system + 当前任务 + 当前历史）。"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"当前任务：{self.task_text}"},
        ]
        for turn in self.turns:
            messages.append(
                {
                    "role": str(turn.get("role", "user")),
                    "content": str(turn.get("content", "")),
                }
            )
        return messages

    def apply_compression(self) -> None:
        if self.method == "none":
            self.turns = list(self.full_turns)
            return
        if self._recovery_cooldown > 0 and self.method not in {"pruner", "pruner_v0", "pruner_v1"}:
            self._recovery_cooldown -= 1
            self.turns = list(self.full_turns)
            return
        if self.method == "window":
            self._apply_window()
            return
        if self.method == "summary":
            self._apply_summary()
            return
        if self.method in {"pruner", "pruner_v0", "pruner_v1"}:
            self._apply_pruner()
            return
        raise NotImplementedError(f"未知压缩方法：{self.method}")

    def recover(self, reason: str = "", query: str = "") -> bool:
        """按方法恢复；pruner 使用来源映射检索，基线保留完整回退行为。"""
        if not self.full_turns:
            return False
        if self.method in {"pruner", "pruner_v0", "pruner_v1"}:
            before = self.lifecycle.recovery_count
            snapshot = self.lifecycle.recover(reason=reason, query=query)
            if self.lifecycle.recovery_count == before:
                return False
            self.turns = self._turns_from_snapshot(snapshot)
            self.recovery_count = self.lifecycle.recovery_count
            return True
        self.recovery_count += 1
        self._recovery_cooldown = self.recovery_cooldown_steps
        self.turns = list(self.full_turns)
        return True

    def inject_context_loss(self, terms: list[str]) -> int:
        """仅供故障注入实验：把指定证据移到旁路归档，返回移出的 token 数。"""
        if not self.lifecycle_enabled:
            return 0
        before = self.tokens_in()
        snapshot = self.lifecycle.force_archive_matching(terms)
        self.turns = self._turns_from_snapshot(snapshot)
        return max(0, before - self.tokens_in())

    def tokens_in(self) -> int:
        return sum(count_tokens(m["content"]) for m in self.build_messages())

    def full_tokens_in(self) -> int:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"当前任务：{self.task_text}"},
            *self.full_turns,
        ]
        return sum(count_tokens(str(message.get("content", ""))) for message in messages)

    @property
    def archive_count(self) -> int:
        return self.lifecycle.archive_count

    @property
    def recovered_tokens(self) -> int:
        return self.lifecycle.recovered_tokens

    @property
    def checkpoint_count(self) -> int:
        return self.lifecycle.checkpoint_count

    @property
    def lifecycle_records(self) -> list:
        return list(self.lifecycle.records)

    @property
    def runtime_events(self) -> list:
        return list(self.lifecycle.events)

    def record_boundary(self, kind: ContextEventKind, metadata: dict | None = None) -> None:
        if self.lifecycle_enabled:
            self.lifecycle.record_boundary(kind, metadata)

    # ---- 压缩动作 ----

    def _steps(self, turns: list[dict]) -> list[tuple[int, int]]:
        """把 turns 切成 ReAct 步骤：assistant 消息 + 紧跟的 observation(user)。"""
        steps: list[tuple[int, int]] = []
        i = 0
        while i < len(turns):
            role = turns[i].get("role")
            if role == "assistant":
                if i + 1 < len(turns) and turns[i + 1].get("role") == "user":
                    steps.append((i, i + 1))
                    i += 2
                else:
                    steps.append((i, i))
                    i += 1
            else:
                steps.append((i, i))
                i += 1
        return steps

    def _apply_window(self) -> None:
        if not self.full_turns or self.window_steps <= 0:
            self.turns = list(self.full_turns)
            return
        steps = self._steps(self.full_turns)
        keep = steps[-self.window_steps :]
        idx = [j for start, end in keep for j in range(start, end + 1)]
        self.turns = [self.full_turns[j] for j in idx]

    def _apply_summary(self) -> None:
        if not self.full_turns:
            self.turns = []
            return
        steps = self._steps(self.full_turns)
        recent_steps = max(1, min(self.window_steps, 2))
        if len(steps) <= recent_steps:
            self.turns = list(self.full_turns)
            return
        recent_idx = [j for start, end in steps[-recent_steps:] for j in range(start, end + 1)]
        old_idx = [j for start, end in steps[:-recent_steps] for j in range(start, end + 1)]
        old_turns = [self.full_turns[j] for j in old_idx]
        recent_turns = [self.full_turns[j] for j in recent_idx]

        text = "\n".join(
            f"{t.get('role')}: {t.get('content', '')}" for t in old_turns
        )
        in_tokens = count_tokens(text)
        if self.summarizer is not None:
            summary = str(self.summarizer(text)).strip()
        else:
            summary = self._fallback_summary(text)
        out_tokens = count_tokens(summary)
        self.compression_overhead_input_tokens += in_tokens
        self.compression_overhead_output_tokens += out_tokens
        self.compression_overhead_tokens += in_tokens + out_tokens
        self.turns = [{"role": "assistant", "content": f"[历史摘要] {summary}"}] + recent_turns

    def _apply_pruner(self) -> None:
        if not self.full_turns:
            self.turns = []
            return
        managed_budget = None
        if self.context_budget is not None:
            fixed_tokens = count_tokens(self.system_prompt) + count_tokens(
                f"当前任务：{self.task_text}"
            )
            managed_budget = self.context_budget.reserve(fixed_tokens)
        snapshot = self.lifecycle.compress(reason="before_model", budget=managed_budget)
        self.turns = self._turns_from_snapshot(snapshot)

    @staticmethod
    def _turns_from_snapshot(snapshot) -> list[dict]:
        return [
            {
                "role": getattr(item, "role", "assistant"),
                "content": item.text,
                "_source_event_ids": list(item.source_event_ids),
                "_status": item.status.value,
                "_action": item.action.value,
            }
            for item in snapshot.chunks
            if item.text
        ]

    @staticmethod
    def _fallback_summary(text: str, head: int = 120, tail: int = 80) -> str:
        text = text.strip()
        if len(text) <= head + tail + 20:
            return text
        return text[:head] + "\n…[摘要]…\n" + text[-tail:]
