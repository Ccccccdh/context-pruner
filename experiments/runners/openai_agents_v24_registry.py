"""The v24 registry: the v18 registry plus one length-discipline sentence in the first prompt.

What this file is
-----------------
A **fork** of ``experiments/runners/openai_agents_v18_registry.py``.  It is a new file; the v18
module is not edited, not moved and not re-pointed, so every earlier round keeps resolving exactly
the registry it resolved before.  Nothing in the task data changes here: the tasks, the views, the
line ranges, the blob ids, the view hashes, the literals, the frozen regexes, the registered
requests, the protocol steps, the ``cause``/``fix`` tokens, the 160-character bound and the
renderer are read from the same artifacts and are byte-identical to what the v18 registry builds.
The only difference is described below.

Why a new registry module rather than a runtime patch
-----------------------------------------------------
The discipline sentence has to be **in the first prompt**, and the first prompt is assembled by the
frozen runner from the case's ``final_contract``, which every caller fills from
``registry.task(task_id)["contract_prompt"]``.  Putting the sentence inside that value is the only
way to be sure it reaches the model on the first turn, for every arm, without editing the frozen
runner or the frozen live contract.  The sentence is one module constant, used by every task, so it
cannot vary by task, by round or by arm.

What the added sentence must do, and does
-----------------------------------------
* it states the bound **and** that exceeding it means the host rejects the whole answer;
* it gives a concrete working target well below the bound, so the model is not asked to guess how
  close to 160 it may sit;
* it restates the two-sided rule that already governs the contract: nothing may be dropped;
* it is appended to the existing contract prompt, so the existing sentence that already names the
  bound and the two-sided rule is untouched, and no requirement is weakened.

The added text is declared here, exposed as :data:`LENGTH_DISCIPLINE_TEXTS` and recorded in every
manifest this registry writes, so a reader can compare it with the module that produced it.
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
#: ``"sealed"`` is the sealed confirmation set, loaded only for a confirmation batch from its own
#: registration artifact.
ACTIVE = "development"

REGISTRY_SCHEMA = "openai_agents_v24_registry"
#: The v24 fork's parent module.  Recorded rather than imported: the point of the fork is that the
#: parent's bytes and behaviour are untouched, and a reader can verify that by digest.
PARENT_MODULE = "experiments/runners/openai_agents_v18_registry.py"
FORK_SCHEMA = "openai_agents_v24_registry_fork"
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

DERIVED_FIELDS = ("protocol_steps", "tool_docs", "contract_prompt_length_discipline")

#: The v24 addition, in two parts.  Part one is appended to the contract prompt of **every** task;
#: part two is a per-task budgeting sentence built from that task's own ``issue_value`` so the
#: arithmetic the model is given is true for the task it is working on.  Both parts are built by
#: :func:`_length_discipline_texts` from the registered values only, and both are the same for
#: every arm because neither is a function of the arm.
LENGTH_DISCIPLINE_SENTENCE = (
    " Length discipline: this is a hard rejection criterion, not a preference. The host accepts "
    "the answer only when the whole rendered line \"RESULT issue=<issue> cause=<cause> fix=<fix>\" "
    "is at most 160 characters, and when it is longer the host rejects the ENTIRE answer, so the "
    "answer is lost and the sample scores zero. Work to a target well below the bound: write "
    "cause and fix so that the two fields together come to about 120 characters or less, leaving "
    "room under the 160-character limit. Do not buy that room by dropping anything: every required "
    "string and both required technical names must still appear verbatim inside cause or fix, and "
    "an abbreviation, a shorter spelling or a summary of one of them does not count as the "
    "required string."
)
#: The per-task part.  ``{issue}`` and ``{headroom}`` are filled from the registered contract.
LENGTH_DISCIPLINE_BUDGET_SENTENCE = (
    " Budget for this task: the issue value \"{issue}\" plus the fixed prefix \"RESULT issue=\" "
    "and the fixed markers \" cause=\" and \" fix=\" already take {fixed} characters of the 160, "
    "so everything you write in the cause and fix fields together must stay within {headroom} "
    "characters to be accepted."
)
#: The fixed part of the rendered line, as a template; recorded rather than assumed.
RENDERED_LINE_TEMPLATE = "RESULT issue={issue} cause={cause} fix={fix}"
#: The working target the discipline sentence states, in characters for cause and fix together.
WORKING_TARGET_CAUSE_PLUS_FIX_CHARS = 120


_CACHE: dict[str, Any] = {}


def arm_neutral_prompt_evidence() -> dict[str, Any]:
    """Proof that the first prompt's v24 addition is the same for the three arms.

    The arms are a property of the model *input* filter, never of the agent instructions: the
    frozen runner assembles the instructions once per sample from the fixed sentence and the
    case's contract prompt, and this registry supplies that contract prompt from the task row
    alone.  This function therefore recomputes the prompt the runner would build for each arm
    from the same inputs, over every task, and reports the digests.
    """

    arms = ("none", "native_summary", "pruner_v1")
    digest_by_arm_task: dict[str, dict[str, str]] = {}
    for task_id in task_ids():
        prompt = str(task(task_id)["contract_prompt"])
        for arm in arms:
            digest_by_arm_task.setdefault(arm, {})[task_id] = hashlib.sha256(
                prompt.encode("utf-8")
            ).hexdigest()
    identical = all(
        len({digests[task_id] for digests in digest_by_arm_task.values()}) == 1
        for task_id in task_ids()
    )
    return {
        "arms": list(arms),
        "contract_prompt_sha256_by_arm_and_task": digest_by_arm_task,
        "the_contract_prompt_is_one_digest_per_task_across_all_three_arms": identical,
        "arm_is_not_an_input_of_the_prompt": True,
        "why_it_cannot_vary_by_arm": (
            "no function in this module takes the arm as an argument, and no arm rewrites the "
            "agent instructions after the frozen runner has assembled them"
        ),
        "task_ids": list(task_ids()),
    }


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
        + _length_discipline_texts(entry)
    )


# ------------------------------------------------------- the v24 length discipline (arm neutral)
def _fixed_rendered_prefix_chars(issue_value: str) -> int:
    """The characters of the rendered line that are not the model's own cause and fix text.

    Counted from the frozen rendered-line template, not assumed: ``"RESULT issue="``,
    ``" cause="``, ``" fix="`` and the issue value the contract fixes.
    """

    line = RENDERED_LINE_TEMPLATE.format(issue=issue_value, cause="", fix="")
    return len(line)


def _length_discipline_texts(entry: dict) -> str:
    """The two length-discipline sentences, built from this task's own registered values.

    Neither sentence mentions a task, a round or an arm, and the first is the same text for every
    task; the second substitutes only the task's own fixed issue value and the character counts
    that follow from it.  Both are therefore identical for the three arms by construction.
    """

    issue_value = str(entry["contract"]["issue_value"])
    fixed = _fixed_rendered_prefix_chars(issue_value)
    headroom = MAX_ANSWER_CHARS - fixed
    return LENGTH_DISCIPLINE_SENTENCE + LENGTH_DISCIPLINE_BUDGET_SENTENCE.format(
        issue=issue_value, fixed=fixed, headroom=headroom
    )


def length_discipline(entry: dict) -> dict[str, Any]:
    """The disclosure block for one task: the added text, its digest and its arithmetic."""

    text = _length_discipline_texts(entry)
    issue_value = str(entry["contract"]["issue_value"])
    fixed = _fixed_rendered_prefix_chars(issue_value)
    return {
        "appended_to": "contract_prompt",
        "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
        "identical_for_every_arm": (
            "the text is a function of the registered contract only: the arm is not an input of "
            "the function that builds it, and no arm rewrites the agent instructions, so all three "
            "arms receive these characters"
        ),
        "arm_neutral": (
            "the text is built from the registered contract only; no arm, no round and no filter "
            "can reach it, so every arm receives the same characters"
        ),
        "arithmetic": {
            "bound": MAX_ANSWER_CHARS,
            "rendered_line_template": RENDERED_LINE_TEMPLATE,
            "fixed_characters_of_the_rendered_line": fixed,
            "characters_left_for_cause_and_fix": MAX_ANSWER_CHARS - fixed,
            "working_target_cause_plus_fix_chars": WORKING_TARGET_CAUSE_PLUS_FIX_CHARS,
            "working_target_leaves_spare_characters": (MAX_ANSWER_CHARS - fixed)
            - WORKING_TARGET_CAUSE_PLUS_FIX_CHARS,
        },
        "what_it_states_that_the_previous_prompt_did_not": [
            "that exceeding the bound makes the host reject the whole answer, so the answer is "
            "lost, rather than merely being undesirable",
            "a concrete working target below the bound, in characters, for the two fields the "
            "rendered line is built from",
            "the exact number of characters this task's own issue value and the fixed markers "
            "consume of the bound",
        ],
        "what_it_does_not_change": [
            "the 160-character bound",
            "the required literals, the cause and fix tokens and the frozen regex",
            "the renderer, the retry budget and the two-sided diagnostic",
            "the mechanism, the window K=1, the gate threshold and the read shape",
        ],
    }


LENGTH_DISCIPLINE_TEXTS = (
    LENGTH_DISCIPLINE_SENTENCE,
    LENGTH_DISCIPLINE_BUDGET_SENTENCE,
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
    entry["contract_prompt_sha256"] = hashlib.sha256(
        entry["contract_prompt"].encode("utf-8")
    ).hexdigest()
    entry["length_discipline"] = length_discipline(entry)
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
        # The v24 fork's only addition is part of the fingerprint: a v24 sample cannot be confused
        # with a v23 sample that never saw the discipline sentence.
        "contract_prompt_sha256": entry["contract_prompt_sha256"],
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


def length_discipline_problems(task_id: str) -> list[str]:
    """The v24 fork's own invariants: the added sentence is present, true and arm neutral."""

    problems: list[str] = []
    entry = task(task_id)
    prompt = str(entry["contract_prompt"])
    discipline = dict(entry["length_discipline"])
    if discipline["text"] not in prompt:
        problems.append(f"{task_id}: the length discipline text is absent from the contract prompt")
    if not prompt.rstrip().endswith(discipline["text"].rstrip()):
        problems.append(f"{task_id}: the length discipline text is not the end of the contract prompt")
    if "160" not in discipline["text"]:
        problems.append(f"{task_id}: the length discipline text does not state the 160 bound")
    if "rejects the ENTIRE answer" not in discipline["text"]:
        problems.append(
            f"{task_id}: the length discipline text does not state that the whole answer is rejected"
        )
    if str(WORKING_TARGET_CAUSE_PLUS_FIX_CHARS) not in discipline["text"]:
        problems.append(f"{task_id}: the length discipline text gives no concrete working target")
    if "must still appear verbatim" not in discipline["text"]:
        problems.append(
            f"{task_id}: the length discipline text does not keep the two-sided literal rule"
        )
    fixed = int(discipline["arithmetic"]["fixed_characters_of_the_rendered_line"])
    if fixed != _fixed_rendered_prefix_chars(str(entry["contract"]["issue_value"])):
        problems.append(f"{task_id}: the disclosed fixed character count is not the measured one")
    if int(discipline["arithmetic"]["characters_left_for_cause_and_fix"]) != MAX_ANSWER_CHARS - fixed:
        problems.append(f"{task_id}: the disclosed cause-and-fix budget does not follow from the bound")
    if int(entry["contract"]["max_answer_chars"]) != MAX_ANSWER_CHARS:
        problems.append(f"{task_id}: the registered bound is not {MAX_ANSWER_CHARS} characters")
    return problems


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
    problems.extend(length_discipline_problems(task_id))
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


