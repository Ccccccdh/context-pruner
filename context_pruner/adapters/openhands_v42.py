"""v42 candidate: inject current, revision-aware task state into v41 memory."""
from __future__ import annotations

from pydantic import PrivateAttr

from context_pruner.adapters.openhands_v41 import ContextPrunerCondenserV41


class ContextPrunerCondenserV42(ContextPrunerCondenserV41):
    _current_state: str = PrivateAttr(default='')

    def set_current_state(self, state: str) -> None:
        if len(state) > 3000:
            raise ValueError('Current task state exceeds 3000 characters')
        if len(self.protected_contract) + len(state) + 1 > 4000:
            raise ValueError('Combined protected contract and state exceed 4000 characters')
        self._current_state = state

    def condense(self, view, agent_llm=None):
        original = self.protected_contract
        try:
            self.protected_contract = (original + '\n' + self._current_state).strip()
            return super().condense(view, agent_llm)
        finally:
            self.protected_contract = original

    def export_state(self):
        state = super().export_state()
        state['adapter'] = 'openhands-condenser-v42-candidate'
        state['current_task_state_chars'] = len(self._current_state)
        return state
