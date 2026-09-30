"""Exploratory long-context profile of the v11 OpenHands condenser.

The evidence selection and safety checks are inherited unchanged. Thresholds
were selected from zero-provider replay of saved v30 trajectories and need a
new-task live evaluation before any efficacy claim.
"""

from .openhands_v11 import ContextPrunerCondenserV11


class ContextPrunerCondenserV36(ContextPrunerCondenserV11):
    trigger_tokens: int = 20000
    target_tokens: int = 16000
    hard_tokens: int = 28000