def fork_block() -> dict[str, Any]:
    """What this module is, and what its parent was, written into every manifest it feeds."""

    parent = REPO / PARENT_MODULE
    own = Path(__file__).resolve()
    return {
        "schema": FORK_SCHEMA,
        "module": str(own.relative_to(REPO)).replace("\\", "/"),
        "module_sha256": hashlib.sha256(own.read_bytes()).hexdigest(),
        "parent_module": PARENT_MODULE,
        "parent_sha256": hashlib.sha256(parent.read_bytes()).hexdigest() if parent.is_file() else None,
        "parent_is_not_edited": True,
        "registry_schema": REGISTRY_SCHEMA,
        "parent_registry_schema": "openai_agents_v18_registry",
        "only_change": (
            "the contract prompt of every task gains the length-discipline text below; the tasks, "
            "views, ranges, blob ids, view hashes, literals, frozen regexes, registered requests, "
            "protocol steps, cause and fix tokens, the 160-character bound and the renderer are "
            "unchanged"
        ),
        "length_discipline_texts": list(LENGTH_DISCIPLINE_TEXTS),
        "length_discipline_sentence_sha256": hashlib.sha256(
            LENGTH_DISCIPLINE_SENTENCE.encode("utf-8")
        ).hexdigest(),
        "budget_sentence_template_sha256": hashlib.sha256(
            LENGTH_DISCIPLINE_BUDGET_SENTENCE.encode("utf-8")
        ).hexdigest(),
        "working_target_cause_plus_fix_chars": WORKING_TARGET_CAUSE_PLUS_FIX_CHARS,
        "bound": MAX_ANSWER_CHARS,
    }


