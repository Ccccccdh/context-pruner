"""Keep the initial public issue and task contract verbatim during pruning."""

from __future__ import annotations

import inspect
from typing import Any

from agents.run import ModelInputData


class TaskAnchorFilter:
    """Present short protected placeholders to the pruner, then restore text.

    Only the first ``count`` user messages are pinned. A missing or duplicated
    placeholder restores the original input rather than sending a damaged task.
    The wrapper is installed *inside* the shared trigger gate, so the trigger
    still measures the actual unmodified model input.
    """

    def __init__(self, inner: Any, *, count: int = 2) -> None:
        if count < 0:
            raise ValueError('count must be nonnegative')
        self.inner = inner
        self.count = count
        self.calls = 0
        self.pinned_items = 0
        self.restore_failures = 0

    async def __call__(self, data: Any) -> ModelInputData:
        self.calls += 1
        original = list(data.model_data.input or [])
        prepared = []
        pinned: dict[str, Any] = {}
        for item in original:
            role = str(item.get('role', '')).lower() if isinstance(item, dict) else str(getattr(item, 'role', '')).lower()
            if role == 'user' and len(pinned) < self.count:
                anchor_id = f'initial-user-{len(pinned)}'
                pinned[anchor_id] = item
                prepared.append({
                    'role': 'system',
                    'content': f'[protected initial user task {len(pinned) - 1}]',
                    '_context_pruner_task_anchor': anchor_id,
                })
            else:
                prepared.append(item)
        self.pinned_items += len(pinned)
        wrapped = type('CallData', (), {'model_data': ModelInputData(
            input=prepared, instructions=data.model_data.instructions,
        )})()
        result = self.inner(wrapped)
        if inspect.isawaitable(result):
            result = await result
        restored = []
        seen: set[str] = set()
        for item in result.input or []:
            anchor_id = item.get('_context_pruner_task_anchor') if isinstance(item, dict) else None
            if anchor_id is None:
                restored.append(item)
                continue
            if anchor_id not in pinned or anchor_id in seen:
                self.restore_failures += 1
                return data.model_data
            restored.append(pinned[anchor_id])
            seen.add(anchor_id)
        if seen != set(pinned):
            self.restore_failures += 1
            return data.model_data
        return ModelInputData(input=restored, instructions=result.instructions)

    def metrics_dict(self) -> dict[str, Any]:
        metrics = dict(self.inner.metrics_dict()) if hasattr(self.inner, 'metrics_dict') else {}
        metrics.update({
            'task_anchor_filter_calls': self.calls,
            'task_anchor_pinned_items': self.pinned_items,
            'task_anchor_restore_failures': self.restore_failures,
        })
        return metrics
