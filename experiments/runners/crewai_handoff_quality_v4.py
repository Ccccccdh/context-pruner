"""Prospective r4 handoff contract; never rescore the frozen r3 batch.

The first-pass model output remains observable. Only a factual handoff can be
locally canonicalized; absent facts require a separately metered recovery.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re


HANDOFF_FACTS = {
    "incident_triage": ("degraded", "3.8"),
    "release_readiness": ("184", "0"),
    "customer_migration": ("eu-west", "02:00 UTC"),
}

_MARKER = re.compile(r"^\s*HANDOFF(?=\s|[:：|]|$)[\s:：|]*", re.IGNORECASE)


@dataclass(frozen=True)
class HandoffAssessment:
    raw: str
    facts_present: bool
    strict_marker: bool
    marker_variant: bool
    one_line: bool
    first_pass_valid: bool
    canonical: str | None
    needs_recovery: bool

    def as_record(self) -> dict:
        return asdict(self)


def assess_handoff(task_id: str, raw: str) -> HandoffAssessment:
    """Check facts before formatting; never invent or infer a missing fact."""
    if task_id not in HANDOFF_FACTS:
        raise ValueError(f"unsupported task: {task_id}")
    text = raw.strip()
    facts_present = all(fact.casefold() in text.casefold() for fact in HANDOFF_FACTS[task_id])
    one_line = "\n" not in text and "\r" not in text
    strict_marker = text.startswith("HANDOFF ")
    marker_variant = bool(_MARKER.match(text)) and not strict_marker
    first_pass_valid = facts_present and strict_marker and one_line
    canonical: str | None = None
    if facts_present and one_line:
        body = _MARKER.sub("", text, count=1).strip()
        if body:
            canonical = "HANDOFF " + body
    return HandoffAssessment(
        raw=raw, facts_present=facts_present, strict_marker=strict_marker,
        marker_variant=marker_variant, one_line=one_line,
        first_pass_valid=first_pass_valid, canonical=canonical,
        needs_recovery=not facts_present or not one_line,
    )