def manifest_block() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "fork": fork_block(),
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
                "contract_prompt_sha256": task(task_id)["contract_prompt_sha256"],
                "length_discipline": task(task_id)["length_discipline"],
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
    "FORK_SCHEMA",
    "LENGTH_DISCIPLINE_BUDGET_SENTENCE",
    "LENGTH_DISCIPLINE_SENTENCE",
    "LENGTH_DISCIPLINE_TEXTS",
    "MAX_ANSWER_CHARS",
    "PARENT_MODULE",
    "PARQUET",
    "READ_CALLS",
    "REGISTRATION",
    "REGISTRY_SCHEMA",
    "RENDERED_LINE_TEMPLATE",
    "REPO",
    "STAGE1",
    "STAGE1_MODULE",
    "STAGE1_TOOL_NAMES",
    "STAGE1_TOOL_NAMES_SOURCE",
    "WORKING_TARGET_CAUSE_PLUS_FIX_CHARS",
    "all_problems",
    "arm_neutral_prompt_evidence",
    "artifact_hashes",
    "blob_bytes",
    "blob_git_id",
    "fingerprint",
    "fork_block",
    "history",
    "input_hashes",
    "instance_rows",
    "length_discipline",
    "length_discipline_problems",
    "literal_phrases",
    "manifest_block",
    "minimal_feasible_line",
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
