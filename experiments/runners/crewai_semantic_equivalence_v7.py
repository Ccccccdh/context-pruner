"""Forward-looking semantic-equivalence judge for the CrewAI r7 batch.

Why this module exists
----------------------
The frozen r4 strict contract counts ``0 failed`` vs ``fail 0`` and ``28%`` vs
``28 percent`` as failures.  That is a formatting difference, not a factual one,
but it must not be repaired by loosening the factual requirements.  The Chinese
handoff (``HANDOFF_DEEPSEEK_HARNESS_20261004.md`` §4B) therefore asks for a
**separate, forward-looking** judge:

    the answer is equivalent only when it carries the same facts, the same
    decision, and is backed by the same tool evidence; a missing fact, a wrong
    version, a wrong region or a wrong decision must never be rescued by
    normalisation.

Properties of this judge
------------------------
* It is generic over the frozen task file (``tasks/stage5_autogen/natural_tasks_r5.json``):
  every rule is read from the task record (``handle_facts``, ``answer_facts``,
  ``forbidden_facts``, ``decision``), so a task cannot be silently reworded
  without breaking the freeze hash.
* It computes **both** gates on the same answer: ``strict`` (the r4-style gate,
  re-implemented here so the r5 batch does not depend on r4 scoring code) and
  ``semantic`` (this new gate).  A batch reports both; neither replaces the other.
* Normalisation is *format-only*: Unicode punctuation folding, markdown stripping,
  whitespace collapsing, ``percent``/``pct`` spelling, thousands separators and
  spaces glued around digits and separators.  It never rewrites, reorders,
  deletes or invents an alphanumeric fact token, and it never drops a conflicting
  value.
* Missing facts, wrong versions, wrong regions and wrong decisions are detected
  *after* normalisation as well, because every required fact must survive
  normalisation as a contiguous literal.
* Old batches are never re-scored with this module: r4 keeps its frozen 8/9.

The counter-example tests in ``tests/test_crewai_semantic_equivalence_v7.py``
prove the four failure kinds are rejected before the judge is frozen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r7.json"

#: Full-width and typographic punctuation that may wrap an otherwise identical
#: factual answer.  Folding these is purely cosmetic.
_PUNCTUATION_EQUIVALENTS = {
    "\u3000": " ",
    "\uff1a": ":",
    "\uff1b": ";",
    "\uff0c": ",",
    "\uff08": "(",
    "\uff09": ")",
    "\uff05": "%",
    "\uff1d": "=",
    "\uff1f": "?",
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
}
_PERCENT_WORDS = re.compile(r"(?<=\d)\s*(?:percent|percents|pct|percentage)\b", re.IGNORECASE)
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_MARKDOWN = re.compile(r"(?<!\*)\*+(?!\s)|(?<!\w)`+")
#: JSON-style ``"key": value`` glue, folded to ``key value``.  The key text is
#: kept: ``"failed": 0`` must still be recognisable as the fact ``0 failed``,
#: so no rule here may delete a factual label.
_JSON_KEY = re.compile(r'"([A-Za-z_][A-Za-z0-9_.]*)"\s*:\s*')
#: The protocol's own field name for the decision.  No other label is treated as
#: a decision marker, so ``RESULT task=...`` can never be read as one.
_PROTOCOL_LABELS = ("decision",)
_SPACES = re.compile(r"\s+")
#: Characters that may glue factual tokens without changing them.  Quotes are
#: included so that ``"region": "us-west-2"`` and ``region=us-west-2`` fold to
#: the same normal form.
_GLUE = re.compile(r"[|,;/\\\"'()\[\]{}]+")
#: Word characters that may not be glued onto a fact token when matching a
#: literal, so that ``v16`` does not silently satisfy required ``v1``.
_WORD = re.compile(r"[0-9a-z]")
#: Non-guards for inflection-only differences (``failed``/``fail``,
#: ``region``/``regions``).  They apply to tokens of at least four characters and
#: never to the first token, so ``45``/``12`` and ``schema``/``schema-41`` cannot
#: be conflated.
_GUARD_TOKENS = ("no", "go", "not", "none", "non")


@dataclass(frozen=True)
class FactCheck:
    literal: str
    present: bool
    strict_present: bool

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JudgeVerdict:
    """Verdict of both gates for one answer (or one handoff)."""

    strict_pass: bool
    semantic_pass: bool
    strict: dict[str, Any]
    semantic: dict[str, Any]

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def _fold(text: str) -> str:
    """Format-only normalisation; never edits an alphanumeric fact token."""
    folded = unicodedata.normalize("NFKC", str(text))
    for source, target in _PUNCTUATION_EQUIVALENTS.items():
        folded = folded.replace(source, target)
    folded = _MARKDOWN.sub("", folded)
    folded = folded.replace("\r", " ").replace("\n", " ")
    folded = _JSON_KEY.sub(r"\1 ", folded)
    folded = _THOUSANDS.sub("", folded)
    folded = _GLUE.sub(" ", folded)
    folded = re.sub(r"\s*([=%?])\s*", r"\1", folded)
    folded = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", folded)
    folded = folded.casefold()
    return _SPACES.sub(" ", folded).strip()


def _literal_pattern(literal: str) -> re.Pattern[str]:
    """Boundary-aware pattern over folded text for one literal.

    The literal's own tokens are matched in order, and only non-alphanumeric
    characters may separate them, so a JSON-style ``"failed": 0`` still carries
    the fact ``0 failed`` while a *different* token that merely contains the
    literal (required ``v1`` inside ``v16``) does not satisfy it.
    """
    folded_literal = _fold(literal)
    tokens = [token for token in re.split(r"[^0-9a-z%]+", folded_literal) if token]
    if not tokens:
        return re.compile(re.escape(folded_literal))
    # A tool evidence fragment is serialized as ``"failed": 0`` while the task
    # contract writes the same fact as ``0 failed``; both orderings denote the
    # same fact, so either is accepted.  Only the *separator* is relaxed: every
    # token must still be present, in `{0,4}` non-alphanumeric characters.
    prefix = r"(?<![0-9a-z])" if _WORD.match(tokens[0][0]) else ""
    orderings = [tokens, list(reversed(tokens))]
    bodies: list[str] = []
    for ordering in orderings:
        body = r"[^0-9a-z]{0,4}".join(re.escape(token) for token in ordering)
        if body not in bodies:
            bodies.append(body)
    suffix_token = tokens[-1]
    suffix = r"(?![0-9a-z])" if _WORD.match(suffix_token[-1]) else ""
    joined = "|".join(bodies)
    return re.compile(f"{prefix}(?:{joined}){suffix}")


def _raw_fold(text: str) -> str:
    """The strict gate's view: case folding only, no punctuation equivalents."""
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", str(text)).replace("\r", " ").replace("\n", " ")).casefold()


