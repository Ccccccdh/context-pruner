"""Registered literal spans for v6 pointer retention.

What is new compared with the frozen v5 registry
------------------------------------------------
``experiments/runners/openai_agents_literal_registry_v5.py`` stays byte-identical
to the v5 freeze.  v6 does not edit it; it *extends* it with the one fact v5 did
not register: **which lines of a registered source range carry the registered
literals**, and therefore which lines must stay verbatim when the rest of that
range is replaced by a line-range pointer.

That extension is what makes a cheaper retention mechanism possible at all.  v5
could only keep a constraint-carrying tool output *whole* (4,896 characters on
Django), because it had no way to say "these 15 lines are what the frozen quality
contract depends on, the other 75 are recoverable by pointer".  v6 can say it, and
this module is the registry that makes the claim checkable:

* :func:`literal_line_numbers` recomputes the span from the **public baseline
  file** (``.tooling/upstream/<repo>/<path>``) by matching the registered literal
  strings case-insensitively, line by line - the same text the tool returns;
* :func:`verify` fails loudly when a registered range's tool is unknown, when the
  file is missing, or when the recomputed span disagrees with the registered
  :class:`RegisteredSpan`;
* every elision note in v6 must state the exact omitted line interval, and
  :func:`audit_span_pointers` checks that the union of the kept lines and the
  omitted intervals is exactly the registered range with no gap and no overlap -
  so "the omitted text is recoverable by re-issuing the tool" is a property of the
  artifact the model receives, not a claim about intent.

Everything here is public information: literal strings are substrings of the
public SWE-bench issue statements, and the ranges are fixed slices of the public
baseline repository.  No reference patch, host test patch or credential is read,
and nothing from this module is ever written into a result row - only booleans,
counts and SHA256 digests are persisted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from experiments.runners import openai_agents_literal_registry_v5 as base

#: Schema version of this registry extension.  A new schema means the registered
#: spans changed and results are not comparable with earlier batches.
REGISTRY_SCHEMA = "openai_agents_literal_registry_v6"

#: Context lines kept around every literal-carrying line.  Zero is the honest
#: minimum for this mechanism: the registered literal lines are what the frozen
#: quality contract depends on, and every other line is recoverable by the note's
#: path + line-range pointer.  It is a frozen constant, not a runtime choice.
CONTEXT_LINES = 0


@dataclass(frozen=True)
class RegisteredSpan:
    """One registered source range and the lines of it that carry its literals.

    ``literal_first_line``/``literal_last_line`` are the outer bounds of the
    literal-carrying lines *before* the context window is applied, so the
    registry states the fact and the mechanism decides how much context to keep.
    ``literal_lines`` is the exact, recomputable set.
    """

    tool: str
    repo: str
    path: str
    first_line: int
    last_line: int
    literal_lines: tuple[int, ...]
    literal_labels: tuple[str, ...]

    @property
    def literal_first_line(self) -> int:
        return min(self.literal_lines) if self.literal_lines else self.first_line

    @property
    def literal_last_line(self) -> int:
        return max(self.literal_lines) if self.literal_lines else self.first_line

    def range_label(self) -> str:
        return f"{self.repo}/{self.path}:{self.first_line}-{self.last_line}"

    def span_label(self) -> str:
        return f"{self.repo}/{self.path}:{self.literal_first_line}-{self.literal_last_line}"


def _spans_for(task: str) -> tuple[RegisteredSpan, ...]:
    """Build the span table for one task from the v5 registered sources.

    The literal line numbers are recomputed from the public baseline text, never
    declared by hand: a declared span that does not match the file is exactly the
    kind of silent drift ``verify`` exists to prevent.
    """
    spans: list[RegisteredSpan] = []
    for source in base.SOURCES.get(str(task), ()):
        labels = tuple(source.literal_labels)
        spans.append(
            RegisteredSpan(
                tool=source.tool,
                repo=source.repo,
                path=source.path,
                first_line=source.first_line,
                last_line=source.last_line,
                literal_lines=tuple(
                    _recompute_literal_lines(task, source, labels)
                ),
                literal_labels=labels,
            )
        )
    return tuple(spans)


def _registered_source_lines(source: Any) -> list[str] | None:
    root = Path(__file__).resolve().parents[2]
    path = root / ".tooling" / "upstream" / source.repo / source.path
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").splitlines()


def _recompute_literal_lines(task: str, source: Any, labels: Sequence[str]) -> list[int]:
    """Line numbers inside the registered range that carry a registered literal."""
    lines = _registered_source_lines(source)
    if lines is None:
        return []
    literals = [
        phrase
        for label in labels
        for phrase in base.literals_for(task).get(label, ())
    ]
    if not literals:
        return []
    found: list[int] = []
    for number in range(source.first_line, min(source.last_line, len(lines)) + 1):
        text = lines[number - 1]
        lowered = text.lower()
        if any(str(phrase).lower() in lowered for phrase in literals):
            found.append(number)
    return found


def literal_lines_in_text(task: str, text: str) -> tuple[int, int, list[int]] | None:
    """Line numbers of one tool output that carry a registered literal.

    Returns ``(first_number, last_number, numbers)`` parsed from the line-numbered
    source view itself, or ``None`` when the text is not a parseable source view.
    The numbers are taken from the text, so a re-ranged or renamed source fails
    here instead of producing a pointer to the wrong lines.
    """
    lines = str(text).splitlines()
    if not lines:
        return None
    if base.parse_source_pointer(text) is None:
        return None
    literals = [
        phrase
        for phrases in base.literals_for(task).values()
        for phrase in phrases
    ]
    numbers: list[int] = []
    for line in lines[1:]:
        stripped = line.strip()
        head, _, body = stripped.partition(":")
        if not head.isdigit():
            return None
        lowered = body.lower()
        if any(str(phrase).lower() in lowered for phrase in literals):
            numbers.append(int(head))
    if not numbers:
        return None
    return numbers[0], numbers[-1], numbers


def spans(task: str) -> tuple[RegisteredSpan, ...]:
    """The frozen span table for one task (recomputed from the public files)."""
    return _SPAN_CACHE.get(str(task)) or _cache(task)


_SPAN_CACHE: dict[str, tuple[RegisteredSpan, ...]] = {}


def _cache(task: str) -> tuple[RegisteredSpan, ...]:
    value = _spans_for(task)
    _SPAN_CACHE[str(task)] = value
    return value


def span_for_tool(task: str, tool_name: str) -> RegisteredSpan | None:
    for span in spans(task):
        if span.tool == str(tool_name):
            return span
    return None


def verify(task: str) -> list[str]:
    """Registration problems for one task (empty list means verified)."""
    problems = list(base.verify(task))
    for span in spans(task):
        if span.literal_labels and not span.literal_lines:
            problems.append(
                f"{task}/{span.tool}: registered literals have no registered line span"
            )
        if span.literal_lines:
            if span.literal_lines[0] < span.first_line or span.literal_lines[-1] > span.last_line:
                problems.append(
                    f"{task}/{span.tool}: literal span {span.span_label()} outside the "
                    f"registered range {span.range_label()}"
                )
    return problems


def all_problems(tasks: Sequence[str] | None = None) -> list[str]:
    values = tuple(tasks) if tasks is not None else tuple(base.SOURCES)
    return [problem for task in values for problem in verify(task)]


def base_registry_fingerprint(task: str) -> str:
    return base.registry_fingerprint(task)


def registry_fingerprint(task: str) -> str:
    """Digest of the v5 registry slice plus the v6 span table (audit cross-check)."""
    payload = {
        "schema": REGISTRY_SCHEMA,
        "base_schema": base.REGISTRY_SCHEMA,
        "base_fingerprint": base.registry_fingerprint(str(task)),
        "task": str(task),
        "context_lines": CONTEXT_LINES,
        "spans": [
            {
                "tool": span.tool,
                "repo": span.repo,
                "path": span.path,
                "first_line": span.first_line,
                "last_line": span.last_line,
                "literal_lines": list(span.literal_lines),
                "literal_labels": list(span.literal_labels),
            }
            for span in spans(task)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


#: A line of a line-numbered source view: ``"<number>: <text>"``.
def parse_numbered_line(line: str) -> tuple[int, str] | None:
    stripped = str(line).strip()
    head, separator, body = stripped.partition(":")
    if not separator or not head.isdigit():
        return None
    return int(head), body


def elision_completeness(
    text: str,
    kept_numbers: Sequence[int],
    omitted_intervals: Sequence[Sequence[int]],
) -> list[str]:
    """Problems with one elided source view's kept/omitted line partition.

    The check is exact: every line number of the registered range must be either
    kept or inside exactly one omitted interval, and no interval may overlap
    another.  Anything else means the note does not describe the omission
    truthfully, and the mechanism may not emit it.
    """
    parsed = base.parse_source_pointer(text)
    if parsed is None:
        return ["text is not a line-numbered source view"]
    _, _, first, last = parsed
    expected = list(range(first, last + 1))
    kept = sorted(set(int(value) for value in kept_numbers))
    covered: list[int] = []
    problems: list[str] = []
    for interval in omitted_intervals:
        values = list(interval)
        if len(values) != 2:
            problems.append(f"malformed omitted interval: {values}")
            continue
        start, end = int(values[0]), int(values[1])
        if start > end:
            problems.append(f"inverted omitted interval: {values}")
            continue
        covered.extend(range(start, end + 1))
    if sorted(kept + covered) != expected:
        problems.append(
            f"kept+omitted do not partition the registered range {first}-{last}"
        )
    if len(set(covered)) != len(covered):
        problems.append("overlapping omitted intervals")
    if any(number < first or number > last for number in kept + covered):
        problems.append("kept/omitted line outside the source range")
    return problems
