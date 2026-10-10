"""Bounded v16 payload acquisition under the all-arm structured answer contract.

This runner spends the frozen acquisition batch - three development tasks, the ``none`` arm,
one repeat, at most 30 provider requests - and nothing else.  It refuses to run before
``V16_ACQUISITION_FREEZE_20261006.json`` verifies byte for byte (freeze digest, source-of-record
digests, renderer digest, task matrix, cap) and before the registry and the renderer self-test
are green.

Three modes, and only the paid one contacts a provider:

``--plan``
    Zero API.  Verifies the freeze, the registry and the renderer, prints the frozen request
    plan through the unmodified runner, and writes an artifact whose ``api_calls`` is 0.  The
    provider client and the provider model are replaced by objects that raise on use, so the
    zero-API claim is enforced, not asserted.
``--stub``
    Zero API.  Replays the three stage-1 negative controls (and one tampered positive control)
    through the *same* wiring - SDK ``Runner``, real ``output_type`` agent, real input filter,
    real recorder, real renderer and the one-retry wrapper - against a deterministic local
    model, and refuses to continue if any control does not land on its expected verdict.
``(default)``
    The paid batch: three tasks, one arm, one repeat, cap 30, with the manifest finalised in a
    single pass that refuses to overwrite an already finalised manifest.

The batch is payload acquisition only: it is not saving evidence and not quality evidence, and
both citation flags are written false.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents import function_tool

from experiments.runners import openai_agents_multitask_chain_v14 as chain
from experiments.runners import openai_agents_v17_live_contract as live
from experiments.runners import openai_agents_v18_registry as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8

REPO = registry.REPO
KIND = "openai_agents_v16_structured_payload_acquisition"
FREEZE = REPO / "integrations/openai_agents/V16_ACQUISITION_FREEZE_20261006.json"
FREEZE_SHA256 = "18c0d49f5cec9e7deebe6054ec6bb79356d4d289072d063dd196dada2eababc8"
EXPERIMENT_ID = "openai-repo-diagnostic-v18-four-round-shape-acquisition-01"
ATTEMPT = 3
ATTEMPT_PURPOSE = (
    "confirmatory acquisition after the retry gained the host validator's rejection reason: the "
    "only observed failure class was a declared fact that the rendered line cannot carry, and "
    "the retry previously repeated it blindly"
)
PREVIOUS_ATTEMPTS = {
    "attempt_01": {
        "batch": "runs/stage5-openai-agents-api/openai-repo-diagnostic-v16-structured-acquisition-01",
        "requests_spent": 3,
        "outcome": "the provider refused the structured-output request type (HTTP 400)",
        "detail": "integrations/openai_agents/V16_ACQUISITION_ATTEMPT_01_20261006.json",
    },
    "attempt_02": {
        "batch": "runs/stage5-openai-agents-api/openai-repo-diagnostic-v16-structured-acquisition-02",
        "requests_spent": 8,
        "outcome": (
            "informative baseline under option A: 2/3 samples rendered, 0/3 over-length, and the "
            "single failure was a declared fact missing from the rendered line, repeated by the "
            "retry"
        ),
        "detail": "runs/stage5-openai-agents-api/openai-repo-diagnostic-v16-structured-acquisition-02/manifest.json",
    },
}
PLAN_ARTIFACT = REPO / "integrations/openai_agents/V18_LIVE_PLAN_20261010.json"
STUB_ARTIFACT = REPO / "integrations/openai_agents/V18_LIVE_STUB_20261010.json"
AMENDMENT = (
    REPO / "integrations/openai_agents/V16_ACQUISITION_FREEZE_AMENDMENT_20261006.json"
)
METHODS = ("none",)
REPEATS = 1
MAX_API_REQUESTS = 30
#: twelve reads, an answer and a retry must all fit: the protocol asks for four
#: batches, but a model that issues single reads still needs twelve turns, and
#: truncating one arm silently is exactly the asymmetry this design must avoid.
MAX_TURNS = registry.READ_CALLS + 2
MAX_OUTPUT_TOKENS = 1024
MAX_API_RETRIES = 2
PROVIDER_SOFT, PROVIDER_TARGET, PROVIDER_HARD = 2000, 1500, 6000
DISCLOSURE = (
    "Public SWE-bench issue statements and three read-only ranges of public Django baseline "
    "source at the pinned base commits are sent through real SDK tools. No reference patch, "
    "evaluation test patch, local secret or expected answer is sent."
)
FORCED_FLAGS = (
    "--methods",
    "--repeats",
    "--scenarios",
    "--experiment-id",
    "--max-api-requests",
    "--max-turns",
    "--max-output-tokens",
    "--provider-soft",
    "--provider-target",
    "--provider-hard",
    "--max-summary-calls",
    "--max-api-retries",
    "--soft",
    "--hard",
    "--target",
    "--trigger-policy",
)


class _CaseWithTaskStatement:
    """The ApiCase plus the task statement the duplicate filter needs (plugin arm only)."""

    def __init__(self, case: Any, task_statement: str) -> None:
        self.__dict__["_wrapped"] = case
        self.__dict__["task_statement"] = str(task_statement)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_wrapped"], name)


# ------------------------------------------------------------------------- zero-API guards
class _NoNetworkClient:
    """Stand-in for the provider client: it can be constructed and closed, nothing else."""

    api_key = "zero-api-mode"
    base_url = "http://zero-api.invalid/v1"
    timeout = 1.0

    async def close(self) -> None:
        return None

    class _Chat:
        class _Completions:
            async def create(self, *args: Any, **kwargs: Any):
                raise AssertionError("provider call attempted in zero-API mode")

        completions = _Completions()

    chat = _Chat()


class _NoProviderModel:
    """Stand-in for the provider model: any actual call is a hard failure."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.arguments = (args, kwargs)

    async def get_response(self, *args: Any, **kwargs: Any):
        raise AssertionError("provider call attempted in zero-API mode")

    def stream_response(self, *args: Any, **kwargs: Any):
        raise AssertionError("provider call attempted in zero-API mode")

    def get_retry_advice(self, request: Any):
        return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    """Refuse to overwrite an existing artifact: the record is written once."""

    if path.exists():
        raise SystemExit(
            f"refusing to overwrite the existing artifact {path}: write a new one instead"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------- freeze
def verify_freeze() -> dict[str, Any]:
    if not FREEZE.is_file():
        raise SystemExit(f"the acquisition freeze is missing: {FREEZE}")
    digest = _sha256(FREEZE)
    if digest != FREEZE_SHA256:
        raise SystemExit(
            f"the acquisition freeze digest changed: {digest} != {FREEZE_SHA256}"
        )
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    if str(freeze.get("status")) != "FROZEN":
        raise SystemExit("the acquisition freeze is not FROZEN")
    if freeze.get("citable_as_saving") is not False or (
        freeze.get("citable_as_quality_equivalence") is not False
    ):
        raise SystemExit("the acquisition freeze must keep both citation flags false")
    matrix = freeze["matrix"]
    if (
        list(matrix["tasks"]) != list(registry.task_ids())
        or list(matrix["arms"]) != list(METHODS)
        or int(matrix["repeats"]) != REPEATS
        or int(matrix["max_api_requests"]) != MAX_API_REQUESTS
        or int(matrix["task_count"]) != len(registry.task_ids())
        or int(matrix["samples"]) != len(registry.task_ids()) * REPEATS
    ):
        raise SystemExit("the runner matrix differs from the frozen acquisition matrix")
    for relative, expected in freeze["source_of_record"].items():
        path = REPO / relative
        if not path.is_file() or _sha256(path) != expected:
            raise SystemExit(f"source-of-record drift: {relative}")
    renderer = freeze["host_configuration"]["renderer"]
    if _sha256(REPO / renderer["module"]) != renderer["sha256"]:
        raise SystemExit("the frozen renderer module changed")
    if renderer.get("truncates") is not False or renderer.get("drops_fields") is not False:
        raise SystemExit("the frozen renderer must not truncate or drop fields")
    if int(freeze["host_configuration"]["retry_budget"]) != live.RETRY_BUDGET_PER_SAMPLE:
        raise SystemExit("the frozen retry budget differs from the implemented one")
    criteria = freeze["criteria"]
    if int(criteria["max_answer_chars"]) != live.MAX_CHARS:
        raise SystemExit("the frozen bound differs from the renderer's bound")
    missing = [name for name in criteria["per_sample_report"] if name not in live.PER_SAMPLE_FIELDS]
    if missing:
        raise SystemExit(f"the per-sample report cannot fill frozen fields: {missing}")
    cache_fields = set(freeze["metering"]["cache_fields_per_call"])
    required_cache = {
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "call_timestamp_utc",
        "off_peak",
        "cache_fields_present",
        "cache_usage_unknown",
    }
    if not required_cache.issubset(cache_fields):
        raise SystemExit("the frozen per-call cache fields are not all implemented")
    return {
        "path": str(FREEZE.relative_to(REPO)).replace("\\", "/"),
        "sha256": digest,
        "status": freeze["status"],
        "matrix": matrix,
        "renderer_sha256": renderer["sha256"],
        "source_of_record": dict(freeze["source_of_record"]),
        "per_sample_report": list(criteria["per_sample_report"]),
    }


def verify_amendment() -> dict[str, Any]:
    """Check the amendment against the freeze and against the implementation on disk.

    The freeze pins the four source-of-record files by path *and* SHA256 and this runner
    re-verifies all four; what the freeze does not pin is the runner-side implementation that
    executes the batch, so that is pinned in the amendment and re-checked here.  A drift in any
    pinned file stops the batch before a request is sent.
    """

    if not AMENDMENT.is_file():
        raise SystemExit(f"the freeze amendment is missing: {AMENDMENT}")
    digest = _sha256(AMENDMENT)
    record = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    amendments = list(record.get("amendments", []))
    # Several entries may pin the same path (an entry per recorded change). A path is accepted
    # only when its current digest is exactly one of the digests an entry records, and the
    # manifest states which entry matched, so an undeclared edit can never pass.
    pinned: dict[str, dict[str, Any]] = {}
    archived: dict[str, dict[str, Any]] = {}
    drifted: list[str] = []
    for index, amendment in enumerate(amendments):
        for entry in amendment.get("additional_files_edited_after_freeze") or []:
            relative = str(entry["path"]).replace("\\", "/")
            if entry.get("pin") is False:
                # Recorded for traceability but not pinned: such a file is not executed by the
                # batch (for example the validation module), so pinning it would report every
                # later test edit as an implementation drift. A later archival entry wins over
                # an earlier pin, and the manifest states which files are archived.
                archived[relative] = {
                    "recorded_sha256": str(entry.get("new_sha256") or ""),
                    "reason": str(entry.get("reason") or ""),
                }
                pinned.pop(relative, None)
                continue
            recorded = pinned.setdefault(
                relative,
                {
                    "recorded_digests": [],
                    "matched_entry": None,
                    "current_sha256": "",
                    "matches": False,
                    "in_freeze": str(entry.get("frozen_sha256_in_freeze") or ""),
                    "reason": str(entry.get("reason") or ""),
                },
            )
            recorded["recorded_digests"].append(str(entry.get("new_sha256") or ""))
            if len(recorded["recorded_digests"]) > 1:
                recorded["reason"] = str(entry.get("reason") or recorded["reason"])
                recorded["in_freeze"] = str(
                    entry.get("frozen_sha256_in_freeze") or recorded["in_freeze"]
                )
    if not pinned:
        raise SystemExit("the amendment pins no implementation file")
    for relative, entry in pinned.items():
        path = REPO / relative
        current = _sha256(path) if path.is_file() else ""
        entry["current_sha256"] = current
        entry["matches"] = current in entry["recorded_digests"]
        if not entry["matches"]:
            drifted.append(relative)
        else:
            entry["matched_entry"] = entry["recorded_digests"].index(current)
        entry["recorded_sha256"] = current if entry["matches"] else ""
    if drifted:
        raise SystemExit(
            "pinned implementation files changed since the amendment: " + ", ".join(drifted)
        )
    for amendment in amendments:
        stated = str(amendment.get("freeze_sha256") or "")
        if stated and stated != FREEZE_SHA256:
            raise SystemExit(
                f"the amendment pins a different freeze digest: {stated} != {FREEZE_SHA256}"
            )
    replacement = next(
        (
            amendment.get("runtime_prompt_replacement")
            for amendment in record.get("amendments", [])
            if amendment.get("runtime_prompt_replacement")
        ),
        None,
    )
    if replacement:
        source = REPO / str(replacement["file"])
        if _sha256(source) != str(replacement.get("file_sha256")):
            raise SystemExit(
                "the file whose prompt the host adapts changed since the amendment"
            )
    return {
        "path": (
            str(AMENDMENT.relative_to(REPO)).replace("\\", "/")
            if str(AMENDMENT).startswith(str(REPO))
            else str(AMENDMENT)
        ),
        "sha256": digest,
        "freeze_sha256": FREEZE_SHA256,
        "freeze_unchanged": True,
        "pinned_files": pinned,
        "pinned_count": len(pinned),
        "archived_files": archived,
        "amendment_entries": len(amendments),
        "runtime_prompt_replacement": replacement,
        "files_changed_after_freeze": int(
            amendments[0].get("files_changed_after_freeze", 0)
        ),
    }


def source_of_record_evidence(freeze: dict[str, Any]) -> dict[str, Any]:
    """The four frozen inputs, each with the freeze's own hash and its hash on disk now."""

    files = {}
    for relative, recorded in freeze["source_of_record"].items():
        path = REPO / relative
        current = _sha256(path) if path.is_file() else ""
        files[relative] = {
            "recorded_sha256": str(recorded),
            "current_sha256": current,
            "matches": current == str(recorded),
        }
    return {
        "note": (
            "the freeze's source_of_record already carries a SHA256 per file; this block "
            "records both that hash and the file's hash at batch time so the check is "
            "reproducible from the batch artifact alone"
        ),
        "files": files,
        "all_match": all(entry["matches"] for entry in files.values()),
        "count": len(files),
    }


def verify_registration() -> dict[str, Any]:
    problems = registry.all_problems()
    if any(problems.values()):
        raise SystemExit(f"v16 registration did not verify: {problems}")
    return {
        "task_ids": list(registry.task_ids()),
        "problems": problems,
        "artifact_hashes": registry.artifact_hashes(),
        "tool_name_sources": {
            task_id: registry.task(task_id)["tool_names_source"]
            for task_id in registry.task_ids()
        },
        "derived_fields": {
            task_id: list(registry.task(task_id)["derived_fields"])
            for task_id in registry.task_ids()
        },
    }


def contract_table() -> dict[str, dict[str, Any]]:
    table = {}
    for task_id in registry.task_ids():
        entry = registry.task(task_id)
        contract = entry["contract"]
        table[task_id] = {
            "issue": str(contract["issue_value"]),
            "pattern": str(contract["frozen_regex"]),
            "facts": [str(fact) for fact in contract["required_facts"]],
            "cause_token": str(entry["cause_token"]),
            "fix_token": str(entry["fix_token"]),
        }
    return table


# -------------------------------------------------------------------------- task wiring
def tools_for(task_id: str) -> list[Any]:
    entry = registry.task(task_id)
    built = []
    for index, name in enumerate(entry["tool_names"]):
        doc = entry["tool_docs"][index]

        def make(index: int = index, doc: str = doc, name: str = name):
            @function_tool(name_override=name, description_override=doc)
            def reader() -> str:
                return registry.source_view(task_id, index)

            return reader

        built.append(make())
    return built


def build_case(task_id: str, repeat: int):
    entry = registry.task(task_id)
    contract = entry["contract"]
    case = base.ApiCase(
        scenario=task_id,
        repeat=int(repeat),
        codename=entry["instance_id"],
        history=registry.history(task_id),
        tools=tools_for(task_id),
        expected_terms=tuple(str(fact) for fact in contract["required_facts"]),
        # each of the three registered views is read twice, then the answer turn
        expected_tool_names=tuple(entry["tool_names"]) * 2,
        expected_model_calls=int(entry["expected_model_calls"]),
        final_contract=entry["contract_prompt"],
        answer_pattern=str(contract["frozen_regex"]),
        allow_repeat_tools=True,
        disable_thinking=True,
    )
    return _CaseWithTaskStatement(case, registry.statement(task_id))


def forced_args(args: list[str], *, out: str | None = None, confirm: bool = False) -> list[str]:
    if "--resume" in args or "--retry-failed" in args:
        raise SystemExit("the frozen discipline forbids --resume and --retry-failed")
    if "--confirm-send-synthetic-data" in args:
        raise SystemExit("use --confirm-send-public-source for exact public source")
    cleaned: list[str] = []
    skip = False
    for item in args:
        if skip:
            skip = False
            continue
        if item in FORCED_FLAGS:
            skip = True
            continue
        if item in (
            "--plan",
            "--stub",
            "--probe",
            "--pilot",
            "--pilot-stub",
            "--confirmation",
            "--confirmation-stub",
        ):
            continue
        if item == "--confirm-send-public-source":
            cleaned.append("--confirm-send-synthetic-data")
            continue
        if item == "--confirm-send-synthetic-data":
            raise SystemExit("use --confirm-send-public-source for exact public source")
        cleaned.append(item)
    if confirm and "--confirm-send-synthetic-data" not in cleaned:
        cleaned.append("--confirm-send-synthetic-data")
    if out is not None and "--out" not in cleaned:
        cleaned += ["--out", str(out)]
    return cleaned + [
        "--methods",
        ",".join(METHODS),
        "--repeats",
        str(REPEATS),
        "--scenarios",
        ",".join(registry.task_ids()),
        "--experiment-id",
        EXPERIMENT_ID,
        "--max-api-requests",
        str(MAX_API_REQUESTS),
        "--max-turns",
        str(MAX_TURNS),
        "--max-output-tokens",
        str(MAX_OUTPUT_TOKENS),
        "--provider-soft",
        str(PROVIDER_SOFT),
        "--provider-target",
        str(PROVIDER_TARGET),
        "--provider-hard",
        str(PROVIDER_HARD),
        "--max-summary-calls",
        "0",
        "--max-api-retries",
        str(MAX_API_RETRIES),
        "--trigger-policy",
        "symmetric_budget",
    ]


def planned_requests() -> dict[str, Any]:
    samples = len(registry.task_ids()) * len(METHODS) * REPEATS
    reads_plus_answer = int(registry.EXPECTED_MODEL_CALLS)
    logical = samples * reads_plus_answer
    with_retries = samples * (reads_plus_answer + live.RETRY_BUDGET_PER_SAMPLE)
    return {
        "samples": samples,
        "reads_plus_answer_per_sample": reads_plus_answer,
        "minimum_planned_requests": logical,
        "worst_case_logical_requests_with_one_retry_each": with_retries,
        "max_api_requests": MAX_API_REQUESTS,
        "max_api_retries_per_model_call": MAX_API_RETRIES,
        "transport_retries_share_the_cap": True,
        "worst_case_fits_the_cap": with_retries <= MAX_API_REQUESTS,
    }


# ------------------------------------------------------------------------- mode: --plan
def preflight() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Freeze, amendment, source-of-record and registration checks, all zero API."""

    freeze = verify_freeze()
    amendment = verify_amendment()
    sources = source_of_record_evidence(freeze)
    if not sources["all_match"]:
        raise SystemExit("a frozen source-of-record file changed")
    registration = verify_registration()
    self_test = live.self_test(
        registry.task(registry.task_ids()[0])["contract"]["issue_value"]
    )
    if not self_test["ok"]:
        raise SystemExit(f"the renderer self-test failed: {self_test['checks']}")
    plan = planned_requests()
    if not plan["worst_case_fits_the_cap"]:
        raise SystemExit("the planned worst case does not fit inside the frozen request cap")
    return freeze, amendment, sources, {"registration": registration, "self_test": self_test}


def run_plan(artifact: Path, args: list[str]) -> int:
    freeze, amendment, sources, checks = preflight()
    registration = verify_registration()
    self_test = live.self_test(registry.task(registry.task_ids()[0])["contract"]["issue_value"])
    if not self_test["ok"]:
        raise SystemExit(f"the renderer self-test failed: {self_test['checks']}")
    plan = planned_requests()
    cleaned = forced_args(args)
    original_client = base.AsyncOpenAI
    original_model = base.OpenAIChatCompletionsModel
    original_scenarios = base.SCENARIOS
    original_build_case = base.build_case
    base.SCENARIOS = tuple(registry.task_ids())
    base.build_case = lambda scenario, repeat: build_case(scenario, repeat)
    base.AsyncOpenAI = lambda **kwargs: _NoNetworkClient()
    base.OpenAIChatCompletionsModel = _NoProviderModel
    try:
        status = base.main(cleaned + ["--plan"])
    finally:
        base.AsyncOpenAI = original_client
        base.OpenAIChatCompletionsModel = original_model
        base.SCENARIOS = original_scenarios
        base.build_case = original_build_case
    payload = {
        "schema": "openai_agents_v16_live_plan",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "plan",
        "api_calls": 0,
        "zero_api_enforced_by": [
            "base.AsyncOpenAI replaced by a client whose completions call raises",
            "base.OpenAIChatCompletionsModel replaced by a model whose responses raise",
            "the frozen runner returns from --plan before any client is built",
        ],
        "runner_status": int(status),
        "freeze": freeze,
        "freeze_amendment": amendment,
        "source_of_record_verified": sources,
        "registration": checks["registration"],
        "renderer_self_test": checks["self_test"],
        "planned_requests": plan,
        "sdk_wiring": {
            "agent_output_type": "StructuredAnswer (pydantic, issue/cause/fix/facts)",
            "final_answer_path": live.RENDERER_MODULE,
            "retry_budget_per_sample": live.RETRY_BUDGET_PER_SAMPLE,
            "retry_is_billed": True,
            "per_sample_report_fields": list(live.PER_SAMPLE_FIELDS),
            "per_boundary_recording": (
                "input_sha256 / item_count / message_items / output_manifest(call_id, "
                "output_sha256, output_chars) are persisted per boundary by the v9 recorder"
            ),
            "per_call_cache_fields": [
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
                "call_timestamp_utc",
                "off_peak",
                "cache_fields_present",
                "cache_usage_unknown",
            ],
        },
        "paid_batch_started": False,
    }
    _write_artifact(artifact, payload)
    print(f"plan artifact: {artifact}")
    print(
        "api_calls=0 "
        f"samples={plan['samples']} worst_case_requests={plan['worst_case_logical_requests_with_one_retry_each']} "
        f"cap={MAX_API_REQUESTS}"
    )
    return 0


# ------------------------------------------------------------------------- mode: --stub
def stub_controls() -> list[dict[str, Any]]:
    """The host validator's negative controls plus one positive control.

    Six negatives, driven through the same wiring the paid batch uses: unparseable JSON,
    a missing field, an extra field, a duplicate key, a wrong field type, and a rendered
    answer that exceeds the frozen bound. Two more pin the independent checks: a proposal
    that renders but whose rendered line no longer matches the frozen regex (one character
    changed), and a well-formed proposal that must be accepted first time.
    """

    issue = "django-13821"
    good_cause = "check_sqlite_version accepts an older sqlite version"
    good_fix = "require 3.9.0"
    good_facts = ["check_sqlite_version", "3.9.0"]
    valid = {
        "issue": issue,
        "cause": good_cause,
        "fix": good_fix,
        "facts": good_facts,
    }
    over_long = dict(valid)
    over_long["cause"] = (
        "check_sqlite_version accepts an older sqlite version because the floor is only "
        "compared once " * 3
    ).strip()
    missing_field = {key: value for key, value in valid.items() if key != "fix"}
    extra_field = dict(valid, note="this key is not in the contract")
    wrong_type = dict(valid, facts="check_sqlite_version 3.9.0")
    declared_fact_absent = dict(valid, facts=good_facts + ["3.12.0"])
    tampered = dict(valid, fix="require 3.9.1", facts=["check_sqlite_version", "3.9.1"])
    duplicate_key = (
        '{"issue": "%s", "issue": "%s", "cause": "%s", "fix": "%s", "facts": ["%s"]}'
        % (issue, issue, good_cause, good_fix, good_facts[0])
    )
    # The repair text a model would produce *if it acted on the host's diagnostic*: same facts,
    # no declaration that the rendered line cannot carry.
    corrected_after_diagnostic = dict(
        valid, cause=f"{good_cause}, floor 3.8.3"
    )
    # The same, for the over-length class the three-arm pilot actually hit: the shortened
    # rewrite keeps all three required facts and both regex tokens and still fits the bound.
    all_facts = ["check_sqlite_version", "3.8.3", "3.9.0"]
    over_long_all_facts = {
        "issue": issue,
        "cause": (
            "check_sqlite_version currently accepts 3.8.3 because the comparison only checks "
            "the version once and never enforces the newer floor anywhere in the backend "
            * 2
        ).strip(),
        "fix": "require 3.9.0",
        "facts": all_facts,
    }
    shortened_all_facts = {
        "issue": issue,
        "cause": "check_sqlite_version floor is 3.8.3",
        "fix": "require 3.9.0",
        "facts": all_facts,
    }
    return [
        {
            "control": "invalid_json",
            "requirement": "unparseable proposal",
            "task": "django_sqlite_version_floor",
            "proposal": "RESULT issue=django-13821 cause=check_sqlite_version fix=require 3.9.0",
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:invalid_json_or_duplicate_key",
                "repair_calls": 1,
                "candidate_chars": 0,
            },
        },
        {
            "control": "missing_field",
            "requirement": "a required field is absent",
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(missing_field),
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:missing_or_extra_field",
                "repair_calls": 1,
                "candidate_chars": 0,
            },
        },
        {
            "control": "extra_field",
            "requirement": "an unexpected field is present",
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(extra_field),
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:missing_or_extra_field",
                "repair_calls": 1,
                "candidate_chars": 0,
            },
        },
        {
            "control": "duplicate_key",
            "requirement": "the same key appears twice",
            "task": "django_sqlite_version_floor",
            "proposal": duplicate_key,
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:invalid_json_or_duplicate_key",
                "repair_calls": 1,
                "candidate_chars": 0,
            },
        },
        {
            "control": "wrong_type",
            "requirement": "facts is a string instead of a list of strings",
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(wrong_type),
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:invalid_fact_list",
                "repair_calls": 1,
                "candidate_chars": 0,
            },
        },
        {
            "control": "missing_literal",
            "requirement": "a declared fact does not appear in the rendered line",
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(declared_fact_absent),
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:declared_fact_missing_from_rendered_answer",
                "repair_calls": 1,
                "candidate_over_160": False,
            },
        },
        {
            "control": "artificially_over_long",
            "requirement": "the rendered answer exceeds 160 characters",
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(over_long),
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:over_160_chars",
                "repair_calls": 1,
                "candidate_chars": None,
                "candidate_over_160": True,
            },
        },
        {
            "control": "diagnostic_used_by_model",
            "requirement": (
                "the host's rejection reason reaches the retry, and a model that acts on it is "
                "accepted on the retry"
            ),
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(declared_fact_absent),
            "repair_proposal": json.dumps(corrected_after_diagnostic),
            "uses_diagnostic": True,
            "expected": {
                "render_accepted": True,
                "reason": "accepted_retry",
                "repair_calls": 1,
                "frozen_regex": True,
                "diagnostic_carried": True,
            },
        },
        {
            "control": "diagnostic_ignored_still_fails",
            "requirement": (
                "a retry that ignores the diagnostic repeats the failure and is closed out"
            ),
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(declared_fact_absent),
            "uses_diagnostic": False,
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:declared_fact_missing_from_rendered_answer",
                "repair_calls": 1,
                "diagnostic_carried": True,
            },
        },
        {
            "control": "diagnostic_shortening_used_by_model",
            "requirement": (
                "an over-length proposal whose retry acts on the length diagnostic is accepted, "
                "with every required literal still present"
            ),
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(over_long_all_facts),
            "repair_proposal": json.dumps(shortened_all_facts),
            "uses_diagnostic": True,
            "expected": {
                "render_accepted": True,
                "reason": "accepted_retry",
                "repair_calls": 1,
                "frozen_regex": True,
                "facts_complete": True,
                "within_160": True,
                "diagnostic_carried": True,
            },
        },
        {
            "control": "diagnostic_shortening_ignored_still_fails",
            "requirement": (
                "the same over-length proposal, when the retry ignores the length diagnostic, "
                "stays over-length and the answer is closed to empty"
            ),
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(over_long_all_facts),
            "uses_diagnostic": False,
            "expected": {
                "render_accepted": False,
                "reason": "retry_rejected:over_160_chars",
                "repair_calls": 1,
                "candidate_over_160": True,
                "diagnostic_carried": True,
            },
        },
        {
            "control": "one_character_tampered_output",
            "requirement": "the rendered line no longer matches the frozen regex",
            "task": "django_sqlite_version_floor",
            "proposal": json.dumps(tampered),
            "expected": {
                "render_accepted": True,
                "reason": "accepted_first",
                "repair_calls": 0,
                "final_output_chars": None,
                "frozen_regex": False,
            },
        },
        {
            "control": "well_formed_positive",
            "requirement": "a well-formed proposal is accepted first time",
            "task": "django_orderedset_reversed",
            "proposal": json.dumps(
                {
                    "issue": "django-14089",
                    "cause": "OrderedSet lacks __reversed__",
                    "fix": "add __reversed__ to OrderedSet",
                    "facts": ["OrderedSet", "__reversed__"],
                }
            ),
            "expected": {
                "render_accepted": True,
                "reason": "accepted_first",
                "repair_calls": 0,
                "frozen_regex": True,
                "substituted": True,
            },
        },
    ]


def _boundary_summary(root: Path) -> dict[str, Any]:
    """What the per-boundary evidence of one sample recorded, read back from disk."""

    directory = root / "input-evidence"
    files = sorted(directory.glob("*.jsonl")) if directory.is_dir() else []
    records: list[dict[str, Any]] = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    manifest_keys: list[str] = []
    for record in records:
        for entry in record.get("output_manifest") or []:
            manifest_keys = sorted(entry)
            break
        if manifest_keys:
            break
    return {
        "files": [path.name for path in files],
        "records": len(records),
        "record_fields": sorted(records[0]) if records else [],
        "input_sha256_present": all("input_sha256" in record for record in records),
        "item_count_present": all("item_count" in record for record in records),
        "message_items_present": all(
            "recency_message_items" in record for record in records
        ),
        "output_manifest_present": all("output_manifest" in record for record in records),
        "output_manifest_entry_fields": manifest_keys,
    }


@contextmanager
def _null_wiring():
    """No-op wiring used by the paid path, which keeps the real provider model in place."""

    yield None


class _StubHolder:
    """The scripted proposal the local model returns for the current control."""

    def __init__(
        self,
        proposal: str,
        *,
        repair_proposal: str = "",
        uses_diagnostic: bool = True,
    ) -> None:
        self.proposal = str(proposal)
        self.repair_proposal = str(repair_proposal)
        self.uses_diagnostic = bool(uses_diagnostic)

    def factory(self, shared: dict[str, Any]):
        def build(inner: Any, request_budget: Any, **kwargs: Any):
            key = shared.get("key")
            scenario = str(key[0]) if key else registry.task_ids()[0]
            repeat = int(key[1]) if key else 0
            return live.ScriptedStubModel(
                build_case(scenario, repeat),
                request_budget=request_budget,
                final_text_value=self.proposal,
                final_text_after_diagnostic=self.repair_proposal,
                uses_diagnostic=self.uses_diagnostic,
            )

        return build


def run_stub(artifact: Path, args: list[str]) -> int:
    freeze, amendment, sources, preflight_checks = preflight()
    contracts = contract_table()
    import tempfile

    results: list[dict[str, Any]] = []
    original = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
        "client": base.AsyncOpenAI,
        "provider_model": base.OpenAIChatCompletionsModel,
        "chain_registry": chain.registry,
    }
    chain.registry = registry
    base.build_case = lambda scenario, repeat: build_case(scenario, repeat)
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    base.AsyncOpenAI = lambda **kwargs: _NoNetworkClient()
    base.OpenAIChatCompletionsModel = _NoProviderModel
    placeholder_installed = _placeholder_key_installed()
    try:
        for control in stub_controls():
            task_id = str(control["task"])
            base.SCENARIOS = (task_id,)
            holder = _StubHolder(
                str(control["proposal"]),
                repair_proposal=str(control.get("repair_proposal") or ""),
                uses_diagnostic=bool(control.get("uses_diagnostic", True)),
            )
            cleaned = forced_args(args, confirm=True)
            cleaned = _replace_option(cleaned, "--scenarios", task_id)
            cleaned = _replace_option(
                cleaned,
                "--experiment-id",
                f"{EXPERIMENT_ID}-stub-{control['control']}",
            )
            with tempfile.TemporaryDirectory() as tmp:
                cleaned = _replace_option(cleaned, "--out", tmp)
                parsed = base.build_parser().parse_args(cleaned)
                root = Path(parsed.out) / parsed.experiment_id
                budget, _ = base.resolve_budget(parsed)
                shared: dict[str, Any] = {}
                # The local model is installed *before* the evidence wiring so the evidence
                # proxy wraps it; the structured wrapper is installed after, so it is the
                # outermost model and sees every call, retries included.
                with chain.chain_wiring():
                    with live.inner_model_wiring(holder.factory(shared)):
                        with v8.evidence_wiring(
                            root,
                            int(budget.hard_limit_tokens) * 4,
                            recorder_factory=chain.MultitaskRecorder,
                        ):
                            with live.structured_wiring(
                                root, contracts=contracts, state=shared
                            ):
                                status = base.main(cleaned)
                row = json.loads(
                    (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()[-1]
                )
                boundaries = _boundary_summary(root)
            expected = dict(control["expected"])
            report = dict(row.get("render_report") or {})
            observed_reason = str(row.get("render_reason", ""))
            adaptations = dict(row.get("prompt_adaptations") or {})
            observed = {
                "status": int(status),
                "model_calls": int(row.get("model_calls", 0)),
                "api_request_attempts": int(row.get("api_request_attempts", 0)),
                "render_accepted": bool(row.get("render_accepted")),
                "reason": observed_reason,
                "repair_calls": int(row.get("render_repair_calls", 0)),
                "final_output": str(row.get("final_output", "")),
                "final_output_chars": len(str(row.get("final_output", ""))),
                "frozen_regex": bool(report.get("frozen_regex")),
                "facts_complete": bool(report.get("facts_complete")),
                "single_line_prefix": bool(report.get("single_line_prefix")),
                "within_160": bool(report.get("within_160")),
                "source_chars": int(report.get("source_chars", 0) or 0),
                "rendered_chars": int(report.get("rendered_chars", 0) or 0),
                "provider_calls": len(row.get("provider_calls") or []),
                "had_candidate": bool(row.get("render_had_candidate")),
                "candidate_chars": int(row.get("render_rendered_chars", 0) or 0),
                "substituted": bool(row.get("render_substituted_into_final_answer")),
                "diagnostic_carried": bool(row.get("render_diagnostic_carried")),
                "diagnostic_chars": len(str(row.get("render_diagnostic") or "")),
                "truncated": bool(row.get("render_truncated")),
                "fields_dropped": bool(row.get("render_fields_dropped")),
                "success": bool(row.get("success")),
                "boundaries": boundaries,
                "raw_usage_records": len(row.get("raw_provider_usage") or []),
                "raw_usage_pairing": str(row.get("raw_usage_pairing", "")),
                "calls_with_cache_fields": sum(
                    1
                    for call in (row.get("provider_calls") or [])
                    if call.get("cache_fields_present")
                ),
                "calls_with_cache_unknown": sum(
                    1
                    for call in (row.get("provider_calls") or [])
                    if call.get("cache_usage_unknown")
                ),
                "prompt_adaptations": {
                    "applied": list(adaptations.get("applied") or []),
                    "unapplied": list(adaptations.get("unapplied") or []),
                },
            }
            adaptations = dict(row.get("prompt_adaptations") or {})
            reasons_match = observed_reason == str(expected["reason"]) or observed_reason.startswith(
                str(expected["reason"])
            )
            checks = {
                "reason": reasons_match,
                "render_accepted": observed["render_accepted"] == bool(expected["render_accepted"]),
                "repair_calls": observed["repair_calls"] == int(expected["repair_calls"]),
                "never_truncated": observed["truncated"] is False,
                "no_field_dropped": observed["fields_dropped"] is False,
                "model_calls_are_reads_plus_answer_plus_retry": observed["model_calls"]
                == registry.EXPECTED_MODEL_CALLS + observed["repair_calls"],
                "frozen_prompt_adapted": not adaptations.get("unapplied")
                and len(adaptations.get("applied") or []) == len(live.PROMPT_REPLACEMENTS),
            }
            control_checks = checks
            if expected["render_accepted"] is False:
                checks["empty_final_answer"] = observed["final_output"] == ""
                checks["fail_closed_not_renderable"] = (
                    not observed["final_output"].startswith("RESULT ")
                    or observed["final_output"] == ""
                )
            if expected.get("candidate_chars") is not None:
                checks["candidate_chars"] = (
                    observed["candidate_chars"] == int(expected["candidate_chars"])
                )
            if expected.get("candidate_over_160"):
                checks["candidate_over_160"] = (
                    observed["candidate_chars"] > live.MAX_CHARS
                )
            if expected.get("substituted"):
                checks["rendered_line_substituted"] = observed["substituted"] is True
            if "diagnostic_carried" in expected:
                checks["diagnostic_carried"] = (
                    observed["diagnostic_carried"] == bool(expected["diagnostic_carried"])
                )
                checks["diagnostic_text_present"] = (
                    observed["diagnostic_chars"] > 0
                    if bool(expected["diagnostic_carried"])
                    else observed["diagnostic_chars"] == 0
                )
            if "frozen_regex" in expected:
                checks["frozen_regex"] = observed["frozen_regex"] == bool(expected["frozen_regex"])
            if "facts_complete" in expected:
                checks["facts_complete"] = observed["facts_complete"] == bool(
                    expected["facts_complete"]
                )
            if "within_160" in expected:
                checks["within_160"] = observed["within_160"] == bool(expected["within_160"])
            results.append(
                {
                    "control": control["control"],
                    "task": task_id,
                    "proposal_chars": len(str(control["proposal"])),
                    "expected": expected,
                    "observed": observed,
                    "checks": control_checks,
                    "ok": all(control_checks.values()),
                }
            )
    finally:
        base.SCENARIOS = original["scenarios"]
        base.build_case = original["build_case"]
        base.SYNTHETIC_DISCLOSURE = original["disclosure"]
        base.AsyncOpenAI = original["client"]
        base.OpenAIChatCompletionsModel = original["provider_model"]
        chain.registry = original["chain_registry"]
    payload = {
        "schema": "openai_agents_v16_live_stub",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "stub",
        "api_calls": 0,
        "api_key_placeholder_installed": bool(placeholder_installed),
        "api_key_used": False,
        "zero_api_enforced_by": [
            "base.AsyncOpenAI replaced by a client whose completions call raises",
            "base.OpenAIChatCompletionsModel replaced by a model whose responses raise",
            "the provider model is replaced by a deterministic local model before evidence wiring",
        ],
        "freeze": freeze,
        "freeze_amendment": amendment,
        "source_of_record_verified": sources,
        "registration": preflight_checks["registration"],
        "renderer_self_test": preflight_checks["self_test"],
        "controls": results,
        "controls_ok": all(entry["ok"] for entry in results),
        "wiring": {
            "agent_output_type": "StructuredAnswer (pydantic, issue/cause/fix/facts)",
            "final_answer_path": live.RENDERER_MODULE,
            "sdk_runner": "agents.Runner.run",
            "input_filter": "the real call_model_input_filter (baseline arm installs none)",
            "recorder": "chain.MultitaskRecorder (per-boundary input/output evidence)",
            "retry_budget_per_sample": live.RETRY_BUDGET_PER_SAMPLE,
            "per_sample_report_fields": list(live.PER_SAMPLE_FIELDS),
        },
        "paid_batch_started": False,
    }
    _write_artifact(artifact, payload)
    print(f"stub artifact: {artifact}")
    for entry in results:
        print(
            f"{entry['control']}: ok={entry['ok']} reason={entry['observed']['reason']} "
            f"repair_calls={entry['observed']['repair_calls']} "
            f"final_chars={entry['observed']['final_output_chars']}"
        )
    return 0 if payload["controls_ok"] else 1


def _replace_option(args: list[str], option: str, value: str) -> list[str]:
    cleaned = list(args)
    if option in cleaned:
        index = cleaned.index(option)
        cleaned[index + 1] = value
        return cleaned
    return cleaned + [option, value]


# -------------------------------------------------------------------------- mode: paid
def manifest_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The per-sample report the freeze fixes, plus the shares the decision needs.

    "No answer was produced" and "the rendered answer exceeded 160 characters" are counted
    separately: a sample that never reached the answer turn has no candidate at all, and folding
    it into an over-length share would report an environment failure as a length failure.
    """

    per_sample = []
    for row in sorted(rows, key=lambda item: (str(item["scenario"]), int(item["repeat"]))):
        report = dict(row.get("render_report") or {})
        per_sample.append(
            {
                "task": str(row["scenario"]),
                "repeat": int(row["repeat"]),
                "method": str(row["method"]),
                "source_chars": int(report.get("source_chars", 0) or 0),
                "rendered_chars": int(report.get("rendered_chars", 0) or 0),
                "render_accepted": bool(report.get("render_accepted")),
                "facts_complete": bool(report.get("facts_complete")),
                "frozen_regex": bool(report.get("frozen_regex")),
                "single_line_prefix": bool(report.get("single_line_prefix")),
                "within_160": bool(report.get("within_160")),
                "render_reason": str(row.get("render_reason", "")),
                "had_candidate": bool(row.get("render_had_candidate")),
                "candidate_chars": int(row.get("render_rendered_chars", 0) or 0),
                "repair_calls": int(row.get("render_repair_calls", 0) or 0),
                "requests": int(row.get("api_request_attempts", 0) or 0),
                "success": bool(row.get("success")),
                "error_type": str(row.get("error_type", "")),
            }
        )
    total = len(per_sample)
    candidates = [entry for entry in per_sample if entry["had_candidate"]]
    without = [entry for entry in per_sample if not entry["had_candidate"]]
    accepted = sum(1 for entry in per_sample if entry["render_accepted"])
    over_length = [entry for entry in candidates if entry["candidate_chars"] > live.MAX_CHARS]
    retried = [entry for entry in candidates if entry["repair_calls"] > 0]
    reasons: dict[str, int] = {}
    for entry in without:
        reason = entry["error_type"] or entry["render_reason"] or "unknown"
        key = f"provider_error:{reason}" if entry["error_type"] else f"no_answer_turn:{reason}"
        reasons[key] = reasons.get(key, 0) + 1
    return {
        "per_sample": per_sample,
        "samples": total,
        "render_accepted_count": accepted,
        "render_rejected_count": total - accepted,
        "render_failure_share": round((total - accepted) / total, 4) if total else None,
        "samples_with_a_candidate": len(candidates),
        "samples_without_a_candidate": len(without),
        "samples_without_a_candidate_reasons": reasons,
        "post_render_over_160_count": len(over_length),
        "post_render_over_160_share_over_candidates": (
            round(len(over_length) / len(candidates), 4) if candidates else None
        ),
        "post_render_over_160_share_over_all_samples": (
            round(len(over_length) / total, 4) if total else None
        ),
        "post_render_over_160_note": (
            "measured over samples that produced an answer candidate; a sample that never "
            "reached the answer turn is counted under samples_without_a_candidate and is not a "
            "length measurement"
        ),
        "retry_count": len(retried),
        "observed_retry_rate_over_candidates": (
            round(len(retried) / len(candidates), 4) if candidates else None
        ),
        "truncations": sum(1 for row in rows if row.get("render_truncated")),
        "fields_dropped": sum(1 for row in rows if row.get("render_fields_dropped")),
        "attainability_rule": (
            "every sample of the batch must render inside facts + frozen regex + 160 characters; "
            "a sample that cannot render counts as a failure and its share is reported"
        ),
        "attainable_on_this_batch": (total - accepted) == 0,
    }


def row_index(row: dict[str, Any]) -> int:
    return int(row.get("repeat", 0) or 0)


def _placeholder_key_installed() -> bool:
    """Stub mode needs no credential: the provider client is replaced before wiring."""

    names = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    if any(os.getenv(name) for name in names):
        return False
    os.environ["DEEPSEEK_API_KEY"] = "zero-api-placeholder"
    return True


def finalise_manifest(
    root: Path,
    *,
    freeze: dict[str, Any],
    amendment: dict[str, Any],
    sources: dict[str, Any],
    status: int,
    stopped_early: bool,
    stop_reason: str,
) -> dict[str, Any]:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("v16_manifest_finalized") is True:
        raise SystemExit(f"refusing to overwrite the finalised manifest at {path}")
    rows = [
        json.loads(line)
        for line in (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = manifest_report(rows)
    requests_used = sum(int(row.get("api_request_attempts", 0) or 0) for row in rows)
    caches = [
        call
        for row in rows
        for call in (row.get("raw_provider_usage") or row.get("provider_calls") or [])
    ]
    cache_source = (
        "raw_provider_usage"
        if any(row.get("raw_provider_usage") for row in rows)
        else "sdk_usage"
    )
    adaptation_complete = bool(rows) and all(
        not (row.get("prompt_adaptations") or {}).get("unapplied")
        and len((row.get("prompt_adaptations") or {}).get("applied") or [])
        == len(live.PROMPT_REPLACEMENTS)
        for row in rows
    )
    manifest.update(
        {
            "kind": KIND,
            "purpose": (
                "payload acquisition only: record the real model inputs and the rendered answers "
                "of the three development tasks under the all-arm structured contract, so that "
                "the frozen criteria can be checked per sample"
            ),
            "citable_as_saving": False,
            "citable_as_quality_equivalence": False,
            "not_evidence": (
                "this batch is not saving evidence and not quality evidence; a single repeat of "
                "the baseline arm cannot support either claim"
            ),
            "data_class": "public_source_diagnostic",
            "disclosure": DISCLOSURE,
            "freeze_path": freeze["path"],
            "freeze_sha256": freeze["sha256"],
            "freeze_matrix": freeze["matrix"],
            "source_of_record": freeze["source_of_record"],
            "source_of_record_verified": sources,
            "freeze_amendment": amendment,
            "implementation_hashes": {
                relative: entry["current_sha256"]
                for relative, entry in amendment["pinned_files"].items()
            },
            "v16_registry": registry.manifest_block(),
            "host_configuration": {
                "structured_final_answer": "JSON issue/cause/fix/facts, identical for every arm",
                "renderer": {
                    "module": live.RENDERER_MODULE,
                    "sha256": freeze["renderer_sha256"],
                    "truncates": False,
                    "drops_fields": False,
                    "final_answer_path": True,
                },
                "retry_budget": live.RETRY_BUDGET_PER_SAMPLE,
                "retry_is_billed": True,
                "agent_output_type": "StructuredAnswer",
                "frozen_prompt_adaptation": {
                    "applied": [old for old, _ in live.PROMPT_REPLACEMENTS],
                    "complete_in_every_sample": adaptation_complete,
                    "registered_in_amendment": amendment["path"],
                    "source_file": (
                        (amendment.get("runtime_prompt_replacement") or {}).get("file")
                    ),
                    "canary": (
                        "every sample records prompt_adaptations; a sample whose frozen prompt "
                        "no longer contains the replaced sentences is reported and stops the run"
                    ),
                    "why": (
                        "the frozen runner prompt describes the old model-written RESULT line; "
                        "under this host configuration the model writes JSON and the host renders "
                        "the line, so those two sentences are replaced identically in every arm"
                    ),
                },
            },
            "criteria": {
                "strict_track": [
                    "required facts present",
                    "frozen regex full match",
                    "single line with the contract prefix",
                    "at most 160 characters",
                ],
                "max_answer_chars": live.MAX_CHARS,
                "bounds_are_not_relaxed": (
                    "at most 160 characters remains an acceptance condition"
                ),
                "fidelity": (
                    "a sample whose fields cannot be rendered inside facts + frozen regex + 160 "
                    "characters counts as a failure; the share is reported; truncation and "
                    "dropped fields are forbidden"
                ),
                "per_sample_report": list(live.PER_SAMPLE_FIELDS),
            },
            "metering": {
                "cache_fields_per_call": [
                    "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens",
                    "call_timestamp_utc",
                    "off_peak",
                    "cache_fields_present",
                    "cache_usage_unknown",
                ],
                "cache_fields_fail_closed": (
                    "missing fields are recorded as unknown, never as zero"
                ),
                "cache_source": cache_source,
                "cache_note": (
                    "the SDK's own Usage type keeps only the standard counters, so the vendor "
                    "cache split is read from each raw chat-completion response; when a call has "
                    "no raw entry (a transport retry) its cache fields stay unknown and the "
                    "totals stay null rather than being reported as zero"
                ),
                "prompt_cache": _cache_aggregate(caches),
                "manifest_written_in_one_pass": True,
                "writer_refuses_overwrite": True,
            },
            "request_budget": {
                **planned_requests(),
                "api_calls": requests_used,
                "recorded_attempts_all_samples": requests_used,
            },
            "api_calls": requests_used,
            "attempt": ATTEMPT,
            "attempt_purpose": ATTEMPT_PURPOSE,
            "previous_attempts": PREVIOUS_ATTEMPTS,
            "batch_report": report,
            "prompt_adaptation_complete": adaptation_complete,
            "runner_status": int(status),
            "stopped_early": bool(stopped_early),
            "stop_reason": str(stop_reason),
            "stop_conditions": [
                "attainability fails: a material share of none-arm samples cannot render inside "
                "160 characters -> stop and report, do not proceed to the three arms",
                f"request cap {MAX_API_REQUESTS} exceeded -> stop",
                "any recorded sample missing its four condition flags -> stop",
            ],
            "never_rejudge": (
                "v12, v13, v14 and v15 freezes, scores and answers are untouched and unrejudged"
            ),
            "v16_manifest_finalized": True,
        }
    )
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _cache_aggregate(calls: list[dict[str, Any]]) -> dict[str, Any]:
    hits = [call for call in calls if call.get("cache_fields_present")]
    return {
        "calls": len(calls),
        "calls_with_cache_fields": len(hits),
        "calls_without_cache_fields": len(calls) - len(hits),
        "prompt_cache_hit_tokens_total": (
            sum(int(call["prompt_cache_hit_tokens"]) for call in hits) if hits else None
        ),
        "prompt_cache_miss_tokens_total": (
            sum(int(call["prompt_cache_miss_tokens"]) for call in hits) if hits else None
        ),
        "all_calls_off_peak": all(bool(call.get("off_peak")) for call in calls) if calls else None,
        "note": (
            "a batch whose provider omits the cache fields records them as unknown; the totals "
            "stay null rather than being reported as zero"
        ),
    }


# ------------------------------------------------------------------------- mode: pilot
PILOT_ATTEMPT = 2
PILOT_FREEZE = REPO / "integrations/openai_agents/V17_DEV_PILOT_FREEZE_20261010.json"
PILOT_EXPERIMENT_ID = "openai-repo-diagnostic-v18-four-round-shape-pilot-01"
PILOT_PREVIOUS = "runs/stage5-openai-agents-api/openai-repo-diagnostic-v16-three-arm-pilot-01"
PILOT_METHODS = ("none", "pruner_v1", "native_summary")
PILOT_REPEATS = 1
#: worst case per sample is MAX_TURNS + one retry = 7 requests, so nine samples need at
#: most 63 model requests plus six summary requests = 69; the cap of 90 keeps 1.30x
PILOT_MAX_API_REQUESTS = 100
PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE = 2
PILOT_ACCEPTANCE_LINE = (
    "per task, the plugin arm's strict track is at least the baseline's (the four conditions "
    "judged on the rendered line) AND the paired complete provider-token saving is positive for "
    "a majority of pairs AND the batch mean is positive; failures, cap hits, retries and summary "
    "requests are all charged to their arm"
)


def verify_pilot_freeze() -> dict[str, Any]:
    """The v17 development pilot freeze: written once, read here, recorded by digest.

    The v17 freeze has its own schema: it states the matrix, the budget arithmetic with the cap
    inside it, the decision line, the report requirements and the cannot-show list, and it pins
    the renderer hash in its unchanged list rather than listing module hashes. Everything it does
    state is verified here; everything it does not state is reported as not pinned rather than
    assumed.
    """

    if not PILOT_FREEZE.is_file():
        raise SystemExit(f"the v17 development pilot freeze is missing: {PILOT_FREEZE}")
    freeze = json.loads(PILOT_FREEZE.read_text(encoding="utf-8"))
    digest = _sha256(PILOT_FREEZE)
    if str(freeze.get("status")) != "FROZEN":
        raise SystemExit("the v17 freeze is not FROZEN")
    if str(freeze.get("purpose")) != "dev_pilot_first_attempt_compliance":
        raise SystemExit("the v17 freeze does not declare the first-attempt compliance purpose")
    if freeze.get("written_before_any_v17_request") is not True or int(
        freeze.get("api_requests_spent_when_written", -1)
    ) != 0:
        raise SystemExit("the v17 freeze must record that it was written before any request")
    if freeze.get("citable_as_saving") is not False or (
        freeze.get("citable_as_quality_equivalence") is not False
    ):
        raise SystemExit("the v17 freeze must keep both citation flags false")
    matrix = freeze["matrix"]
    arms = [str(arm) for arm in matrix["arms"]]
    if (
        int(matrix["tasks"]) != len(registry.task_ids())
        or int(matrix["repeats"]) != PILOT_REPEATS
        or sorted(arms) != sorted(PILOT_METHODS)
        or int(matrix["samples"]) != len(registry.task_ids()) * PILOT_REPEATS * len(PILOT_METHODS)
    ):
        raise SystemExit("the runner matrix differs from the frozen v17 matrix")
    arithmetic = freeze["budget_arithmetic"]
    if int(arithmetic["max_api_requests"]) != PILOT_MAX_API_REQUESTS:
        raise SystemExit("the frozen cap differs from the implemented one")
    worst = int(arithmetic["worst_case_requests"])
    if float(arithmetic["cap_over_worst_case"]) < 1.3 or PILOT_MAX_API_REQUESTS < 1.3 * worst:
        raise SystemExit("the frozen cap does not keep a 1.3x margin over its worst case")
    unchanged = " ".join(freeze["host_configuration_diff_against_v16"]["unchanged"])
    renderer_relative = "experiments/runners/openai_agents_structured_final_v16.py"
    renderer_sha = _sha256(REPO / renderer_relative)
    if renderer_sha not in unchanged:
        raise SystemExit("the freeze does not pin the renderer hash that is on disk now")
    return {
        "path": str(PILOT_FREEZE.relative_to(REPO)).replace("\\", "/"),
        "sha256": digest,
        "status": freeze["status"],
        "purpose": freeze["purpose"],
        "matrix": {
            "tasks": list(registry.task_ids()),
            "arms": list(PILOT_METHODS),
            "arms_as_frozen": arms,
            "repeats": PILOT_REPEATS,
            "samples": int(matrix["samples"]),
            "max_api_requests": PILOT_MAX_API_REQUESTS,
        },
        "budget_arithmetic": arithmetic,
        "decision_line": freeze["decision_line"],
        "acceptance_line": {
            "line": str(freeze["decision_line"]["pass_and_continue_to_task_shape_and_confirmation"]),
            "source": "the v17 freeze decides on first-attempt compliance and the retry tax",
        },
        "report_requirements": list(freeze["report_requirements"]),
        "cannot_show": list(freeze["cannot_show"]),
        "stop_conditions": list(freeze["stop_conditions"]),
        "required_wording": str(freeze["required_wording"]),
        "forbidden_wording": list(freeze["forbidden_wording"]),
        "renderer": {"module": renderer_relative, "sha256": renderer_sha, "pinned_in_freeze": True},
        "modules_pinned_in_freeze": [],
        "modules_not_pinned_note": (
            "the v17 freeze pins the renderer hash only; the runner and registry hashes are "
            "recorded in the manifest as computed values and in the host-configuration diff"
        ),
        "source_of_record": {},
    }

def pilot_arithmetic(freeze: dict[str, Any]) -> dict[str, Any]:
    """Zero-API upper-bound arithmetic, derived from the measured calls per sample."""

    samples = len(registry.task_ids()) * PILOT_REPEATS * len(PILOT_METHODS)
    #: the four-round shape issues four read turns and one answer turn per sample when the
    #: protocol is followed, and twelve reads, an answer and a retry when it is not
    measured_low, measured_high = 2.67, 3.0
    expected_per_sample = registry.READ_ROUNDS + 1
    expected = samples * expected_per_sample
    worst_per_sample = MAX_TURNS + live.RETRY_BUDGET_PER_SAMPLE
    worst_calls = samples * worst_per_sample
    native_samples = len(registry.task_ids()) * PILOT_REPEATS
    summary_expected = native_samples
    summary_worst = native_samples * PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE
    return {
        "samples": samples,
        "expected_model_calls_per_sample": registry.READ_ROUNDS + 1,
        "worst_case_model_calls_per_sample": MAX_TURNS + live.RETRY_BUDGET_PER_SAMPLE,
        "max_turns": MAX_TURNS,
        "read_calls": registry.READ_CALLS,
        "measured_model_calls_per_sample_low_of_the_two_round_shape": measured_low,
        "measured_model_calls_per_sample_high_of_the_two_round_shape": measured_high,
        "expected_requests": int(expected),
        "worst_case_requests": int(worst_calls + summary_worst),
        "best_case_requests": int(samples * 2 + native_samples),
        "summary_calls_expected": summary_expected,
        "summary_calls_worst_case": summary_worst,
        "cap": PILOT_MAX_API_REQUESTS,
        "cap_has_headroom": (worst_calls + summary_worst) <= PILOT_MAX_API_REQUESTS,
        "headroom_factor": round(PILOT_MAX_API_REQUESTS / (worst_calls + summary_worst), 2),
        "baseline_formula_minimum": (
            samples * registry.EXPECTED_MODEL_CALLS
            + (summary_worst if "native_summary" in PILOT_METHODS else 0)
        ),
        "frozen_cap_is_the_batches_own": True,
    }


def _paired_saving(pair: dict[str, dict[str, Any]], arm: str) -> float:
    baseline = float(pair["none"].get("all_arm_total_tokens", 0) or 0)
    plugin = float(pair[arm].get("all_arm_total_tokens", 0) or 0)
    return (baseline - plugin) / baseline if baseline else 0.0


def _strict(row: dict[str, Any]) -> bool:
    conditions = dict(row.get("render_conditions") or {})
    return bool(row.get("render_accepted")) and all(conditions.get(name) for name in live.CONDITION_NAMES)


def pilot_report(
    rows: list[dict[str, Any]],
    *,
    arm_set: tuple[str, ...] | None = None,
    repeat_count: int | None = None,
) -> dict[str, Any]:
    """Per-task and pooled paired statistics, per-arm failure decomposition, retries.

    The pre-registered verdict is computed over every pair of the frozen matrix. The
    trajectory-matched subset - pairs whose two arms took the same number of model calls - is
    reported next to it because it isolates the compression effect from the trajectory, but it
    never substitutes for the pre-registered reading.
    """

    methods = tuple(arm_set or PILOT_METHODS)
    repeats = int(repeat_count if repeat_count is not None else PILOT_REPEATS)
    by_key = {(str(row["scenario"]), int(row["repeat"]), str(row["method"])): row for row in rows}
    tasks = list(registry.task_ids())
    per_task: dict[str, Any] = {}
    pooled: list[float] = []
    matched_values: list[float] = []
    unmatched_values: list[float] = []
    positives = 0
    for task_id in tasks:
        savings: list[float] = []
        strict = {"none": 0, "pruner_v1": 0, "native_summary": 0}
        totals = {"none": 0, "pruner_v1": 0, "native_summary": 0}
        for repeat in range(repeats):
            pair = {
                method: by_key.get((task_id, repeat, method)) for method in methods
            }
            if any(row is None for row in pair.values()):
                continue
            for method in methods:
                if _strict(pair[method]):
                    strict[method] += 1
                totals[method] += int(pair[method].get("all_arm_total_tokens", 0) or 0)
            saving = _paired_saving(pair, "pruner_v1")
            savings.append(round(saving, 4))
            pooled.append(saving)
            if int(pair["none"].get("model_calls", 0) or 0) == int(
                pair["pruner_v1"].get("model_calls", 0) or 0
            ):
                matched_values.append(saving)
            else:
                unmatched_values.append(saving)
            if saving > 0:
                positives += 1
        per_task[task_id] = {
            "paired_n": len(savings),
            "paired_savings": savings,
            "mean_saving": round(sum(savings) / len(savings), 4) if savings else None,
            "positive_pairs": sum(1 for value in savings if value > 0),
            "min_saving": min(savings) if savings else None,
            "max_saving": max(savings) if savings else None,
            "strict_track": strict,
            "strict_track_plugin_at_least_baseline": strict["pruner_v1"] >= strict["none"],
            "complete_total_tokens": totals,
        }
    mean = sum(pooled) / len(pooled) if pooled else 0.0
    variance = (
        sum((value - mean) ** 2 for value in pooled) / (len(pooled) - 1)
        if len(pooled) > 1
        else 0.0
    )
    return {
        "per_task": per_task,
        "pooled": {
            "paired_n": len(pooled),
            "mean_saving": round(mean, 4),
            "median_saving": round(sorted(pooled)[len(pooled) // 2], 4) if pooled else None,
            "stdev_saving": round(variance**0.5, 4),
            "min_saving": min(pooled) if pooled else None,
            "max_saving": max(pooled) if pooled else None,
            "positive_pairs": positives,
            "majority_positive": positives * 2 > len(pooled) if pooled else False,
            "mean_positive": mean > 0,
        },
        "trajectory_matched_subset": {
            **pilot_spread(matched_values),
            "note": (
                "reported for explanation only: these are the pairs whose two arms took the same "
                "number of model calls, so they isolate the compression effect; the frozen verdict "
                "is decided on every pair of the matrix, never on this subset"
            ),
        },
        "trajectory_unmatched_subset": {
            **pilot_spread(unmatched_values),
            "note": "pairs whose arms differ in model calls; their spread is trajectory, not compression",
        },
        "acceptance_line": PILOT_ACCEPTANCE_LINE,
        "track_a_all_tasks": all(
            entry["strict_track_plugin_at_least_baseline"] for entry in per_task.values()
        ),
        "met": (
            all(entry["strict_track_plugin_at_least_baseline"] for entry in per_task.values())
            and positives * 2 > len(pooled)
            and mean > 0
        )
        if pooled
        else False,
    }


def pilot_spread(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean_saving": None, "stdev_saving": None, "min_saving": None, "max_saving": None}
    mean = sum(values) / len(values)
    variance = (
        sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        if len(values) > 1
        else 0.0
    )
    return {
        "n": len(values),
        "mean_saving": round(mean, 4),
        "stdev_saving": round(variance**0.5, 4),
        "min_saving": round(min(values), 4),
        "max_saving": round(max(values), 4),
        "positive_pairs": sum(1 for value in values if value > 0),
    }


def pilot_length_trajectory(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """First answer versus retried answer per sample: did the retry shorten or grow?"""

    samples = []
    for row in rows:
        ledger = (row.get("render_ledger") or [{}])[-1]
        samples.append(
            {
                "task": str(row["scenario"]),
                "repeat": int(row["repeat"]),
                "method": str(row["method"]),
                "first_render_reason": str(ledger.get("first_render_reason") or ""),
                "first_raw_chars": int(ledger.get("first_raw_chars", 0) or 0),
                "first_candidate_chars": int(ledger.get("first_candidate_chars", 0) or 0),
                "retried": int(row.get("render_repair_calls", 0) or 0) > 0,
                "repair_raw_chars": ledger.get("repair_raw_chars"),
                "repair_candidate_chars": ledger.get("repair_candidate_chars"),
                "repair_candidate_delta_chars": ledger.get("repair_candidate_delta_chars"),
                "repair_render_reason": ledger.get("repair_render_reason"),
                "retry_shortened_the_candidate": (
                    ledger.get("repair_candidate_delta_chars") is not None
                    and int(ledger["repair_candidate_delta_chars"]) < 0
                ),
                "retry_grew_the_candidate": (
                    ledger.get("repair_candidate_delta_chars") is not None
                    and int(ledger["repair_candidate_delta_chars"]) > 0
                ),
                "diagnostic_carried": bool(ledger.get("diagnostic_carried_in_retry_input")),
                "diagnostic_named_the_bound": "160" in str(ledger.get("diagnostic") or ""),
                "accepted_final": bool(row.get("render_accepted")),
                "render_reason": str(row.get("render_reason", "")),
            }
        )
    retried = [entry for entry in samples if entry["retried"]]
    return {
        "samples": samples,
        "retried_samples": len(retried),
        "retries_that_shortened": sum(1 for entry in retried if entry["retry_shortened_the_candidate"]),
        "retries_that_grew": sum(1 for entry in retried if entry["retry_grew_the_candidate"]),
        "retries_carrying_the_diagnostic": sum(
            1 for entry in retried if entry["diagnostic_carried"]
        ),
        "retries_naming_the_bound": sum(
            1 for entry in retried if entry["diagnostic_named_the_bound"]
        ),
    }


def pilot_failure_decomposition(
    rows: list[dict[str, Any]], *, arm_set: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Per-arm failure classes and retries, so no arm's failures hide in a pooled mean."""

    decomposition: dict[str, Any] = {}
    for method in tuple(arm_set or PILOT_METHODS):
        arm_rows = [row for row in rows if str(row["method"]) == method]
        classes: dict[str, int] = {}
        for row in arm_rows:
            reason = str(row.get("render_reason", ""))
            error = str(row.get("error_type", ""))
            if row.get("render_accepted"):
                continue
            if error:
                key = f"provider_or_run_error:{error}"
            elif reason.startswith("retry_rejected:"):
                key = reason.split(":", 1)[1]
            elif reason:
                key = reason
            else:
                key = "no_answer_turn"
            classes[key] = classes.get(key, 0) + 1
        decomposition[method] = {
            "samples": len(arm_rows),
            "strict_pass": sum(1 for row in arm_rows if _strict(row)),
            "render_accepted": sum(1 for row in arm_rows if row.get("render_accepted")),
            "failures": classes,
            "retries": sum(int(row.get("render_repair_calls", 0) or 0) for row in arm_rows),
            "diagnostic_carried_samples": sum(
                1 for row in arm_rows if row.get("render_diagnostic_carried")
            ),
            "retry_rate": (
                round(
                    sum(1 for row in arm_rows if int(row.get("render_repair_calls", 0) or 0) > 0)
                    / len(arm_rows),
                    4,
                )
                if arm_rows
                else None
            ),
            "summary_calls": sum(int(row.get("summary_calls", 0) or 0) for row in arm_rows),
            "requests": sum(int(row.get("api_request_attempts", 0) or 0) for row in arm_rows),
            "complete_total_tokens": sum(
                int(row.get("all_arm_total_tokens", 0) or 0) for row in arm_rows
            ),
        }
    return decomposition


def forced_pilot_args(args: list[str]) -> list[str]:
    cleaned = forced_args(args)
    return _replace_option(cleaned, "--methods", ",".join(PILOT_METHODS)) + [
        "--repeats",
        str(PILOT_REPEATS),
        "--experiment-id",
        PILOT_EXPERIMENT_ID,
        "--max-api-requests",
        str(PILOT_MAX_API_REQUESTS),
        "--max-summary-calls",
        str(PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE),
    ]


def run_pilot(
    args: list[str],
    *,
    inner_factory: Any | None = None,
    freeze_override: dict[str, Any] | None = None,
) -> int:
    """The 27-sample three-arm pilot under the frozen v16 host configuration.

    ``freeze_override`` exists only for the zero-API rehearsal, which runs before the pilot
    freeze file is written: the rehearsal supplies an in-memory draft describing the same
    matrix, cap and summary bound. The paid path always calls ``verify_pilot_freeze()``.
    """

    freeze = freeze_override if freeze_override is not None else verify_pilot_freeze()
    amendment = verify_amendment()
    if not (
        "--confirm-send-public-source" in args or "--confirm-send-synthetic-data" in args
    ):
        # ``forced_args`` only ever produces the synthetic form by converting an explicit
        # public-source confirmation, and it rejects the synthetic flag typed by hand, so this
        # accepts both spellings without weakening the guard.
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    arithmetic = pilot_arithmetic(freeze)
    if not arithmetic["cap_has_headroom"]:
        raise SystemExit("the pilot's worst case does not fit inside its own frozen cap")
    cleaned = forced_pilot_args(args)
    parsed = base.build_parser().parse_args(cleaned)
    if int(parsed.max_api_requests) != PILOT_MAX_API_REQUESTS:
        raise SystemExit("the pilot cap differs from the frozen cap")
    root = Path(parsed.out) / parsed.experiment_id
    budget, _ = base.resolve_budget(parsed)
    contracts = contract_table()
    original = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
        "chain_registry": chain.registry,
    }
    base.SCENARIOS = tuple(registry.task_ids())
    base.build_case = lambda scenario, repeat: build_case(scenario, repeat)
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    chain.registry = registry
    status = 0
    stopped_early = False
    stop_reason = ""
    shared: dict[str, Any] = {}
    inner_wiring = (
        live.inner_model_wiring(inner_factory(shared))
        if inner_factory is not None
        else _null_wiring()
    )
    try:
        with chain.chain_wiring():
            with inner_wiring:
                with v8.evidence_wiring(
                    root,
                    int(budget.hard_limit_tokens) * 4,
                    recorder_factory=chain.MultitaskRecorder,
                ):
                    with live.structured_wiring(
                        root,
                        contracts=contracts,
                        state=shared,
                        check_request_cap=True,
                    ):
                        status = base.main(cleaned)
    except live.RequestCapReached as error:
        stopped_early = True
        stop_reason = str(error)
        status = 3
        print(f"pilot stopped: {stop_reason}")
    finally:
        base.SCENARIOS = original["scenarios"]
        base.build_case = original["build_case"]
        base.SYNTHETIC_DISCLOSURE = original["disclosure"]
        chain.registry = original["chain_registry"]
    manifest = finalise_pilot_manifest(
        root,
        freeze=freeze,
        amendment=amendment,
        arithmetic=arithmetic,
        status=status,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
    )
    report = manifest["pilot_report"]
    print(
        f"samples={manifest['batch_report']['samples']} "
        f"pooled_mean_saving={report['pooled']['mean_saving']} "
        f"positive_pairs={report['pooled']['positive_pairs']}/{report['pooled']['paired_n']} "
        f"track_a_all_tasks={report['track_a_all_tasks']} met={report['met']} "
        f"api_calls={manifest['api_calls']}/{PILOT_MAX_API_REQUESTS}"
    )
    print(f"manifest: {root / 'manifest.json'}")
    return int(status)


def finalise_pilot_manifest(
    root: Path,
    *,
    freeze: dict[str, Any],
    amendment: dict[str, Any],
    arithmetic: dict[str, Any],
    status: int,
    stopped_early: bool,
    stop_reason: str,
    arm_set: tuple[str, ...] | None = None,
    repeat_count: int | None = None,
    summary_cap: int | None = None,
    kind: str = "openai_agents_v16_three_arm_pilot",
    purpose: str = "three_arm_pilot",
    attempt: int | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    methods = tuple(arm_set or PILOT_METHODS)
    repeats = int(repeat_count if repeat_count is not None else PILOT_REPEATS)
    cap = int(summary_cap if summary_cap is not None else PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("v16_manifest_finalized") is True:
        raise SystemExit(f"refusing to overwrite the finalised manifest at {path}")
    rows = [
        json.loads(line)
        for line in (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    batch = manifest_report(rows)
    requests_used = sum(int(row.get("api_request_attempts", 0) or 0) for row in rows)
    summary_calls = sum(int(row.get("summary_calls", 0) or 0) for row in rows)
    over_summary = [
        str(row["scenario"])
        for row in rows
        if int(row.get("summary_calls", 0) or 0) > cap
    ]
    caches = [
        call
        for row in rows
        for call in (row.get("raw_provider_usage") or row.get("provider_calls") or [])
    ]
    adaptation_complete = bool(rows) and all(
        not (row.get("prompt_adaptations") or {}).get("unapplied")
        and len((row.get("prompt_adaptations") or {}).get("applied") or [])
        == len(live.PROMPT_REPLACEMENTS)
        for row in rows
    )
    manifest.update(
        {
            "kind": kind,
            "purpose": purpose,
            "citable_as_saving": False,
            "citable_as_quality_equivalence": False,
            "citable_note": (
                "the flags stay false until an independent audit passes; this pilot is "
                "development evidence for this host configuration, because the three development "
                "tasks took part in building it"
            ),
            "data_class": "public_source_diagnostic",
            "disclosure": DISCLOSURE,
            "freeze_path": freeze["path"],
            "freeze_sha256": freeze["sha256"],
            "freeze_matrix": freeze["matrix"],
            "source_of_record": freeze["source_of_record"],
            "source_of_record_verified": {
                relative: {
                    "recorded_sha256": str(expected),
                    "current_sha256": _sha256(REPO / relative),
                    "matches": _sha256(REPO / relative) == str(expected),
                }
                for relative, expected in freeze["source_of_record"].items()
            },
            "host_configuration": {
                "structured_final_answer": (
                    "JSON issue/cause/fix/facts asked for in the prompt, identical for every arm, "
                    "rendered by the frozen renderer, which is the final-answer path and the only "
                    "validator"
                ),
                "renderer": {
                    "module": live.RENDERER_MODULE,
                    "sha256": _sha256(REPO / live.RENDERER_MODULE),
                    "truncates": False,
                    "drops_fields": False,
                },
                "retry_budget": live.RETRY_BUDGET_PER_SAMPLE,
                "retry_is_billed": True,
                "retry_carries_host_diagnostic": True,
                "diagnostic_module": live.__name__,
                "modules": freeze.get("modules") or {},
                "summary_cap_per_sample": cap,
                "frozen_prompt_adaptation": {
                    "applied": [old for old, _ in live.PROMPT_REPLACEMENTS],
                    "complete_in_every_sample": adaptation_complete,
                },
            },
            "criteria": {
                "strict_track": [
                    "required facts present",
                    "frozen regex full match",
                    "single line with the contract prefix",
                    "at most 160 characters",
                ],
                "max_answer_chars": live.MAX_CHARS,
                "bounds_are_not_relaxed": (
                    "at most 160 characters remains an acceptance condition"
                ),
                "per_sample_report": list(live.PER_SAMPLE_FIELDS),
            },
            "acceptance_line": freeze["acceptance_line"],
            "pilot_report": pilot_report(rows, arm_set=methods, repeat_count=repeats),
            "length_trajectory": pilot_length_trajectory(rows),
            "failure_decomposition": pilot_failure_decomposition(rows, arm_set=methods),
            "attempt": attempt if attempt is not None else PILOT_ATTEMPT,
            "previous_attempt": previous or {
                "batch": PILOT_PREVIOUS,
                "outcome": (
                    "pilot 01 failed the frozen quality gate: one task's plugin strict track was "
                    "below its baseline, and every over-length rejection there began as a "
                    "declared-fact rejection whose diagnostic never named the 160-character bound"
                ),
                "detail": "integrations/openai_agents/V16_PILOT_RETRO_20261006.json",
            },
            "batch_report": batch,
            "request_budget": {
                **arithmetic,
                "api_calls": requests_used,
                "summary_calls": summary_calls,
                "max_summary_calls_per_sample": cap,
                "samples_over_the_summary_cap": over_summary,
            },
            "api_calls": requests_used,
            "metering": {
                "cache_fields_per_call": [
                    "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens",
                    "call_timestamp_utc",
                    "off_peak",
                    "cache_fields_present",
                    "cache_usage_unknown",
                ],
                "cache_fields_fail_closed": (
                    "missing fields are recorded as unknown, never as zero"
                ),
                "cache_source": (
                    "raw_provider_usage"
                    if any(row.get("raw_provider_usage") for row in rows)
                    else "sdk_usage"
                ),
                "prompt_cache": _cache_aggregate(caches),
                "manifest_written_in_one_pass": True,
                "writer_refuses_overwrite": True,
            },
            "prompt_adaptation_complete": adaptation_complete,
            "stopped_early": bool(stopped_early),
            "stop_reason": str(stop_reason),
            "stop_conditions": list(freeze["stop_conditions"]),
            "report_requirements": list(freeze["report_requirements"]),
            "runner_status": int(status),
            "v16_manifest_finalized": True,
        }
    )
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def pilot_draft_freeze() -> dict[str, Any]:
    """The in-memory description the zero-API rehearsal runs under (no file is written)."""

    return {
        "path": "<draft: the pilot freeze is written after the rehearsal>",
        "sha256": "0" * 64,
        "status": "DRAFT_FOR_REHEARSAL",
        "purpose": "three_arm_pilot",
        "matrix": {
            "tasks": list(registry.task_ids()),
            "arms": list(PILOT_METHODS),
            "repeats": PILOT_REPEATS,
            "samples": len(registry.task_ids()) * PILOT_REPEATS * len(PILOT_METHODS),
            "max_api_requests": PILOT_MAX_API_REQUESTS,
        },
        "modules": {
            relative: _sha256(REPO / relative)
            for relative in (
                "experiments/runners/run_openai_agents_v16_acquisition.py",
                "experiments/runners/openai_agents_v16_live_contract.py",
                "experiments/runners/openai_agents_v16_registry.py",
                "experiments/runners/openai_agents_structured_final_v16.py",
                "experiments/runners/openai_agents_multitask_chain_v14.py",
            )
        },
        "summary_cap_per_sample": PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE,
        "acceptance_line": {"line": PILOT_ACCEPTANCE_LINE},
        "source_of_record": {},
        "stop_conditions": ["draft rehearsal: no request is sent"],
        "report_requirements": ["draft rehearsal"],
    }


def run_pilot_stub(args: list[str]) -> int:
    """Zero-API rehearsal of the whole pilot: 27 samples, three arms, deterministic local model.

    It exists to exercise the plugin arm's frozen filter, the three-arm rotation, the summary
    accounting and the pilot report before any request is spent; it writes into a temporary
    directory and never touches the real batch path.
    """

    contracts = contract_table()
    originals = {
        "client": base.AsyncOpenAI,
        "provider_model": base.OpenAIChatCompletionsModel,
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
        "chain_registry": chain.registry,
    }
    base.AsyncOpenAI = lambda **kwargs: _NoNetworkClient()
    base.OpenAIChatCompletionsModel = _NoProviderModel
    placeholder_installed = _placeholder_key_installed()

    def proposal(task_id: str) -> str:
        entry = registry.task(task_id)
        contract = entry["contract"]
        facts = [str(fact) for fact in contract["required_facts"]]
        return json.dumps(
            {
                "issue": str(contract["issue_value"]),
                "cause": ("cause: " + " ".join(facts[:2]) + " " + entry["cause_token"]).strip(),
                "fix": ("fix: " + " ".join(facts[2:]) + " " + entry["fix_token"]).strip(),
                "facts": facts,
            }
        )

    def factory(shared: dict[str, Any]):
        def build(inner: Any, request_budget: Any, **kwargs: Any):
            key = shared.get("key")
            task_id = str(key[0]) if key else registry.task_ids()[0]
            repeat = int(key[1]) if key else 0
            return live.ScriptedStubModel(
                build_case(task_id, repeat),
                request_budget=request_budget,
                final_text_value=proposal(task_id),
            )

        return build

    try:
        with tempfile.TemporaryDirectory() as tmp:
            cleaned = ["--confirm-send-public-source", "--out", tmp]
            status = run_pilot(
                cleaned,
                inner_factory=factory,
                freeze_override=pilot_draft_freeze(),
            )
            parsed = base.build_parser().parse_args(forced_pilot_args(cleaned))
            root = Path(parsed.out) / parsed.experiment_id
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            report = manifest["pilot_report"]
            print(
                "rehearsal: "
                f"samples={manifest['batch_report']['samples']} "
                f"api_calls={manifest['api_calls']} "
                f"pooled_mean_saving={report['pooled']['mean_saving']} "
                f"positive_pairs={report['pooled']['positive_pairs']}/{report['pooled']['paired_n']} "
                f"summary_calls={manifest['request_budget']['summary_calls']} "
                f"placeholder_key={placeholder_installed}"
            )
            for method, entry in manifest["failure_decomposition"].items():
                print(
                    f"  {method}: strict={entry['strict_pass']}/{entry['samples']} "
                    f"retries={entry['retries']} summary={entry['summary_calls']} "
                    f"requests={entry['requests']} failures={entry['failures']}"
                )
            for task_id, entry in report["per_task"].items():
                print(
                    f"  {task_id}: mean={entry['mean_saving']} "
                    f"strict={entry['strict_track']} "
                    f"plugin_at_least_baseline={entry['strict_track_plugin_at_least_baseline']}"
                )
    finally:
        base.AsyncOpenAI = originals["client"]
        base.OpenAIChatCompletionsModel = originals["provider_model"]
        base.SCENARIOS = originals["scenarios"]
        base.build_case = originals["build_case"]
        base.SYNTHETIC_DISCLOSURE = originals["disclosure"]
        chain.registry = originals["chain_registry"]
    return int(status)


# ------------------------------------------------------------------- mode: confirmation
CONFIRMATION_FREEZE = REPO / "integrations/openai_agents/V16_CONFIRMATION_FREEZE_20261006.json"
CONFIRMATION_EXPERIMENT_ID = "openai-repo-diagnostic-v16-sealed-confirmation-01"
CONFIRMATION_REPEATS = 5
CONFIRMATION_MAX_API_REQUESTS = 520
CONFIRMATION_MAX_SUMMARY_CALLS_PER_SAMPLE = 2
CONFIRMATION_ACCEPTANCE_LINE = (
    "per task, the plugin arm's strict track is at least the baseline's (the four conditions "
    "judged on the rendered line) AND the paired complete provider-token saving is positive for "
    "a majority of pairs AND the batch mean is positive. The verdict is decided on every pair of "
    "the frozen matrix; the trajectory-matched subset and the per-arm model-call distribution "
    "are reported for explanation only and never substitute for it. Failures, cap hits, retries "
    "and summary requests are all charged to their arm."
)


def verify_confirmation_freeze() -> dict[str, Any]:
    if not CONFIRMATION_FREEZE.is_file():
        raise SystemExit(f"the confirmation freeze is missing: {CONFIRMATION_FREEZE}")
    freeze = json.loads(CONFIRMATION_FREEZE.read_text(encoding="utf-8"))
    digest = _sha256(CONFIRMATION_FREEZE)
    if str(freeze.get("status")) != "FROZEN":
        raise SystemExit("the confirmation freeze is not FROZEN")
    if str(freeze.get("purpose")) != "sealed_confirmation":
        raise SystemExit("the confirmation freeze does not declare purpose=sealed_confirmation")
    if freeze.get("citable_as_saving") is not False or (
        freeze.get("citable_as_quality_equivalence") is not False
    ):
        raise SystemExit("the confirmation freeze must keep both citation flags false")
    matrix = freeze["matrix"]
    sealed = freeze["task_set"]
    if (
        list(matrix["arms"]) != list(PILOT_METHODS)
        or int(matrix["repeats"]) != CONFIRMATION_REPEATS
        or int(matrix["max_api_requests"]) != CONFIRMATION_MAX_API_REQUESTS
        or int(matrix["samples"]) != len(sealed) * CONFIRMATION_REPEATS * len(PILOT_METHODS)
    ):
        raise SystemExit("the runner matrix differs from the frozen confirmation matrix")
    if int(freeze["host_configuration"]["summary_cap_per_sample"]) != (
        CONFIRMATION_MAX_SUMMARY_CALLS_PER_SAMPLE
    ):
        raise SystemExit("the frozen per-sample summary cap differs from the implemented one")
    drifted = [
        relative
        for relative, expected in freeze["host_configuration"]["modules"].items()
        if not (REPO / relative).is_file() or _sha256(REPO / relative) != str(expected)
    ]
    if drifted:
        raise SystemExit("host configuration modules changed since the freeze: " + ", ".join(drifted))
    if freeze["acceptance_line"]["line"] != CONFIRMATION_ACCEPTANCE_LINE:
        raise SystemExit("the frozen acceptance line differs from the implemented one")
    for relative, expected in freeze["source_of_record"].items():
        if not (REPO / relative).is_file() or _sha256(REPO / relative) != str(expected):
            raise SystemExit(f"source-of-record drift: {relative}")
    return {
        "path": str(CONFIRMATION_FREEZE.relative_to(REPO)).replace("\\", "/"),
        "sha256": digest,
        "status": freeze["status"],
        "purpose": freeze["purpose"],
        "matrix": matrix,
        "task_set": list(sealed),
        "modules": dict(freeze["host_configuration"]["modules"]),
        "summary_cap_per_sample": int(freeze["host_configuration"]["summary_cap_per_sample"]),
        "acceptance_line": freeze["acceptance_line"],
        "report_only_items": list(freeze.get("report_only_items") or []),
        "source_of_record": dict(freeze["source_of_record"]),
        "stop_conditions": list(freeze["stop_conditions"]),
        "report_requirements": list(freeze["report_requirements"]),
    }


def confirmation_arithmetic(freeze: dict[str, Any]) -> dict[str, Any]:
    samples = len(freeze["task_set"]) * CONFIRMATION_REPEATS * len(PILOT_METHODS)
    measured = 3.67  # pilot 02: 99 requests over 27 samples, retries included
    expected = int(round(samples * measured))
    worst_calls = samples * (registry.EXPECTED_MODEL_CALLS + live.RETRY_BUDGET_PER_SAMPLE)
    native_samples = len(freeze["task_set"]) * CONFIRMATION_REPEATS
    summary_worst = native_samples * CONFIRMATION_MAX_SUMMARY_CALLS_PER_SAMPLE
    worst = worst_calls + summary_worst
    return {
        "samples": samples,
        "measured_requests_per_sample": measured,
        "expected_requests": expected,
        "worst_case_model_calls": worst_calls,
        "summary_calls_worst_case": summary_worst,
        "worst_case_requests": worst,
        "cap": CONFIRMATION_MAX_API_REQUESTS,
        "headroom_factor_over_worst_case": round(CONFIRMATION_MAX_API_REQUESTS / worst, 2),
        "headroom_at_least_1_3": CONFIRMATION_MAX_API_REQUESTS >= 1.3 * worst,
        "derivation": (
            "expected: 45 samples x 3.67 measured requests per sample (pilot 02, retries "
            "included) = 167. worst case: 45 samples x the frozen per-sample shape of six reads, "
            "one answer and one retry = 360 model calls, plus 15 native samples x the frozen "
            "summary cap of 2 = 30 summary requests, so 390; the cap is 520, which is 1.33x the "
            "worst case and 3.1x the expectation"
        ),
    }


def sealed_task_ids() -> tuple[str, ...]:
    previous = registry.set_active("sealed")
    try:
        return tuple(registry.task_ids())
    finally:
        registry.set_active(previous)


def run_confirmation(
    args: list[str],
    *,
    inner_factory: Any | None = None,
    freeze_override: dict[str, Any] | None = None,
) -> int:
    """The sealed confirmation batch: 3 sealed tasks x 5 repeats x 3 arms, same host config."""

    freeze = freeze_override if freeze_override is not None else verify_confirmation_freeze()
    amendment = verify_amendment()
    if not ("--confirm-send-public-source" in args or "--confirm-send-synthetic-data" in args):
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    arithmetic = confirmation_arithmetic(freeze)
    if not arithmetic["headroom_at_least_1_3"]:
        raise SystemExit("the confirmation cap does not keep a 1.3x margin over the worst case")
    previous_active = registry.set_active("sealed")
    try:
        problems = registry.all_problems()
        if any(problems.values()):
            raise SystemExit(f"the sealed registration does not verify: {problems}")
        cleaned = forced_args(args)
        cleaned = _replace_option(cleaned, "--methods", ",".join(PILOT_METHODS))
        cleaned += [
            "--repeats",
            str(CONFIRMATION_REPEATS),
            "--scenarios",
            ",".join(registry.task_ids()),
            "--experiment-id",
            CONFIRMATION_EXPERIMENT_ID,
            "--max-api-requests",
            str(CONFIRMATION_MAX_API_REQUESTS),
            "--max-summary-calls",
            str(CONFIRMATION_MAX_SUMMARY_CALLS_PER_SAMPLE),
        ]
        parsed = base.build_parser().parse_args(cleaned)
        root = Path(parsed.out) / parsed.experiment_id
        budget, _ = base.resolve_budget(parsed)
        contracts = contract_table()
        original = {
            "scenarios": base.SCENARIOS,
            "build_case": base.build_case,
            "disclosure": base.SYNTHETIC_DISCLOSURE,
            "chain_registry": chain.registry,
        }
        base.SCENARIOS = tuple(registry.task_ids())
        base.build_case = lambda scenario, repeat: build_case(scenario, repeat)
        base.SYNTHETIC_DISCLOSURE = DISCLOSURE
        chain.registry = registry
        status = 0
        stopped_early = False
        stop_reason = ""
        shared: dict[str, Any] = {}
        inner_wiring = (
            live.inner_model_wiring(inner_factory(shared))
            if inner_factory is not None
            else _null_wiring()
        )
        try:
            with chain.chain_wiring():
                with inner_wiring:
                    with v8.evidence_wiring(
                        root,
                        int(budget.hard_limit_tokens) * 4,
                        recorder_factory=chain.MultitaskRecorder,
                    ):
                        with live.structured_wiring(
                            root,
                            contracts=contracts,
                            state=shared,
                            check_request_cap=True,
                        ):
                            status = base.main(cleaned)
        except live.RequestCapReached as error:
            stopped_early = True
            stop_reason = str(error)
            status = 3
            print(f"confirmation stopped: {stop_reason}")
        finally:
            base.SCENARIOS = original["scenarios"]
            base.build_case = original["build_case"]
            base.SYNTHETIC_DISCLOSURE = original["disclosure"]
            chain.registry = original["chain_registry"]
        manifest = finalise_pilot_manifest(
            root,
            freeze=freeze,
            amendment=amendment,
            arithmetic=arithmetic,
            status=status,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            arm_set=tuple(PILOT_METHODS),
            repeat_count=CONFIRMATION_REPEATS,
            summary_cap=CONFIRMATION_MAX_SUMMARY_CALLS_PER_SAMPLE,
            kind="openai_agents_v16_sealed_confirmation",
            purpose="sealed_confirmation",
        )
        report = manifest["pilot_report"]
        print(
            f"samples={manifest['batch_report']['samples']} "
            f"pooled_mean_saving={report['pooled']['mean_saving']} "
            f"positive_pairs={report['pooled']['positive_pairs']}/{report['pooled']['paired_n']} "
            f"track_a_all_tasks={report['track_a_all_tasks']} met={report['met']} "
            f"api_calls={manifest['api_calls']}/{CONFIRMATION_MAX_API_REQUESTS}"
        )
        print(f"manifest: {root / 'manifest.json'}")
        return int(status)
    finally:
        registry.set_active(previous_active)


def confirmation_draft_freeze() -> dict[str, Any]:
    """In-memory description for the zero-API rehearsal of the confirmation batch."""

    draft = pilot_draft_freeze()
    draft["path"] = "<draft: the confirmation freeze is written after the rehearsal>"
    draft["purpose"] = "sealed_confirmation"
    draft["task_set"] = list(sealed_task_ids())
    draft["matrix"] = {
        "arms": list(PILOT_METHODS),
        "repeats": CONFIRMATION_REPEATS,
        "samples": len(draft["task_set"]) * CONFIRMATION_REPEATS * len(PILOT_METHODS),
        "max_api_requests": CONFIRMATION_MAX_API_REQUESTS,
    }
    draft["acceptance_line"] = {"line": CONFIRMATION_ACCEPTANCE_LINE}
    return draft


def run_confirmation_stub(args: list[str]) -> int:
    """Zero-API rehearsal of the confirmation batch: 45 samples on the sealed tasks."""

    originals = {
        "client": base.AsyncOpenAI,
        "provider_model": base.OpenAIChatCompletionsModel,
    }
    base.AsyncOpenAI = lambda **kwargs: _NoNetworkClient()
    base.OpenAIChatCompletionsModel = _NoProviderModel
    placeholder_installed = _placeholder_key_installed()
    sealed = sealed_task_ids()

    def proposal(task_id: str) -> str:
        previous = registry.set_active("sealed")
        try:
            entry = registry.task(task_id)
        finally:
            registry.set_active(previous)
        contract = entry["contract"]
        facts = [str(fact) for fact in contract["required_facts"]]
        return json.dumps(
            {
                "issue": str(contract["issue_value"]),
                "cause": ("cause " + " ".join(facts[:2]) + " " + entry["cause_token"]).strip(),
                "fix": ("fix " + " ".join(facts[2:]) + " " + entry["fix_token"]).strip(),
                "facts": facts,
            }
        )

    def factory(shared: dict[str, Any]):
        def build(inner: Any, request_budget: Any, **kwargs: Any):
            key = shared.get("key")
            task_id = str(key[0]) if key else sealed[0]
            repeat = int(key[1]) if key else 0
            previous = registry.set_active("sealed")
            try:
                case = build_case(task_id, repeat)
            finally:
                registry.set_active(previous)
            return live.ScriptedStubModel(
                case, request_budget=request_budget, final_text_value=proposal(task_id)
            )

        return build

    status = 0
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cleaned = ["--confirm-send-public-source", "--out", tmp]
            status = run_confirmation(
                cleaned, inner_factory=factory, freeze_override=confirmation_draft_freeze()
            )
            parsed = base.build_parser().parse_args(
                forced_args(["--confirm-send-public-source"]) + [
                    "--repeats",
                    str(CONFIRMATION_REPEATS),
                    "--experiment-id",
                    CONFIRMATION_EXPERIMENT_ID,
                    "--out",
                    tmp,
                    "--max-api-requests",
                    str(CONFIRMATION_MAX_API_REQUESTS),
                    "--max-summary-calls",
                    str(CONFIRMATION_MAX_SUMMARY_CALLS_PER_SAMPLE),
                ]
            )
            root = Path(parsed.out) / parsed.experiment_id
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            report = manifest["pilot_report"]
            print(
                "confirmation rehearsal: "
                f"samples={manifest['batch_report']['samples']} api_calls={manifest['api_calls']} "
                f"pooled_mean_saving={report['pooled']['mean_saving']} "
                f"positive_pairs={report['pooled']['positive_pairs']}/{report['pooled']['paired_n']} "
                f"placeholder_key={placeholder_installed}"
            )
            for method, entry in manifest["failure_decomposition"].items():
                print(
                    f"  {method}: strict={entry['strict_pass']}/{entry['samples']} "
                    f"retries={entry['retries']} summary={entry['summary_calls']} "
                    f"requests={entry['requests']} failures={entry['failures']}"
                )
            for task_id, entry in report["per_task"].items():
                print(
                    f"  {task_id}: mean={entry['mean_saving']} strict={entry['strict_track']} "
                    f"plugin_at_least_baseline={entry['strict_track_plugin_at_least_baseline']}"
                )
    finally:
        base.AsyncOpenAI = originals["client"]
        base.OpenAIChatCompletionsModel = originals["provider_model"]
    return int(status)


def run_paid(args: list[str], *, inner_factory: Any | None = None) -> int:
    """The single-arm payload acquisition batch (attempts 01-03).

    ``inner_factory`` exists for the zero-API rehearsal: when it is given, it is called with the
    shared per-sample state and must return the replacement for ``base.RecordingRetryModel``, so
    the innermost provider model is a deterministic local model before the evidence wiring. The
    whole paid path - request cap guard, evidence recorder, renderer, retry ledger and the
    one-pass manifest finalisation - then runs without contacting a provider.
    """
    freeze, amendment, sources, checks = preflight()
    if "--confirm-send-public-source" not in args:
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    cleaned = forced_args(args)
    parsed = base.build_parser().parse_args(cleaned)
    root = Path(parsed.out) / parsed.experiment_id
    budget, _ = base.resolve_budget(parsed)
    contracts = contract_table()
    original = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
        "chain_registry": chain.registry,
    }
    base.SCENARIOS = tuple(registry.task_ids())
    base.build_case = lambda scenario, repeat: build_case(scenario, repeat)
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    chain.registry = registry
    status = 0
    stopped_early = False
    stop_reason = ""
    shared: dict[str, Any] = {}
    # ``inner_factory(shared)`` returns the replacement for ``base.RecordingRetryModel``; it is
    # given the shared per-sample state so it can resolve the current sample's task.
    inner_wiring = (
        live.inner_model_wiring(inner_factory(shared))
        if inner_factory is not None
        else _null_wiring()
    )
    try:
        with chain.chain_wiring():
            with inner_wiring:
                with v8.evidence_wiring(
                    root,
                    int(budget.hard_limit_tokens) * 4,
                    recorder_factory=chain.MultitaskRecorder,
                ):
                    with live.structured_wiring(
                        root,
                        contracts=contracts,
                        state=shared,
                        check_request_cap=True,
                    ):
                        status = base.main(cleaned)
    except live.RequestCapReached as error:
        stopped_early = True
        stop_reason = str(error)
        status = 3
        print(f"batch stopped: {stop_reason}")
    finally:
        base.SCENARIOS = original["scenarios"]
        base.build_case = original["build_case"]
        base.SYNTHETIC_DISCLOSURE = original["disclosure"]
        chain.registry = original["chain_registry"]
    manifest = finalise_manifest(
        root,
        freeze=freeze,
        amendment=amendment,
        sources=sources,
        status=status,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
    )
    report = manifest["batch_report"]
    print(
        f"samples={report['samples']} render_accepted={report['render_accepted_count']} "
        f"over_160={report['post_render_over_160_count']} retries={report['retry_count']} "
        f"api_calls={manifest['api_calls']}/{MAX_API_REQUESTS}"
    )
    print(f"manifest: {root / 'manifest.json'}")
    print(f"registration: {checks['registration']['task_ids']}")
    if not manifest.get("prompt_adaptation_complete"):
        raise SystemExit(
            "the frozen prompt no longer contains the sentences the structured contract "
            "replaces: the batch is recorded but the contract was contradictory, so stop"
        )
    return int(status)


# ------------------------------------------------------------------------ mode: --probe
PROBE_ARTIFACT = REPO / "integrations/openai_agents/V18_CAPABILITY_PROBE_20261010.json"
#: Provider errors that mean "the request itself was refused", not "the batch had a bad turn".
PROVIDER_REFUSAL_ERRORS = (
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "UnprocessableEntityError",
    "RateLimitError",
    "InternalServerError",
    "APIConnectionError",
    "APITimeoutError",
)


def run_probe(artifact: Path, args: list[str]) -> int:
    """One real request through the batch's own wiring, before the batch itself.

    The lesson from attempt 1: the zero-API plan and stub paths cannot see a provider
    capability gap, because they never contact the provider. Every new host configuration
    therefore spends one request first, on the real first call of the first task, and stops
    cleanly if the provider refuses it.
    """

    freeze, amendment, sources, preflight_checks = preflight()
    if "--confirm-send-public-source" not in args:
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY or OPENAI_API_KEY is not set")
    contracts = contract_table()
    task_id = registry.task_ids()[0]
    cleaned = forced_args(args, confirm=True)
    parsed = base.build_parser().parse_args(cleaned)
    budget, _thresholds = base.resolve_budget(parsed)
    case = build_case(task_id, 0)
    originals = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
        "chain_registry": chain.registry,
    }
    base.SCENARIOS = (task_id,)
    base.build_case = lambda scenario, repeat: build_case(scenario, repeat)
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    chain.registry = registry
    request_budget = base.RequestBudget(1)
    shared: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        async def probe() -> dict[str, Any]:
            client = base.AsyncOpenAI(
                api_key=api_key,
                base_url=parsed.base_url,
                timeout=parsed.timeout,
                max_retries=0,
            )
            try:
                return await _probe_call(
                    case,
                    client,
                    parsed,
                    budget,
                    request_budget,
                    root,
                    contracts,
                    shared,
                )
            finally:
                await client.close()

        result = asyncio.run(probe())
    base.SCENARIOS = originals["scenarios"]
    base.build_case = originals["build_case"]
    base.SYNTHETIC_DISCLOSURE = originals["disclosure"]
    chain.registry = originals["chain_registry"]
    provider_error = str(result.get("error_type") or "")
    # A probe passes when the provider answered at all: the run stops after its single turn, so
    # "max turns exceeded" is the expected, successful shape, while an API error is a refusal.
    provider_ok = int(result.get("model_calls", 0) or 0) >= 1 and provider_error not in (
        PROVIDER_REFUSAL_ERRORS
    )
    payload = {
        "schema": "openai_agents_v16_capability_probe",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": (
            "one real request through the batch's own wiring, taken before the acquisition "
            "batch, because the zero-API plan and stub paths cannot see a provider capability "
            "gap; a refusal stops the batch cleanly for the price of one request"
        ),
        "task": task_id,
        "api_calls": int(result.get("api_request_attempts", 0) or 0),
        "api_calls_note": (
            "the frozen sample row records provider attempts as api_request_attempts; an earlier "
            "version of this payload read a non-existent 'api_calls' key and reported 0"
        ),
        "model_calls": int(result.get("model_calls", 0) or 0),
        "provider_error": provider_error,
        "provider_error_message": str(result.get("error_message") or ""),
        "provider_ok": provider_ok,
        "freeze": freeze,
        "freeze_amendment": amendment,
        "source_of_record_verified": sources,
        "registration": preflight_checks["registration"],
        "tool_calls": int(result.get("tool_calls", 0)),
        "rendered_conditions": dict(result.get("render_conditions") or {}),
        "ok": provider_ok,
        "batch_started": False,
    }
    _write_artifact(artifact, payload)
    print(f"probe artifact: {artifact}")
    print(
        f"api_calls={payload['api_calls']} ok={payload['ok']} "
        f"error={payload['provider_error'] or 'none'} tool_calls={payload['tool_calls']}"
    )
    return 0 if payload["ok"] else 1


async def _probe_call(
    case: Any,
    client: Any,
    parsed: Any,
    budget: Any,
    request_budget: Any,
    root: Path,
    contracts: dict[str, dict[str, Any]],
    shared: dict[str, Any],
) -> dict[str, Any]:
    """The first real model call of one sample, through the production model chain."""

    with chain.chain_wiring():
        with v8.evidence_wiring(
            root, int(budget.hard_limit_tokens) * 4, recorder_factory=chain.MultitaskRecorder
        ):
            with live.structured_wiring(root, contracts=contracts, state=shared):
                context_filter = base._build_filter(
                    "none",
                    case=case,
                    client=client,
                    model_name=parsed.model,
                    budget=budget,
                    fixed_reserved_tokens=parsed.fixed_reserved_tokens,
                    max_summary_tokens=parsed.max_summary_tokens,
                    max_summary_calls=0,
                    timeout=parsed.timeout,
                    request_budget=request_budget,
                    trigger_policy=parsed.trigger_policy,
                )
                return await base.run_case(
                    case,
                    method="none",
                    client=client,
                    model_name=parsed.model,
                    request_budget=request_budget,
                    budget=budget,
                    fixed_reserved_tokens=parsed.fixed_reserved_tokens,
                    max_turns=1,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    max_api_retries=0,
                    retry_base_delay=0.0,
                    input_cost_per_million=parsed.input_cost_per_million,
                    output_cost_per_million=parsed.output_cost_per_million,
                    context_filter=context_filter,
                )


# ------------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    artifact = None
    if "--artifact" in args:
        index = args.index("--artifact")
        if index + 1 >= len(args):
            raise SystemExit("--artifact needs a path")
        artifact = Path(args[index + 1])
        del args[index : index + 2]
    if "--plan" in args:
        return run_plan(artifact or PLAN_ARTIFACT, args)
    if "--stub" in args:
        return run_stub(artifact or STUB_ARTIFACT, args)
    if "--probe" in args:
        return run_probe(artifact or PROBE_ARTIFACT, args)
    if "--pilot" in args:
        return run_pilot(args)
    if "--pilot-stub" in args:
        return run_pilot_stub(args)
    if "--confirmation" in args:
        return run_confirmation(args)
    if "--confirmation-stub" in args:
        return run_confirmation_stub(args)
    return run_paid(args)


if __name__ == "__main__":
    raise SystemExit(main())
