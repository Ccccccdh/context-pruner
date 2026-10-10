"""Registration for the v13 held-out task: `psf__requests-1766` (quote qop in Digest Auth).

Why a new task
--------------
Every paid OpenAI Agents measurement so far ran on one Django public-source diagnostic task
that took part in tuning v1-v4.  The prescreen showed that candidate-unit counts do not
predict an acceptable benefit, so the only honest next step is a **held-out task**: a
different repository, never used by this host (v1-v12) and never referenced by the other two
hosts in this repository.

The task
--------
Public issue: `psf__requests-1766` - "quote qop options in Digest Auth".  RFC2617 requires
the `qop-options` directive to be a quoted string, and the baseline builds the Digest
Authorization header as `qop=auth` (an unquoted value) in
`requests/auth.py::HTTPDigestAuth.build_digest_header`.

Three read-only views of the **public baseline source at the pinned base commit**:

* V1 `requests/auth.py` lines 58-149 - the Digest auth class and the header builder;
* V2 `requests/models.py` lines 451-472 - where an auth handler is applied to a prepared
  request;
* V3 `requests/sessions.py` lines 232-270 - where request and session auth are merged.

The registered literal labels below are the phrases a reduction must never remove; they all
occur in the baseline views (verified by :func:`verify`), so the freeze cannot register a
phrase that only exists after the fix.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / ".tooling/upstream/psf-requests-1766"
INSTANCE_FILE = ROOT / ".tooling/swebench-verified/psf-requests-1766/instance.json"

REGISTRY_SCHEMA = "openai_agents_requests_task_registry_v13"
TASK_ID = "requests_digest_qop"
ISSUE_ID = "psf__requests-1766"
REPO = "psf/requests"
BASE_COMMIT = "847735553aeda6e6633f2b32e14ba14ba86887a4"

#: (repo, relative path, first line, last line) of each read-only view, baseline source only.
SOURCES: dict[str, tuple[tuple[str, str, int, int], ...]] = {
    TASK_ID: (
        (REPO, "requests/auth.py", 58, 149),
        (REPO, "requests/models.py", 451, 472),
        (REPO, "requests/sessions.py", 232, 270),
    ),
}

#: Tool names, in the order the frozen protocol requests them (one call per turn).
TOOL_NAMES = (
    "read_requests_digest_auth",
    "read_requests_prepare_auth",
    "read_requests_session_auth",
)

#: Phrases a reduction must never remove.  Every phrase is checked against the baseline
#: views by :func:`verify`, so none of them can come from the reference fix.  The last entry
#: is the *statement-side* literal: the issue text itself names the directive, so the guard
#: has a statement unit to protect (the source-side literals live in the tool views).
LITERAL_LABELS: dict[str, tuple[str, ...]] = {
    "digest_qop_header": ("qop=auth",),
    "digest_header_builder": ("def build_digest_header",),
    "digest_challenge": ("qop = self.chal.get('qop')",),
    "auth_apply_step": ("def prepare_auth",),
    "session_auth_merge": ("auth=merge_setting(auth, self.auth)",),
    "issue_qop_options_directive": ("qop-options",),
}

#: Required literals of the frozen Track A contract (case-insensitive substring checks).
REQUIRED_TERMS = ("qop", "build_digest_header", "quot")

#: Frozen Track A answer rule: prefix, then the offending parameter inside `cause=`, then
#: the quoting rule inside `fix=`.  Applied with ``re.fullmatch(..., re.IGNORECASE)``.
ANSWER_PATTERN = r"RESULT issue=requests-1766 cause=.*qop.* fix=.*quot.*"

#: The contract the instructions state verbatim.
FINAL_CONTRACT = (
    "RESULT issue=requests-1766 cause=<offending header parameter and why it is rejected> "
    "fix=<the exact quoting rule>"
)

#: Maximum answer length, stated in the instructions and enforced by Track A.
MAX_ANSWER_CHARS = 160

#: Track B (semantic) limits and forbidden claims; the rule itself lives in the protocol.
TRACK_B_MAX_CHARS = 400
TRACK_B_FORBIDDEN = ("no bug", "not a bug")

REQUEST = (
    "Diagnose why a server that enforces RFC2617 rejects the Digest Authorization header "
    "produced here. Read the digest header builder, the prepared-request auth step and the "
    "session auth merge in order; name the function that emits the offending parameter and "
    "state the exact quoting rule the fix must apply. Issue: psf__requests-1766."
)

#: The two-round read protocol, frozen verbatim.  Reading each view twice is what makes the
#: payload contain byte-identical duplicate tool outputs for the duplicate-output mechanism.
PROTOCOL_STEPS: tuple[str, ...] = (
    "STEP 1: call read_requests_digest_auth alone, then stop and wait for its result.",
    "STEP 2: after step 1 returns, call read_requests_prepare_auth alone, then stop and wait.",
    "STEP 3: after step 2 returns, call read_requests_session_auth alone, then stop and wait.",
    "STEP 4: after step 3 returns, call read_requests_digest_auth once more alone, then stop and wait.",
    "STEP 5: after step 4 returns, call read_requests_prepare_auth once more alone, then stop and wait.",
    "STEP 6: after step 5 returns, call read_requests_session_auth once more alone, then stop and wait.",
    "STEP 7: only after all six reads have returned, state the final diagnosis.",
)

#: Two reads of the three views, then one answer: seven model calls.
EXPECTED_MODEL_CALLS = 7


def sources(task: str = TASK_ID) -> tuple[tuple[str, str, int, int], ...]:
    return SOURCES[str(task)]


def source_path(entry: tuple[str, str, int, int]) -> Path:
    _, relative, _, _ = entry
    return SOURCE_ROOT / relative


def source_view(index: int, task: str = TASK_ID) -> str:
    """One registered view, exactly as the tool returns it: header plus numbered lines."""
    repo, relative, first, last = sources(task)[index]
    lines = source_path(sources(task)[index]).read_text(encoding="utf-8").splitlines()
    end = min(last, len(lines))
    body = "\n".join(f"{number}: {lines[number - 1]}" for number in range(first, end + 1))
    return f"{repo}/{relative} (baseline)\n{body}"


def view_texts(task: str = TASK_ID) -> list[str]:
    return [source_view(index, task) for index in range(len(sources(task)))]


def statement(task: str = TASK_ID) -> str:
    """The registered task statement: public issue, request and read protocol."""
    instance = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
    return "\n".join(
        [str(instance["problem_statement"]).strip(), REQUEST, "\n".join(PROTOCOL_STEPS)]
    )


def history(task: str = TASK_ID) -> list[dict]:
    """The three message units the model receives, in order."""
    instance = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
    return [
        {"role": "user", "content": f"Public issue {ISSUE_ID}:\n{instance['problem_statement']}"},
        {"role": "user", "content": REQUEST},
        {"role": "user", "content": "\n".join(PROTOCOL_STEPS)},
    ]


def literal_phrases() -> dict[str, tuple[str, ...]]:
    return {label: tuple(values) for label, values in LITERAL_LABELS.items()}


def constraint_presence(text: str) -> dict[str, bool]:
    lowered = str(text).lower()
    return {
        label: any(phrase.lower() in lowered for phrase in phrases)
        for label, phrases in LITERAL_LABELS.items()
    }


def input_hashes() -> dict[str, str]:
    """SHA256 of every acquired file this registration rests on."""
    paths = [INSTANCE_FILE]
    paths.extend(source_path(entry) for entry in sources())
    paths.append(ROOT / ".tooling/upstream/psf-requests-1766/test_requests.py")
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }


def registry_fingerprint(task: str = TASK_ID) -> str:
    payload = {
        "schema": REGISTRY_SCHEMA,
        "task": str(task),
        "issue": ISSUE_ID,
        "repo": REPO,
        "base_commit": BASE_COMMIT,
        "sources": [list(entry) for entry in sources(task)],
        "tool_names": list(TOOL_NAMES),
        "literal_labels": {label: list(values) for label, values in LITERAL_LABELS.items()},
        "required_terms": list(REQUIRED_TERMS),
        "answer_pattern": ANSWER_PATTERN,
        "final_contract": FINAL_CONTRACT,
        "max_answer_chars": MAX_ANSWER_CHARS,
        "request": REQUEST,
        "protocol_steps": list(PROTOCOL_STEPS),
        "expected_model_calls": EXPECTED_MODEL_CALLS,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def ftp_consistency() -> dict[str, Any]:
    """Stage 0 check: does the base tree match the candidate's FAIL_TO_PASS metadata?

    The evaluation test patch is deliberately not read.  What can be checked from the public
    baseline tree alone is whether each named test already exists there: a test that exists
    must keep passing (regression entries), and the one the issue is about is expected to be
    absent because it arrives with that patch.  The base defect itself is checked separately
    by the quoted/unquoted emission check below.
    """
    instance = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
    tests = (ROOT / ".tooling/upstream/psf-requests-1766/test_requests.py").read_text(
        encoding="utf-8"
    )
    present, absent = [], []
    for entry in instance["FAIL_TO_PASS"]:
        name = str(entry).split("::")[-1]
        (present if f"def {name}(" in tests else absent).append(name)
    return {
        "fail_to_pass_entries": list(instance["FAIL_TO_PASS"]),
        "present_in_base_tree": sorted(present),
        "absent_from_base_tree": sorted(absent),
        "expected_absent": ["test_DIGESTAUTH_QUOTES_QOP_VALUE"],
        "reading": (
            "the quoting test is the one the evaluation test patch adds; the other entries "
            "already exist in the base tree and are regression entries. The base tree is the "
            "pinned commit, the test patch is not read, and the defect is established from "
            "the source itself."
        ),
    }


def defect_evidence() -> dict[str, Any]:
    """The base-state defect, taken from the registered views only."""
    views = "\n".join(view_texts())
    return {
        "unquoted_emission_present": "qop=auth, nc=" in views,
        "quoted_emission_present": 'qop="auth"' in views,
        "emitting_line": next(
            (
                line.strip()
                for line in views.splitlines()
                if "qop=auth, nc=" in line
            ),
            "",
        ),
        "reading": (
            "the baseline builds the Digest header with an unquoted qop value, which is the "
            "state the frozen Track A contract is about; a quoted emission would mean the "
            "fix is already present and the task would be void"
        ),
    }


def verify(task: str = TASK_ID) -> list[str]:
    """Registration problems (empty list means verified)."""
    problems: list[str] = []
    if not INSTANCE_FILE.is_file():
        problems.append(f"{task}: the acquired instance record is missing")
    for index, entry in enumerate(sources(task)):
        repo, relative, first, last = entry
        path = source_path(entry)
        if not path.is_file():
            problems.append(f"{task}/view{index + 1}: baseline source is missing: {relative}")
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if last > len(lines) or first < 1:
            problems.append(f"{task}/view{index + 1}: line range {first}-{last} is out of range")
        view = source_view(index, task)
        if repo not in view or f"{first}: " not in view:
            problems.append(f"{task}/view{index + 1}: the view is not numbered as registered")
    views = "\n".join(view_texts(task))
    statement_text = statement(task)
    for label, phrases in LITERAL_LABELS.items():
        for phrase in phrases:
            if phrase not in views and phrase not in statement_text:
                problems.append(
                    f"{task}: registered literal {label} is neither in the baseline views nor "
                    f"in the registered statement: {phrase!r}"
                )
    statement_text = statement(task)
    # Every required term must be reachable by the model: the source identifiers come from
    # the views, the remedy word comes from the public issue text.
    reachable = f"{statement_text}\n{views}".lower()
    for term in REQUIRED_TERMS:
        if term.lower() not in reachable:
            problems.append(f"{task}: required term {term!r} is not reachable by the model")
    for term in ("qop", "build_digest_header"):
        if term.lower() not in views.lower():
            problems.append(f"{task}: cause evidence {term!r} is not in the baseline views")
    if PROTOCOL_STEPS[-1] not in statement_text:
        problems.append(f"{task}: the registered statement is missing the read protocol")
    if REQUEST not in statement_text:
        problems.append(f"{task}: the registered statement is missing the request")
    instance = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
    if instance["base_commit"] != BASE_COMMIT:
        problems.append(f"{task}: the acquired record is not the pinned base commit")
    evidence = defect_evidence()
    if not evidence["unquoted_emission_present"]:
        problems.append(f"{task}: the unquoted qop emission is not visible in the views")
    if evidence["quoted_emission_present"]:
        problems.append(f"{task}: the baseline views already contain the fixed quoted form")
    consistency = ftp_consistency()
    if consistency["absent_from_base_tree"] != consistency["expected_absent"]:
        problems.append(
            f"{task}: FAIL_TO_PASS/base-tree split differs from the registered expectation: "
            f"{consistency['absent_from_base_tree']}"
        )
    return problems


def manifest_block() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "task": TASK_ID,
        "issue": ISSUE_ID,
        "repo": REPO,
        "base_commit": BASE_COMMIT,
        "tool_names": list(TOOL_NAMES),
        "views": [
            {"file": entry[1], "first_line": entry[2], "last_line": entry[3]}
            for entry in sources()
        ],
        "literal_labels": {label: list(values) for label, values in LITERAL_LABELS.items()},
        "required_terms": list(REQUIRED_TERMS),
        "answer_pattern": ANSWER_PATTERN,
        "final_contract": FINAL_CONTRACT,
        "max_answer_chars": MAX_ANSWER_CHARS,
        "track_b_max_chars": TRACK_B_MAX_CHARS,
        "request": REQUEST,
        "protocol_steps": list(PROTOCOL_STEPS),
        "expected_model_calls": EXPECTED_MODEL_CALLS,
        "fingerprint": registry_fingerprint(),
        "input_hashes": input_hashes(),
    }


__all__ = [
    "ANSWER_PATTERN",
    "BASE_COMMIT",
    "EXPECTED_MODEL_CALLS",
    "FINAL_CONTRACT",
    "INSTANCE_FILE",
    "ISSUE_ID",
    "LITERAL_LABELS",
    "MAX_ANSWER_CHARS",
    "PROTOCOL_STEPS",
    "REGISTRY_SCHEMA",
    "REPO",
    "REQUEST",
    "REQUIRED_TERMS",
    "SOURCES",
    "SOURCE_ROOT",
    "TASK_ID",
    "TOOL_NAMES",
    "TRACK_B_FORBIDDEN",
    "TRACK_B_MAX_CHARS",
    "constraint_presence",
    "history",
    "input_hashes",
    "literal_phrases",
    "manifest_block",
    "registry_fingerprint",
    "source_path",
    "source_view",
    "sources",
    "statement",
    "verify",
    "view_texts",
]
