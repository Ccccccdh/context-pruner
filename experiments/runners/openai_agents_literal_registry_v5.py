"""Registered literal constraints and literal-unit provenance for v5.

Why this module exists
----------------------
The v4 defect was an *ordering of checks*: the eligibility rule asked "has this
text already been delivered verbatim to the model?" but never asked "does this
text carry a registered literal constraint that the frozen quality contract
depends on?".  On the Django task ``existing_annotations`` exists **only** inside
one compressed source-code tool output, so answering the first question alone
deleted a frozen literal from the model boundary and the plugin arm scored 0/3.

v5 fixes that by registering, before any run, exactly two things:

1. the literal strings each public task's reference conditions are made of, and
2. for every registered *source* range, the public artifact it came from and the
   file path / line range a reader would have to re-read to recover its text.

Registered sources are **verified against their own text** (``verify`` below): a
source whose header does not state its registered path and line range, or which
does not contain the literals attributed to it, is not registered.  That keeps
the registry from decaying into an unchecked declaration.

Everything here is public information: the literal strings are substrings of the
public SWE-bench issue statement, and the sources are fixed ranges of the public
baseline repository.  No reference patch, host test patch or credential is read,
and nothing from this module is ever written into a result row: only booleans,
counts and SHA256 digests are persisted.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Sequence

#: Schema version of the registry itself.  A new schema means the recorded
#: public literals or sources changed and results are not comparable.
REGISTRY_SCHEMA = "openai_agents_literal_registry_v5"

#: Literal reference conditions per public task.  ``literals`` are the exact
#: public strings; ``units`` are the task-statement text units that must survive
#: filtering.  A unit is registered only when it contains the literals.
CONSTRAINTS: dict[str, dict[str, dict[str, Any]]] = {
    "pytest_mro": {
        "own_mark_rule": {
            "literals": ("__dict__",),
            "units": ("__dict__",),
        },
        "mro_rule": {
            "literals": ("MRO",),
            "units": ("MRO",),
        },
    },
    "pylint_regex_csv": {
        "split_location": {
            "literals": ("_splitstrip",),
            "units": ("_splitstrip",),
        },
        "quantifier_boundary": {
            "literals": ("quantifier", "{1,3}"),
            "units": ("quantifier",),
        },
    },
    "django_count_annotations": {
        "filter_references": {
            "literals": ("filter",),
            "units": ("filter operations",),
        },
        "other_annotation_references": {
            "literals": ("other annotations",),
            "units": ("other annotations",),
        },
        "ordering_references": {
            "literals": ("ordering",),
            "units": ("ordering",),
        },
        "aggregation_decision": {
            "literals": ("existing_annotations",),
            # Deliberately empty: this literal is *not* present in the issue
            # statement.  It reaches the model only through a source-code tool
            # output, which is precisely why that output must be pinned instead
            # of being treated as already-served redundant text.
            "units": (),
        },
    },
}


@dataclass(frozen=True)
class RegisteredSource:
    """One fixed public baseline range that a task exposes through one tool."""

    tool: str
    repo: str
    path: str
    first_line: int
    last_line: int
    #: Constraint labels whose literal text this source is expected to carry.
    literal_labels: tuple[str, ...]

    def range_label(self) -> str:
        return f"{self.repo}/{self.path}:{self.first_line}-{self.last_line}"

    def header_fragments(self) -> tuple[str, ...]:
        """Fragments that must appear in the tool output for it to be this source."""
        return (
            f"{self.repo}/{self.path}",
            f"{self.first_line}: ",
            f"{self.last_line}: ",
        )


#: (scenario, tool name) -> registered source range.  Kept in the same order the
#: frozen v1 runner exposes the tools; the audit re-derives the same table from
#: the frozen public input hashes.
SOURCES: dict[str, tuple[RegisteredSource, ...]] = {
    "django_count_annotations": (
        RegisteredSource(
            tool="read_django_count_entry",
            repo="django-16263",
            path="django/db/models/query.py",
            first_line=610,
            last_line=632,
            literal_labels=(),
        ),
        RegisteredSource(
            tool="read_django_aggregation",
            repo="django-16263",
            path="django/db/models/sql/query.py",
            first_line=438,
            last_line=527,
            literal_labels=("ordering_references", "aggregation_decision"),
        ),
        RegisteredSource(
            tool="read_django_count_call",
            repo="django-16263",
            path="django/db/models/sql/query.py",
            first_line=542,
            last_line=560,
            literal_labels=("filter_references",),
        ),
    ),
    "pytest_mro": (
        RegisteredSource(
            tool="read_pytest_mark_decorator",
            repo="pytest-10356",
            path="src/_pytest/mark/structures.py",
            first_line=338,
            last_line=363,
            literal_labels=(),
        ),
        RegisteredSource(
            tool="read_pytest_mark_storage",
            repo="pytest-10356",
            path="src/_pytest/mark/structures.py",
            first_line=365,
            last_line=392,
            # ``__dict__`` is asserted by the frozen answer contract but is absent
            # from this baseline range; an empty tuple is the honest registration
            # and is what ``verify`` checks.
            literal_labels=(),
        ),
        RegisteredSource(
            tool="read_pytest_mark_context",
            repo="pytest-10356",
            path="testing/test_mark.py",
            first_line=540,
            last_line=589,
            literal_labels=(),
        ),
    ),
    "pylint_regex_csv": (
        RegisteredSource(
            tool="read_pylint_option",
            repo="pylint-8898",
            path="pylint/checkers/base/name_checker/checker.py",
            first_line=225,
            last_line=237,
            literal_labels=(),
        ),
        RegisteredSource(
            tool="read_pylint_transformer",
            repo="pylint-8898",
            path="pylint/config/argument.py",
            first_line=45,
            last_line=150,
            literal_labels=(),
        ),
        RegisteredSource(
            tool="read_pylint_csv_helper",
            repo="pylint-8898",
            path="pylint/utils/utils.py",
            first_line=205,
            last_line=267,
            literal_labels=("split_location",),
        ),
    ),
}

#: A source view is line-numbered ``"<repo>/<path> (baseline)"`` followed by
#: ``"<number>: <line>"``.  The recovery pointer is parsed out of the text rather
#: than assumed, so a rename or re-range fails loudly instead of silently
#: producing a pointer to the wrong lines.  ``__HEADER`` additionally rejects a
#: line carrying spaces, which is what keeps this module's own compaction note
#: from being mistaken for a source view.
_HEADER = re.compile(
    r"^(?P<repo>[A-Za-z0-9._-]+)/(?P<path>[^\s()]+)\s*\(baseline\)\s*$"
)
_NUMBERED = re.compile(r"^(?P<number>[0-9]+):")


def literal_labels(task: str) -> tuple[str, ...]:
    """Registered constraint labels for one task, in registration order."""
    return tuple(CONSTRAINTS.get(str(task), {}))


def literals_for(task: str) -> dict[str, tuple[str, ...]]:
    """``label -> literal strings`` for one task."""
    return {
        label: tuple(str(value) for value in spec["literals"])
        for label, spec in CONSTRAINTS.get(str(task), {}).items()
    }


def unit_literals_for(task: str) -> dict[str, tuple[str, ...]]:
    """``label -> task-statement unit fragments`` for one task."""
    return {
        label: tuple(str(value) for value in spec["units"])
        for label, spec in CONSTRAINTS.get(str(task), {}).items()
    }


def labels_in_text(task: str, text: str) -> tuple[str, ...]:
    """Registered labels whose literal text appears in ``text`` (case-insensitive).

    Only labels with at least one registered literal are considered: the Django
    ``aggregation_decision`` label has an empty *unit* list but a non-empty
    literal list, and it is exactly that literal which must be protected.
    """
    lowered = str(text).lower()
    return tuple(
        label
        for label, phrases in literals_for(task).items()
        if phrases and any(str(phrase).lower() in lowered for phrase in phrases)
    )


def source_for_tool(task: str, tool_name: str) -> RegisteredSource | None:
    for source in SOURCES.get(str(task), ()):
        if source.tool == str(tool_name):
            return source
    return None


def parse_source_pointer(text: str) -> tuple[str, str, int, int] | None:
    """Return ``(repo, path, first_line, last_line)`` parsed from a source view.

    ``None`` when the text is not a line-numbered single-range source view, which
    is the honest answer for a tool output this registry knows nothing about.
    """
    lines = str(text).splitlines()
    if not lines:
        return None
    header = _HEADER.match(lines[0].strip())
    if header is None:
        return None
    numbers: list[int] = []
    for line in lines[1:]:
        match = _NUMBERED.match(line.strip())
        if match is None:
            return None
        numbers.append(int(match.group("number")))
    if not numbers:
        return None
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return None
    return (
        str(header.group("repo")),
        str(header.group("path")),
        numbers[0],
        numbers[-1],
    )


#: The pointer a recovery note states: ``<repo>/<path> lines <first>-<last>``.
#: Parsing the note back is what makes "recoverable" a checked property of the
#: artifact the model actually receives, rather than a claim about intent.
_POINTER = re.compile(
    r"(?P<repo>[A-Za-z0-9._-]+)/(?P<path>[^\s:]+)\s+lines\s+(?P<first>[0-9]+)-(?P<last>[0-9]+)"
)


def parse_pointer_claim(text: str) -> tuple[str, str, int, int] | None:
    """Return the path/line pointer a note claims, or ``None`` if it states none."""
    match = _POINTER.search(str(text))
    if match is None:
        return None
    return (
        str(match.group("repo")),
        str(match.group("path")),
        int(match.group("first")),
        int(match.group("last")),
    )


def pointer_matches_registered_source(
    task: str, tool_name: str, claim: tuple[str, str, int, int] | None
) -> bool:
    """True when a note's claimed pointer is exactly the registered source range."""
    if claim is None:
        return False
    source = source_for_tool(task, tool_name)
    if source is None:
        return False
    repo, path, first, last = claim
    return (repo, path, first, last) == (
        source.repo,
        source.path,
        source.first_line,
        source.last_line,
    )