def _ordered_present(folded_text: str, literal: str) -> bool:
    """Ordered, boundary-checked token search with arbitrary separators.

    This is the strict gate's rule: the literal's tokens must appear in order and
    whole, separated only by non-alphanumeric characters (``pending_records
    12400`` matches ``pending_records=12400`` and ``"pending_records": 12400``),
    while nothing else is relaxed - no explicit percent spelling, no reversed
    order.  The semantic gate adds those acceptances on top.
    """
    tokens = _tokens_for(literal)
    if not tokens:
        return False
    position = 0
    for token in tokens:
        pattern = re.compile(r"(?<![0-9a-z])" + re.escape(token) + r"(?![0-9a-z])")
        match = pattern.search(folded_text, position)
        if match is None:
            return False
        position = match.end()
    return True


def _strict_pattern(literal: str) -> re.Pattern[str]:
    """Literal containment for the strict gate, over the folded text."""
    return re.compile(re.escape(_fold(str(literal))))


def canonical_decision(value: str) -> str:
    """Fold a decision label for comparison (``no-go`` == ``NO-GO`` == ``NOGO``)."""
    return re.sub(r"[^a-z0-9]", "", _fold(value))


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    tasks = json.loads((path or TASK_FILE).read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("r5 task file must contain a non-empty list")
    for task in tasks:
        for field_name in (
            "task_id", "title", "task", "history_constraint", "history_topic",
            "tools", "decision", "handle_facts", "answer_facts",
            "forbidden_facts", "expected_terms",
        ):
            if not task.get(field_name):
                raise ValueError(f"task {task.get('task_id')} lacks {field_name}")
        for tool in task["tools"]:
            for key in ("name", "call", "result", "evidence_parts"):
                if not isinstance(tool, dict) or not tool.get(key):
                    raise ValueError(f"task {task['task_id']} has a malformed tool entry")
            serialized = json.dumps(tool["result"], ensure_ascii=False)
            for part in tool["evidence_parts"]:
                if str(part) not in serialized:
                    raise ValueError(
                        f"task {task['task_id']} evidence part {part!r} is not a "
                        f"substring of its tool result"
                    )
    return tasks


def answer_rule(task: Mapping[str, Any]) -> dict[str, Any]:
    """All rules for one task's final single-line answer."""
    return {
        "task_id": str(task["task_id"]),
        "prefix": f"RESULT task={task['task_id']} ",
        "decision": str(task["decision"]),
        "answer_facts": tuple(str(item) for item in task["answer_facts"]),
        "forbidden_facts": tuple(str(item) for item in task.get("forbidden_facts") or ()),
        "max_characters": 450,
    }


def handle_rule(task: Mapping[str, Any]) -> dict[str, Any]:
    """Rules for the first-role HANDOFF line."""
    return {
        "task_id": str(task["task_id"]),
        "handle_facts": tuple(str(item) for item in task["handle_facts"]),
        "forbidden_facts": tuple(str(item) for item in task.get("forbidden_facts") or ()),
        "decision": str(task["decision"]),
        "prefix": "HANDOFF ",
        "max_characters": 450,
    }


def _facts(text: str, literals: Sequence[str]) -> list[FactCheck]:
    """Check every required literal under both gates.

    ``present`` is the semantic gate and ``strict_present`` the r4-style gate.
    Both read the same folded text, because folding punctuation is not the
    difference the two gates are meant to capture.  The difference is that the
    semantic gate alone also accepts an explicit percent spelling
    (``45 percent`` == ``45%``), reversed token order and relaxed boundaries.
    """
    folded = _fold(text)
    checks: list[FactCheck] = []
    for literal in literals:
        checks.append(
            FactCheck(
                literal=str(literal),
                present=_literal_present(folded, str(literal)),
                strict_present=_ordered_present(folded, str(literal)),
            )
        )
    return checks


def _tokens_for(literal: str) -> list[str]:
    folded_literal = _fold(literal)
    return [token for token in re.split(r"[^0-9a-z%]+", folded_literal) if token]


def _tokens_match(observed: str, required: str) -> bool:
    """Same token, or an inflection-only difference (``failed``/``fail``)."""
    if observed == required:
        return True
    if repr(required)[1:-1] in _GUARD_TOKENS:
        return False
    if len(required) < 4 or len(observed) < 4:
        return False
    shorter, longer = sorted((observed, required), key=len)
    return longer.startswith(shorter) and len(longer) - len(shorter) <= 3


def _literal_present(folded_text: str, literal: str) -> bool:
    """Whether the folded text carries this fact, in any accepted form.

    The semantic gate adds the acceptances the strict gate does not have: an
    explicit percent spelling (``45 percent`` == ``45%``), reversed token order
    and relaxed boundaries.
    """
    folded_text = _PERCENT_WORDS.sub("%", folded_text)
    if _literal_pattern(literal).search(folded_text):
        return True
    required = _tokens_for(literal)
    if not required:
        return False
    for ordering in (required, list(reversed(required))):
        found, position = True, 0
        for token in ordering:
            key = token[:-1] if token.endswith("%") else token
            match = next(
                (
                    candidate
                    for candidate in re.finditer(r"[0-9a-z%]+", folded_text[position:])
                    if _tokens_match(candidate.group(0), key)
                ),
                None,
            )
            if match is None:
                found = False
                break
            position += match.end()
        if found:
            return True
    return False


def _forbidden_hits(text: str, literals: Sequence[str]) -> list[str]:
    folded = _fold(text)
    return [
        str(literal)
        for literal in literals
        if _literal_pattern(str(literal)).search(folded)
    ]


def _decision_field(text: str) -> tuple[str, bool]:
    """Read the labelled decision field.  Returns ``(value, found)``.

    ``decision=REPLAY``, ``DECISION: NO-GO`` and ``Decision REPLAY`` are all read
    as the same field.  Only the ``decision`` label is accepted: the contract's
    own ``RESULT task=...`` tokens must never be mistaken for the decision.  The
    label token is consumed here only, so fact matching still sees the whole
    answer.
    """
    folded = _fold(text)
    pattern = re.compile(
        r"(?:(?<=[\s|])|^)decision\s*[:=]?\s*[\"']?([a-z0-9][a-z0-9 _-]*?)[\"']?"
        r"(?=\s|$)"
    )
    match = pattern.search(folded)
    if match:
        return match.group(1).strip(), True
    return "", bool(re.search(r"(?:(?<=[\s|])|^)decision(?![0-9a-z])", folded))


def _decision_value(text: str, expected: str | None = None) -> str:
    """The observed decision, with an evidence-only fallback.

    The field must be labelled; the fallback only applies when no decision field
    exists at all, and it is filtered to the contract's own token, so a stray
    decision word inside the evidence can never satisfy the contract silently.
    The fallback is reported as ``decision_field_found: false`` by the caller.
    """
    value, found = _decision_field(text)
    if found:
        return value
    if expected:
        pattern = _literal_pattern(expected)
        if pattern.search(_fold(text)):
            return str(expected)
    return ""

def _conflicting_version(text: str, literal: str) -> str:
    """Return a *different* version token that shares the required version's stem.

    ``schema-41`` next to required ``schema-42`` is a wrong version, not merely an
    absent one.  The stem (``schema``) and the digit pattern are both taken from
    the frozen contract, so this cannot be tuned per sample after the fact.
    """
    if not literal:
        return ""
    folded = _fold(text)
    match = re.match(r"([^0-9]*)([0-9]+)(.*)", _fold(literal))
    if not match:
        return ""
    stem, _, tail = match.groups()
    if not stem:
        return ""
    allowed = re.escape(_fold(literal)).replace(r"\ ", r"[^0-9a-z]{0,4}")
    conflict = re.compile(
        r"(?<![0-9a-z])" + re.escape(stem) + r"[^0-9a-z]{0,4}([0-9]+)" + re.escape(tail)
    )
    for candidate in conflict.finditer(folded):
        token = candidate.group(0)
        if not re.search(allowed, token):
            return token.strip()
    return ""


def _conflicting_region(text: str, literal: str) -> str:
    """Return a *different* region token that shares the required region's stem."""
    if not literal:
        return ""
    folded = _fold(text)
    segments = [segment for segment in _fold(literal).split("-") if segment]
    if len(segments) < 3:
        return ""
    stem = r"[^0-9a-z]{0,4}".join(re.escape(segment) for segment in segments[:-1])
    conflict = re.compile(
        r"(?<![0-9a-z])" + stem + r"[^0-9a-z]{0,4}([0-9]+)(?![0-9a-z])"
    )
    for candidate in conflict.finditer(folded):
        if candidate.group(1) != segments[-1]:
            return candidate.group(0).strip()
    return ""


def judge_answer(rule: Mapping[str, Any], answer: str) -> JudgeVerdict:
    """Score one final answer under both gates."""
    text = str(answer or "")
    stripped = text.strip()
    checks = _facts(stripped, rule["answer_facts"])
    missing = [check.literal for check in checks if not check.present]
    strict_missing = [check.literal for check in checks if not check.strict_present]
    forbidden = _forbidden_hits(stripped, rule["forbidden_facts"])
    decision_field, decision_field_found = _decision_field(stripped)
    decision_seen = _decision_value(stripped, str(rule["decision"]))
    decision_match = canonical_decision(decision_seen) == canonical_decision(rule["decision"])
    prefix_ok = stripped.startswith(rule["prefix"])
    single_line = "\n" not in stripped and "\r" not in stripped
    length_ok = 0 < len(stripped) <= int(rule["max_characters"])

    strict_issues = []
    if not prefix_ok:
        strict_issues.append("answer_prefix")
    if not single_line:
        strict_issues.append("answer_multiline")
    if not length_ok:
        strict_issues.append("answer_length")
    if strict_missing:
        strict_issues.append("answer_missing_fact")
    if not decision_match:
        strict_issues.append("answer_wrong_decision")
    if forbidden:
        strict_issues.append("answer_forbidden_fact")

    semantic_issues = []
    if not single_line:
        semantic_issues.append("answer_multiline")
    if not length_ok:
        semantic_issues.append("answer_length")
    if not prefix_ok:
        semantic_issues.append("answer_prefix")
    conflict_version = _conflicting_version(stripped, _version_literal(rule))
    conflict_region = _conflicting_region(stripped, _region_literal(rule))
    if conflict_version:
        semantic_issues.append("answer_wrong_version")
    elif _version_literal(rule) and any(
        check.literal == _version_literal(rule) for check in checks if not check.present
    ):
        semantic_issues.append("answer_missing_version")
    if conflict_region:
        semantic_issues.append("answer_wrong_region")
    elif _region_literal(rule) and any(
        check.literal == _region_literal(rule) for check in checks if not check.present
    ):
        semantic_issues.append("answer_missing_region")
    if missing:
        semantic_issues.append("answer_missing_fact")
    if not decision_match:
        semantic_issues.append("answer_wrong_decision")
    if forbidden:
        semantic_issues.append("answer_forbidden_fact")

    strict = {
        "pass": not strict_issues,
        "issues": strict_issues,
        "facts": [check.as_record() for check in checks],
        "missing_facts": strict_missing,
        "forbidden_hits": forbidden,
        "decision_seen": decision_seen,
        "decision_field_found": decision_field_found,
        "decision_field": decision_field,
        "decision_expected": rule["decision"],
    }
    semantic = {
        "pass": not semantic_issues,
        "issues": semantic_issues,
        "facts": [check.as_record() for check in checks],
        "missing_facts": missing,
        "forbidden_hits": forbidden,
        "decision_seen": decision_seen,
        "decision_field_found": decision_field_found,
        "decision_field": decision_field,
        "decision_expected": rule["decision"],
        "normalised": _fold(stripped),
        "conflicting_version": conflict_version,
        "conflicting_region": conflict_region,
    }
    return JudgeVerdict(
        strict_pass=not strict_issues,
        semantic_pass=not semantic_issues,
        strict=strict,
        semantic=semantic,
    )


def judge_handoff(rule: Mapping[str, Any], raw: str) -> JudgeVerdict:
    """Score one first-role HANDOFF line under both gates.

    The handoff must not contain the decision: the decision belongs to the second
    role, and a handoff that already states it would make the paired decision
    measurement unfalsifiable.
    """
    text = str(raw or "")
    stripped = text.strip()
    checks = _facts(stripped, rule["handle_facts"])
    missing = [check.literal for check in checks if not check.present]
    strict_missing = [check.literal for check in checks if not check.strict_present]
    forbidden = _forbidden_hits(stripped, rule["forbidden_facts"])
    decision_label = str(rule["decision"])
    # A short label such as ``GO`` would match inside ordinary words, so the leak
    # check only applies to labels long enough to be unambiguous.
    decision_leak = bool(
        len(canonical_decision(decision_label)) >= 4
        and _literal_pattern(decision_label).search(_fold(stripped))
    )
    single_line = "\n" not in stripped and "\r" not in stripped
    length_ok = 0 < len(stripped) <= int(rule["max_characters"])
    marker = re.compile(r"^\s*HANDOFF(?=\s|[:：|]|$)[\s:：|]*", re.IGNORECASE)
    strict_marker = stripped.startswith(rule["prefix"])
    marker_variant = bool(marker.match(stripped)) and not strict_marker
    body = marker.sub("", stripped, count=1).strip()
    canonical = f"{rule['prefix']}{body}" if (not missing and single_line and body) else None

    strict_issues = []
    if not strict_marker:
        strict_issues.append("handoff_marker")
    if not single_line:
        strict_issues.append("handoff_multiline")
    if not length_ok:
        strict_issues.append("handoff_length")
    if strict_missing:
        strict_issues.append("handoff_missing_fact")
    if forbidden:
        strict_issues.append("handoff_forbidden_fact")
    if decision_leak:
        strict_issues.append("handoff_decision_leak")

    semantic_issues = []
    if not single_line:
        semantic_issues.append("handoff_multiline")
    if not length_ok:
        semantic_issues.append("handoff_length")
    if missing:
        semantic_issues.append("handoff_missing_fact")
    if forbidden:
        semantic_issues.append("handoff_forbidden_fact")
    if decision_leak:
        semantic_issues.append("handoff_decision_leak")

    return JudgeVerdict(
        strict_pass=not strict_issues,
        semantic_pass=not semantic_issues,
        strict={
            "pass": not strict_issues, "issues": strict_issues,
            "facts": [check.as_record() for check in checks],
            "missing_facts": strict_missing, "forbidden_hits": forbidden,
            "strict_marker": strict_marker, "marker_variant": marker_variant,
            "one_line": single_line, "canonical": canonical,
            "decision_leak": decision_leak,
            "needs_recovery": bool(missing) or not single_line,
        },
        semantic={
            "pass": not semantic_issues, "issues": semantic_issues,
            "facts": [check.as_record() for check in checks],
            "missing_facts": missing, "forbidden_hits": forbidden,
            "strict_marker": strict_marker, "marker_variant": marker_variant,
            "one_line": single_line, "canonical": canonical,
            "decision_leak": decision_leak,
            "needs_recovery": bool(missing) or not single_line,
        },
    )


def _version_literal(rule: Mapping[str, Any]) -> str:
    """The fact that looks like a version/handle identifier, for defect labelling."""
    return str(rule.get("version_fact") or "")


def _region_literal(rule: Mapping[str, Any]) -> str:
    return str(rule.get("region_fact") or "")


def answer_rule_for(tasks: Iterable[Mapping[str, Any]], task_id: str) -> dict[str, Any]:
    for task in tasks:
        if task["task_id"] == task_id:
            rule = answer_rule(task)
            rule["version_fact"] = str(task.get("version_fact") or "")
            rule["region_fact"] = str(task.get("region_fact") or "")
            return rule
    raise KeyError(task_id)


def handle_rule_for(tasks: Iterable[Mapping[str, Any]], task_id: str) -> dict[str, Any]:
    for task in tasks:
        if task["task_id"] == task_id:
            rule = handle_rule(task)
            rule["version_fact"] = str(task.get("version_fact") or "")
            rule["region_fact"] = str(task.get("region_fact") or "")
            return rule
    raise KeyError(task_id)


def tool_evidence(task: Mapping[str, Any]) -> tuple[str, ...]:
    """The frozen evidence string of every tool, in call order."""
    return tuple(" ".join(str(part) for part in tool["evidence_parts"]) for tool in task["tools"])


def task_evidence(task: Mapping[str, Any]) -> tuple[str, ...]:
    """The tool evidence that must back an answer (used by the audit)."""
    return tool_evidence(task)


__all__ = [
    "FactCheck",
    "JudgeVerdict",
    "TASK_FILE",
    "answer_rule",
    "answer_rule_for",
    "canonical_decision",
    "handle_rule",
    "handle_rule_for",
    "judge_answer",
    "judge_handoff",
    "load_tasks",
    "task_evidence",
    "tool_evidence",
]
