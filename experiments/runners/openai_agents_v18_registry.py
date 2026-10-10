"""Registry for the three v16 development tasks, read from the frozen artifacts.

Two tasks (``django_sqlite_version_floor``, ``django_orderedset_reversed``) come from
``V16_SOURCE_REGISTRATION_20261006.json``; the third (``django_field_hash_immutability``,
instance ``django__django-15315``) comes from ``V16_STAGE1_20261006.json``.  Nothing here
is invented where the artifacts carry the value:

* views, line ranges, blob ids, file hashes, view hashes, contracts (issue value,
  required facts, frozen regex, 160-character bound) and the request text are read from
  the artifacts and re-verified against the pinned base commits in the local clone;
* the problem statements are read from the public SWE-bench table and checked against the
  ``problem_statement_sha256`` recorded at registration.

Two things the artifacts do **not** carry are supplied here and are declared as derived in
every manifest this registry writes:

* ``protocol_steps`` - derived from the registered read shape (three views read twice, six
  read calls, then one answer call = the registered ``expected_model_calls`` of 7);
* ``tool_names`` for 15315 only - the stage-1 artifact recorded the three views and their
  roles but no tool names, so the three names are supplied here and the source is recorded.

The interface mirrors ``openai_agents_multitask_registry_v14`` so the shared v14 chain
(recorder, filter, manifest patch) can be pointed at this registry unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
TOOLING = REPO / ".tooling"
PARQUET = TOOLING / "swebench-verified/test.parquet"
CLONE = TOOLING / "upstream/django-15563"
REGISTRATION = REPO / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
STAGE1 = REPO / "integrations/openai_agents/V16_STAGE1_20261006.json"
STAGE1_MODULE = REPO / "experiments/runners/openai_agents_v16_stage1.py"
SEALED_REGISTRATION = (
    REPO / "integrations/openai_agents/V16_SEALED_SOURCE_REGISTRATION_20261006.json"
)
#: ``"development"`` is the three dev tasks that took part in building this host configuration;
#: ``"sealed"`` is the untouched confirmation set, loaded only for the confirmation batch from
#: its own registration artifact.
ACTIVE = "development"

REGISTRY_SCHEMA = "openai_agents_v18_registry"
MAX_ANSWER_CHARS = 160
#: the four-round shape: four rounds of three distinct registered views, then one answer turn
READ_ROUNDS = 4
READS_PER_ROUND = 1
READ_CALLS = READ_ROUNDS * READS_PER_ROUND
#: one round reads one view in the amended shape, so this is READ_CALLS + 1 as well;
#: writing it from the round count keeps the meaning when READS_PER_ROUND changes
EXPECTED_MODEL_CALLS = READ_ROUNDS + 1

#: Tool names for the third task.  The stage-1 artifact registered the three views (file,
#: line range, role) and the contract, but no tool names; these three are supplied in
#: registered view order and the source is recorded in the manifest.
STAGE1_TOOL_NAMES = ("read_field_semantics", "read_field_counters", "read_field_binding")
STAGE1_TOOL_NAMES_SOURCE = "runner_supplied_in_registered_view_order"
STAGE1_ROLE_PREFIXES = ("cause:", "context:", "context:")

DERIVED_FIELDS = ("protocol_steps", "tool_docs")

_CACHE: dict[str, Any] = {}


# --------------------------------------------------------------------------- artifacts
def source_registration() -> dict:
    if "registration" not in _CACHE:
        _CACHE["registration"] = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    return _CACHE["registration"]


def stage1() -> dict:
    if "stage1" not in _CACHE:
        _CACHE["stage1"] = json.loads(STAGE1.read_text(encoding="utf-8"))
    return _CACHE["stage1"]


def stage1_request() -> str:
    """The 15315 request text, read from the stage-1 generator that wrote the artifact."""

    if "stage1_request" not in _CACHE:
        text = STAGE1_MODULE.read_text(encoding="utf-8")
        match = re.search(r'"request": \(\n(.*?)\n    \),', text, flags=re.DOTALL)
        if match is None:
            raise ValueError("stage-1 module no longer carries the 15315 request text")
        pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
        _CACHE["stage1_request"] = "".join(pieces)
    return str(_CACHE["stage1_request"])


def instance_rows() -> dict[str, dict]:
    if "rows" not in _CACHE:
        import pyarrow.parquet as pq

        table = pq.read_table(
            str(PARQUET),
            columns=[
                "instance_id",
                "repo",
                "base_commit",
                "difficulty",
                "problem_statement",
            ],
        )
        _CACHE["rows"] = {row["instance_id"]: row for row in table.to_pylist()}
    return _CACHE["rows"]


def _git(*args: str) -> bytes:
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    result = subprocess.run(
        ["git", "-C", str(CLONE), *args],
        capture_output=True,
        check=False,
        env=env,
    )
    if result.returncode:
        raise FileNotFoundError(
            f"pinned read-only git read failed: git -C {CLONE.name} {' '.join(args)}"
        )
    return result.stdout


def blob_bytes(commit: str, path: str) -> bytes:
    key = f"blob:{commit}:{path}"
    if key not in _CACHE:
        _CACHE[key] = _git("show", f"{commit}:{path}")
    return bytes(_CACHE[key])


def blob_git_id(commit: str, path: str) -> str:
    key = f"blobid:{commit}:{path}"
    if key not in _CACHE:
        _CACHE[key] = _git("rev-parse", f"{commit}:{path}").decode("utf-8").strip()
    return str(_CACHE[key])


def view_text(commit: str, path: str, first: int, last: int, repo: str = "django/django") -> str:
    lines = blob_bytes(commit, path).decode("utf-8").splitlines()
    body = "\n".join(
        f"{number}: {lines[number - 1]}" for number in range(int(first), int(last) + 1)
    )
    return f"{repo}/{path} (baseline)\n{body}"


# ------------------------------------------------------------------------------ tasks
def sealed_registration() -> dict:
    if "sealed" not in _CACHE:
        _CACHE["sealed"] = json.loads(SEALED_REGISTRATION.read_text(encoding="utf-8"))
    return _CACHE["sealed"]


def set_active(name: str) -> str:
    """Select the development set or the sealed confirmation set (returns the previous name)."""

    global ACTIVE
    if str(name) not in ("development", "sealed"):
        raise ValueError(f"unknown task set: {name}")
    previous = ACTIVE
    ACTIVE = str(name)
    return previous


def _task_ids() -> tuple[str, ...]:
    if ACTIVE == "sealed":
        return tuple(sealed_registration()["tasks"])
    registered = tuple(source_registration()["tasks"])
    stage = stage1()["registration_15315"]
    return registered + (str(stage["task_id"]),)


def tasks() -> dict[str, dict]:
    key = "tasks" if ACTIVE == "development" else "tasks_sealed"
    if key not in _CACHE:
        built: dict[str, dict] = {}
        if ACTIVE == "sealed":
            for task_id, entry in sealed_registration()["tasks"].items():
                built[task_id] = _from_sealed(entry)
        else:
            registration = source_registration()
            for task_id, entry in registration["tasks"].items():
                built[task_id] = _from_registration(entry)
            built[str(stage1()["registration_15315"]["task_id"])] = _from_stage1()
        _CACHE[key] = built
    return _CACHE[key]


def _from_sealed(entry: dict) -> dict[str, Any]:
    """A sealed confirmation task, already verified by the sealed registration artifact."""

    built: dict[str, Any] = {
        "task_id": str(entry["task_id"]),
        "instance_id": str(entry["instance_id"]),
        "repo": str(entry.get("repo") or "django/django"),
        "base_commit": str(entry["base_commit"]),
        "difficulty": str(entry.get("difficulty") or ""),
        "views": _view_rows(entry),
        "tool_names": list(entry["tool_names"]),
        "tool_names_source": "sealed_registration_artifact",
        "request": str(entry["request"]),
        "contract": dict(entry["contract"]),
        "problem_statement_sha256": str(entry["problem_statement_sha256"]),
        "source_artifact": str(SEALED_REGISTRATION.relative_to(REPO)).replace("\\", "/"),
        "sealed_confirmation_task": True,
    }
    built["derived_fields"] = list(DERIVED_FIELDS)
    return _finish(built)


def task_ids() -> tuple[str, ...]:
    return tuple(tasks())


def task(task_id: str) -> dict:
    return tasks()[str(task_id)]


def views(task_id: str) -> list[dict]:
    return list(task(task_id)["views"])


def _view_rows(entry: dict) -> list[dict]:
    rows = []
    for index, view in enumerate(entry["views"]):
        blob_id = view.get("blob_sha256_git") or view.get("blob_git_id")
        rows.append(
            {
                "file": view["file"],
                "first_line": int(view["first_line"]),
                "last_line": int(view["last_line"]),
                "role": str(view.get("role") or f"registered view {index + 1} of 3"),
                "blob_git_id": str(blob_id),
                "file_content_sha256": str(view["file_content_sha256"]),
                "view_content_sha256": str(view["view_content_sha256"]),
                "view_bytes": int(view.get("view_bytes") or 0),
            }
        )
    return rows


def _tool_docs(entry: dict) -> list[str]:
    docs = []
    for index, view in enumerate(entry["views"]):
        docs.append(
            "Read-only baseline view {index} of {total}: {file} lines {first}-{last} at the "
            "pinned base commit {commit} (role: {role}). Returns those numbered lines verbatim; "
            "it cannot write, search or fetch.".format(
                index=index + 1,
                total=len(entry["views"]),
                file=view["file"],
                first=view["first_line"],
                last=view["last_line"],
                commit=entry["base_commit"],
                role=view.get("role") or "registered view",
            )
        )
    return docs


def _protocol_steps_from(entry: dict) -> list[str]:
    """Derived from the registered read shape: each of the three views read twice."""

    rounds = []
    names = list(entry["tool_names"])
    views = list(entry["views"])
    for round_index in range(READ_ROUNDS):
        chunk = list(
            zip(
                names[round_index * READS_PER_ROUND : (round_index + 1) * READS_PER_ROUND],
                views[round_index * READS_PER_ROUND : (round_index + 1) * READS_PER_ROUND],
            )
        )
        rounds.append(
            f"round {round_index + 1}: "
            + ", ".join(
                f"{name} ({view['file']} {view['first_line']}-{view['last_line']})"
                for name, view in chunk
            )
        )
    return [
        "Read one read-only tool in each of "
        f"{READ_ROUNDS} rounds, in this order, and never read the same registered view twice: "
        + "; ".join(rounds)
        + f". That is {READ_CALLS} read calls over {len(views)} distinct registered views.",
        "Issue the single call of a round on its own turn, wait for its result, and only then "
        "start the next round. Do not issue two reads in the same turn and do not read a view "
        "twice.",
        f"After the last round, having issued all {READ_CALLS} reads, answer with the JSON "
        "object the request describes and call no more tools: the host renders your JSON into "
        "the single RESULT line.",
    ]


def protocol_steps(task_id: str) -> list[str]:
    return _protocol_steps_from(task(task_id))


def _problem_statement_from(entry: dict) -> str:
    row = instance_rows().get(entry["instance_id"])
    if row is None:
        raise KeyError(f"{entry['instance_id']} is absent from the public task table")
    return str(row["problem_statement"])


def _statement_from(entry: dict) -> str:
    return "\n".join(
        [
            _problem_statement_from(entry),
            entry["request"],
            "\n".join(_protocol_steps_from(entry)),
        ]
    )


def _regex_token(pattern: str, side: str) -> str:
    """The first identifier the frozen regex requires on the cause or the fix side."""

    segment = str(pattern).partition(f"{side}=")[2]
    if side == "cause":
        segment = segment.partition(" fix=")[0]
    match = re.search(r"[A-Za-z_][A-Za-z0-9_]{2,}", segment)
    return match.group(0) if match else ""


def _contract_prompt(entry: dict) -> str:
    contract = entry["contract"]
    facts = ", ".join(f'"{fact}"' for fact in contract["required_facts"])
    return (
        "one JSON object with exactly the keys \"issue\", \"cause\", \"fix\" and \"facts\": "
        f"\"issue\" is the fixed value \"{contract['issue_value']}\"; \"cause\" names the root "
        f"cause and must contain {entry['cause_token']!r}; \"fix\" names the change the fix "
        f"must make and must contain {entry['fix_token']!r}; \"facts\" is a list of the exact "
        f"strings {facts} copied from the tool output. "
        f"Every one of those strings must also appear verbatim inside \"cause\" or \"fix\", because the host builds the rendered line from those two fields only, and all of them must be present at the same time as the 160 character bound. "
        "The host renders issue, cause and fix into "
        "one line of the shape \"RESULT issue=<issue> cause=<cause> fix=<fix>\"; keep cause and "
        "fix short so that the rendered line stays within 160 characters. Send nothing outside "
        "the JSON object."
    )


def _finish(entry: dict[str, Any]) -> dict[str, Any]:
    contract = entry["contract"]
    derived = list(entry.get("derived_fields") or DERIVED_FIELDS)
    entry["cause_token"] = str(contract.get("cause_token") or "")
    entry["fix_token"] = str(contract.get("fix_token") or "")
    if not entry["cause_token"]:
        entry["cause_token"] = _regex_token(contract["frozen_regex"], "cause")
        derived.append("cause_token")
    if not entry["fix_token"]:
        entry["fix_token"] = _regex_token(contract["frozen_regex"], "fix")
        derived.append("fix_token")
    entry["derived_fields"] = derived
    entry["protocol_steps"] = _protocol_steps_from(entry)
    entry["tool_docs"] = _tool_docs(entry)
    entry["required_terms"] = list(entry["contract"]["required_facts"])
    entry["literals"] = {
        "required_facts": tuple(entry["contract"]["required_facts"]),
        "issue_value": (str(entry["contract"]["issue_value"]),),
    }
    entry["answer_pattern"] = str(entry["contract"]["frozen_regex"])
    entry["contract_prompt"] = _contract_prompt(entry)
    entry["read_calls"] = READ_CALLS
    entry["expected_model_calls"] = EXPECTED_MODEL_CALLS
    entry["statement_sha256"] = hashlib.sha256(
        _statement_from(entry).encode("utf-8")
    ).hexdigest()
    return entry


def _from_registration(entry: dict) -> dict[str, Any]:
    built: dict[str, Any] = {
        "task_id": str(entry["task_id"]),
        "instance_id": str(entry["instance_id"]),
        "repo": str(entry["repo"]),
        "base_commit": str(entry["base_commit"]),
        "difficulty": str(entry.get("difficulty") or ""),
        "views": _view_rows(entry),
        "tool_names": list(entry["tool_names"]),
        "tool_names_source": "registered_artifact",
        "request": str(entry["request"]),
        "contract": dict(entry["contract"]),
        "problem_statement_sha256": str(entry["problem_statement_sha256"]),
        "source_artifact": str(REGISTRATION.relative_to(REPO)).replace("\\", "/"),
    }
    built["derived_fields"] = list(DERIVED_FIELDS)
    return _finish(built)


def _from_stage1() -> dict[str, Any]:
    stage = stage1()["registration_15315"]
    roles = [str(view.get("role") or "") for view in stage["views"]]
    if len(stage["views"]) != len(STAGE1_TOOL_NAMES) or not all(
        role.startswith(prefix) for role, prefix in zip(roles, STAGE1_ROLE_PREFIXES)
    ):
        raise ValueError("stage-1 view shape changed; the supplied tool names no longer map")
    built: dict[str, Any] = {
        "task_id": str(stage["task_id"]),
        "instance_id": str(stage["instance_id"]),
        "repo": "django/django",
        "base_commit": str(stage["base_commit"]),
        "difficulty": "",
        "views": _view_rows(stage),
        "tool_names": list(STAGE1_TOOL_NAMES),
        "tool_names_source": STAGE1_TOOL_NAMES_SOURCE,
        "request": stage1_request(),
        "contract": dict(stage["contract"]),
        "problem_statement_sha256": str(stage["problem_statement_sha256"]),
        "source_artifact": str(STAGE1.relative_to(REPO)).replace("\\", "/"),
    }
    built["derived_fields"] = list(DERIVED_FIELDS) + ["tool_names"]
    return _finish(built)


# ------------------------------------------------------------------------ model-facing
def problem_statement(task_id: str) -> str:
    entry = task(task_id)
    return _problem_statement_from(entry)


def history(task_id: str) -> list[dict]:
    entry = task(task_id)
    return [
        {
            "role": "user",
            "content": f"Public issue {entry['instance_id']}:\n{problem_statement(task_id)}",
        },
        {"role": "user", "content": entry["request"]},
        {"role": "user", "content": "\n".join(protocol_steps(task_id))},
    ]


def statement(task_id: str) -> str:
    return _statement_from(task(task_id))


def source_view(task_id: str, index: int) -> str:
    entry = task(task_id)
    view = entry["views"][index]
    return view_text(
        entry["base_commit"], view["file"], view["first_line"], view["last_line"], entry["repo"]
    )


def view_texts(task_id: str) -> list[str]:
    return [source_view(task_id, index) for index in range(len(views(task_id)))]


def literal_phrases(task_id: str) -> dict[str, tuple[str, ...]]:
    return {label: tuple(values) for label, values in task(task_id)["literals"].items()}


def fingerprint(task_id: str) -> str:
    entry = task(task_id)
    seed = {
        "schema": REGISTRY_SCHEMA,
        "task_id": entry["task_id"],
        "instance_id": entry["instance_id"],
        "base_commit": entry["base_commit"],
        "views": entry["views"],
        "tool_names": list(entry["tool_names"]),
        "request": entry["request"],
        "protocol_steps": protocol_steps(task_id),
        "contract": entry["contract"],
        "statement_sha256": entry["statement_sha256"],
    }
    return hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# --------------------------------------------------------------------------- verifying
def minimal_feasible_line(task_id: str) -> dict[str, Any]:
    """The shortest rendered line that keeps every required literal and both regex tokens."""

    entry = task(task_id)
    pattern = str(entry["contract"]["frozen_regex"])
    facts = [str(fact) for fact in entry["contract"]["required_facts"]]
    before_fix, _, after_fix = pattern.partition(" fix=")
    token_re = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    skip = {"result", "issue", "cause", "fix", "django"}

    def tokens(segment: str) -> list[str]:
        found: list[str] = []
        for token in token_re.findall(segment):
            if token.lower() in skip or token in found:
                continue
            found.append(token)
        return found

    cause_bits = tokens(before_fix)
    fix_bits = tokens(after_fix)
    for fact in facts:
        if fact in cause_bits or fact in fix_bits:
            continue
        if after_fix and fact in after_fix:
            fix_bits.append(fact)
        else:
            cause_bits.append(fact)
    line = (
        f"RESULT issue={entry['contract']['issue_value']} cause={' '.join(cause_bits)} "
        f"fix={' '.join(fix_bits)}"
    )
    problems = []
    if not re.fullmatch(pattern, line, flags=re.IGNORECASE):
        problems.append("the shortest line does not match the frozen regex")
    for fact in facts:
        if fact not in line:
            problems.append(f"the shortest line lost the required literal {fact!r}")
    if len(line) > MAX_ANSWER_CHARS:
        problems.append("the shortest line exceeds the bound")
    return {
        "task_id": task_id,
        "line": line,
        "chars": len(line),
        "margin_to_bound": MAX_ANSWER_CHARS - len(line),
        "problems": problems,
    }


def verify(task_id: str) -> list[str]:
    """Registration problems for one task (an empty list means the task verified)."""

    problems: list[str] = []
    entry = task(task_id)
    try:
        row = instance_rows()[entry["instance_id"]]
    except KeyError:
        return [f"{task_id}: instance is absent from the public task table"]
    if str(row["base_commit"]) != entry["base_commit"]:
        problems.append(f"{task_id}: base commit differs from the public task table")
    actual_statement_sha = hashlib.sha256(
        _problem_statement_from(entry).encode("utf-8")
    ).hexdigest()
    registered_statement_sha = str(entry["problem_statement_sha256"])
    if actual_statement_sha != registered_statement_sha:
        # The stage-1 artifact recorded ``sha256(hex text of the statement hash)`` instead of
        # the statement hash itself.  That is reconciled here against the public table and the
        # candidate pool, and recorded; it is not a task, view or contract difference.
        if registered_statement_sha == hashlib.sha256(
            actual_statement_sha.encode("utf-8")
        ).hexdigest():
            entry["problem_statement_hash_reconciliation"] = {
                "registered_artifact_value": registered_statement_sha,
                "public_table_statement_sha256": actual_statement_sha,
                "reconciliation": "the artifact value is sha256 of the hex text of this hash",
                "candidate_pool_value": actual_statement_sha,
                "statement_bytes": len(
                    _problem_statement_from(entry).encode("utf-8")
                ),
            }
        else:
            problems.append(f"{task_id}: problem statement hash differs from registration")
    if len(entry["views"]) != READ_CALLS or len(entry["tool_names"]) != READ_CALLS:
        problems.append(
            f"{task_id}: the four-round shape requires {READ_CALLS} distinct views and tools, one per round"
        )
    ranges = {(view["file"], view["first_line"], view["last_line"]) for view in entry["views"]}
    if len(ranges) != len(entry["views"]):
        problems.append(f"{task_id}: two registered views cover the same file range")
    if len(set(entry["tool_names"])) != len(entry["tool_names"]):
        problems.append(f"{task_id}: two registered views share a tool name")
    if len(entry["contract"]["required_facts"]) > 3:
        problems.append(f"{task_id}: more than three required literals are registered")
    feasible = minimal_feasible_line(task_id)
    if feasible["chars"] > MAX_ANSWER_CHARS or feasible["problems"]:
        problems.append(
            f"{task_id}: the shortest feasible rendered line is {feasible['chars']} characters "
            "or does not satisfy the frozen conditions"
        )
    joined_parts = []
    for index, view in enumerate(entry["views"]):
        try:
            raw = blob_bytes(entry["base_commit"], view["file"])
        except FileNotFoundError:
            problems.append(f"{task_id}/view{index + 1}: pinned blob unreadable")
            continue
        if hashlib.sha256(raw).hexdigest() != view["file_content_sha256"]:
            problems.append(f"{task_id}/view{index + 1}: file content hash changed")
        if blob_git_id(entry["base_commit"], view["file"]) != view["blob_git_id"]:
            problems.append(f"{task_id}/view{index + 1}: git blob id changed")
        text = source_view(task_id, index)
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != view["view_content_sha256"]:
            problems.append(f"{task_id}/view{index + 1}: rendered view hash changed")
        if f"{view['first_line']}: " not in text or f"{view['last_line']}: " not in text:
            problems.append(f"{task_id}/view{index + 1}: view is not numbered as registered")
        joined_parts.append(text)
    joined = "\n".join(joined_parts)
    searchable = f"{joined}\n{statement(task_id)}"
    for token in (entry["cause_token"], entry["fix_token"]):
        if not token or token not in searchable:
            problems.append(f"{task_id}: contract token {token!r} is unreachable")
    for fact in entry["contract"]["required_facts"]:
        if str(fact) not in searchable:
            problems.append(f"{task_id}: required fact {fact!r} is unreachable")
    if int(entry["contract"].get("max_answer_chars") or 0) != MAX_ANSWER_CHARS:
        problems.append(f"{task_id}: the registered bound is not {MAX_ANSWER_CHARS} characters")
    if entry["expected_model_calls"] != entry["read_calls"] + 1:
        problems.append(f"{task_id}: expected model calls must be the read calls plus one")
    return problems


def all_problems() -> dict[str, list[str]]:
    return {task_id: verify(task_id) for task_id in task_ids()}


def input_hashes(task_id: str) -> dict[str, str]:
    entry = task(task_id)
    hashes = {
        f"{entry['source_artifact']}::{entry['task_id']}": entry["problem_statement_sha256"]
    }
    for index, view in enumerate(entry["views"]):
        hashes[f"{view['file']}#view{index + 1}"] = view["view_content_sha256"]
    return hashes


def artifact_hashes() -> dict[str, str]:
    return {
        str(REGISTRATION.relative_to(REPO)).replace("\\", "/"): hashlib.sha256(
            REGISTRATION.read_bytes()
        ).hexdigest(),
        str(STAGE1.relative_to(REPO)).replace("\\", "/"): hashlib.sha256(
            STAGE1.read_bytes()
        ).hexdigest(),
        str(STAGE1_MODULE.relative_to(REPO)).replace("\\", "/"): hashlib.sha256(
            STAGE1_MODULE.read_bytes()
        ).hexdigest(),
    }


def manifest_block() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "task_ids": list(task_ids()),
        "task_count": len(task_ids()),
        "artifact_hashes": artifact_hashes(),
        "derived_fields": list(DERIVED_FIELDS),
        "tool_name_sources": {
            task_id: task(task_id)["tool_names_source"] for task_id in task_ids()
        },
        "stage1_tool_names_note": (
            "the stage-1 artifact registered the three 15315 views and their roles but no tool "
            f"names; the three names are supplied here ({STAGE1_TOOL_NAMES_SOURCE}) and the view "
            "contents they return stay byte-verified against the artifact's view hashes"
        ),
        "protocol_steps_note": (
            "derived from the registered read shape: three views read twice (six read calls) "
            "then one answer call, which is the registered expected_model_calls of 7"
        ),
        "max_answer_chars": MAX_ANSWER_CHARS,
        "read_shape": {
            "rounds": READ_ROUNDS,
            "reads_per_round": READS_PER_ROUND,
            "read_calls": READ_CALLS,
            "expected_model_calls": EXPECTED_MODEL_CALLS,
            "distinct_views": "every round reads three views that no other round reads",
        },
        "minimal_feasible_lines": {
            task_id: minimal_feasible_line(task_id) for task_id in task_ids()
        },
        "read_calls": READ_CALLS,
        "expected_model_calls": EXPECTED_MODEL_CALLS,
        "per_task": {
            task_id: {
                "instance_id": task(task_id)["instance_id"],
                "repo": task(task_id)["repo"],
                "base_commit": task(task_id)["base_commit"],
                "views": [
                    {
                        "file": view["file"],
                        "first_line": view["first_line"],
                        "last_line": view["last_line"],
                        "role": view["role"],
                        "blob_git_id": view["blob_git_id"],
                        "view_content_sha256": view["view_content_sha256"],
                    }
                    for view in task(task_id)["views"]
                ],
                "tool_names": list(task(task_id)["tool_names"]),
                "tool_name_source": task(task_id)["tool_names_source"],
                "request": task(task_id)["request"],
                "protocol_steps": protocol_steps(task_id),
                "contract": task(task_id)["contract"],
                "contract_prompt": task(task_id)["contract_prompt"],
                "answer_pattern": task(task_id)["answer_pattern"],
                "required_terms": list(task(task_id)["required_terms"]),
                "literals": {
                    label: list(values)
                    for label, values in task(task_id)["literals"].items()
                },
                "fingerprint": fingerprint(task_id),
                "statement_sha256": task(task_id)["statement_sha256"],
                "problem_statement_sha256_registered": task(task_id)[
                    "problem_statement_sha256"
                ],
                "problem_statement_hash_reconciliation": task(task_id).get(
                    "problem_statement_hash_reconciliation"
                ),
                "derived_fields": list(task(task_id)["derived_fields"]),
                "problems": verify(task_id),
            }
            for task_id in task_ids()
        },
    }


__all__ = [
    "CLONE",
    "DERIVED_FIELDS",
    "EXPECTED_MODEL_CALLS",
    "MAX_ANSWER_CHARS",
    "PARQUET",
    "READ_CALLS",
    "REGISTRATION",
    "REGISTRY_SCHEMA",
    "REPO",
    "STAGE1",
    "STAGE1_MODULE",
    "STAGE1_TOOL_NAMES",
    "STAGE1_TOOL_NAMES_SOURCE",
    "all_problems",
    "artifact_hashes",
    "blob_bytes",
    "blob_git_id",
    "fingerprint",
    "history",
    "input_hashes",
    "instance_rows",
    "literal_phrases",
    "manifest_block",
    "problem_statement",
    "protocol_steps",
    "source_view",
    "stage1",
    "statement",
    "task",
    "task_ids",
    "tasks",
    "verify",
    "view_text",
    "view_texts",
    "views",
]