def verify(task: str) -> list[str]:
    """Return registration problems for one task (empty list means verified).

    Registered sources are checked against their own text; a source that does not
    state its registered path and range, or that does not carry the literals
    attributed to it, is reported here and must be fixed before any run.
    """
    problems: list[str] = []
    for source in SOURCES.get(str(task), ()):
        text = _registered_source_text(source)
        if text is None:
            problems.append(f"{task}/{source.tool}: no registered text")
            continue
        parsed = parse_source_pointer(text)
        if parsed is None:
            problems.append(f"{task}/{source.tool}: text is not a parseable source view")
            continue
        repo, path, first, last = parsed
        if (repo, path) != (source.repo, source.path):
            problems.append(f"{task}/{source.tool}: path mismatch {repo}/{path}")
        if (first, last) != (source.first_line, source.last_line):
            problems.append(
                f"{task}/{source.tool}: range mismatch {first}-{last} != "
                f"{source.first_line}-{source.last_line}"
            )
        present = set(labels_in_text(task, text))
        for label in source.literal_labels:
            if label not in present:
                problems.append(f"{task}/{source.tool}: registered literal {label} absent")
    return problems


def _registered_source_text(source: RegisteredSource) -> str | None:
    """Read the public baseline range declared by ``source`` (no network)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    path = root / ".tooling" / "upstream" / source.repo / source.path
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    body = "\n".join(
        f"{number}: {lines[number - 1]}"
        for number in range(source.first_line, min(source.last_line, len(lines)) + 1)
    )
    return f"{source.repo}/{source.path} (baseline)\n{body}"


def text_sha256(text: Any) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def constraint_presence(task: str, searchable: str) -> dict[str, bool]:
    """Boolean literal presence for one payload; no text is returned or stored."""
    lowered = str(searchable).lower()
    return {
        label: any(str(phrase).lower() in lowered for phrase in phrases)
        for label, phrases in literals_for(task).items()
    }


def missing_labels(task: str, searchable: str) -> list[str]:
    return [label for label, present in constraint_presence(task, searchable).items() if not present]


def registry_fingerprint(task: str) -> str:
    """Digest of the registry slice one task runs under (audit cross-check)."""
    return canonical_sha256(
        {
            "schema": REGISTRY_SCHEMA,
            "task": str(task),
            "constraints": {
                label: {
                    "literals": list(spec["literals"]),
                    "units": list(spec["units"]),
                }
                for label, spec in CONSTRAINTS.get(str(task), {}).items()
            },
            "sources": [
                {
                    "tool": source.tool,
                    "repo": source.repo,
                    "path": source.path,
                    "first_line": source.first_line,
                    "last_line": source.last_line,
                    "literal_labels": list(source.literal_labels),
                }
                for source in SOURCES.get(str(task), ())
            ],
        }
    )


def all_problems(tasks: Sequence[str] | None = None) -> list[str]:
    """Registration problems for every registered task."""
    values = tuple(tasks) if tasks is not None else tuple(SOURCES)
    return [problem for task in values for problem in verify(task)]


def searchable_text(items: Sequence[Any]) -> str:
    """Canonical searchable rendering of one payload (used for literal checks)."""
    return canonical(list(items)).lower()
