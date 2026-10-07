"""New-ID, uncompressed, full-capture CrewAI payload acquisition.

Default mode is a fake OpenAI-compatible provider. API mode is fail-closed behind a
write-once source freeze, explicit synthetic-data flag and global 48-request cap.
Acquisition output is never citable as saving or quality equivalence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openai import OpenAI

from experiments.runners import run_crewai_experiment as base
from experiments.commands import run_crewai_r17_confirmation_acquisition as prior
from experiments.runners.crewai_capture_v19 import CapturedOpenAICompatCrewAILLM

ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r19_acquisition.json"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R19_FULLCAPTURE_ACQUISITION_01.json"
TASK_IDS = ("quorum_snapshot_r19", "cache_purge_r19", "certificate_rollover_r19")
MAX_API_REQUESTS = 48
PINNED = (
    # The runner itself is NOT in this set: it performs the check, so pinning it here would
    # turn every improvement of it into a self-detected drift.  Every module the capture path
    # depends on is pinned, and the freeze's own source_sha256 map (which does name this
    # file) is still key-set checked against PINNED below.
    "experiments/runners/crewai_capture_v19.py",
    "experiments/audits/audit_crewai_r19_acquisition.py",
    "experiments/commands/run_crewai_r17_confirmation_acquisition.py",
    "experiments/runners/run_crewai_experiment.py",
    "experiments/runners/run_crewai_handoff_v16.py",
    "experiments/runners/run_crewai_handoff_v4.py",
    "experiments/runners/crewai_loop_guard_v18.py",
    "experiments/runners/crewai_semantic_equivalence_v8.py",
    "experiments/runners/crewai_handoff_v17_tasks.py",
    "tasks/stage5_autogen/natural_tasks_r19_acquisition.json",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeCompletions:
    def __init__(self, responses: list[str]):
        self.responses = iter(responses)

    def create(self, **request):
        response = next(self.responses)
        input_tokens = max(1, sum(len(str(m.get("content", ""))) for m in request["messages"]) // 4)
        output_tokens = max(1, len(response) // 4)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens),
        )


def _amendment_path(freeze_path: Path) -> Path:
    return freeze_path.with_name(freeze_path.stem + "_FREEZE_AMENDMENT_20261006.json")


def check_freeze(freeze_path: Path) -> dict:
    if not freeze_path.is_file():
        raise SystemExit(f"missing write-once freeze: {freeze_path}")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    # The amendment is the authority for the current pins and the effective cap: the freeze
    # file itself is never rewritten, and a source that changed after it was written is
    # accepted only when its digest is exactly the registered one (fail-closed).
    amendment_path = _amendment_path(freeze_path)
    pins = dict(freeze.get("source_sha256") or {})
    effective_cap = MAX_API_REQUESTS
    if amendment_path.is_file():
        amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
        pins.update(amendment.get("pinned_sources_after_amendment") or {})
        effective_cap = int(
            amendment.get("effective_max_api_requests", effective_cap)
        )
    if freeze.get("purpose") != "payload_acquisition_only" or int(
        freeze.get("max_api_requests", -1)
    ) > 48:
        raise SystemExit("freeze purpose or request hard cap mismatch")
    if tuple(freeze.get("tasks") or ()) != TASK_IDS or freeze.get("methods") != ["none"]:
        raise SystemExit("freeze task/arm mismatch")
    if freeze.get("citable_as_saving") is not False:
        raise SystemExit("acquisition must be non-citable")
    if freeze.get("citable_as_quality_equivalence") is not False:
        raise SystemExit("acquisition must not be citable as quality equivalence")
    frozen_pin_names = set(freeze.get("source_sha256") or {})
    allowed_extra = {"experiments/commands/run_crewai_r19_acquisition.py"}
    if not set(PINNED) <= frozen_pin_names or not frozen_pin_names <= (
        set(PINNED) | allowed_extra
    ):
        raise SystemExit(
            "freeze source pin set incomplete: expected the dependency set plus the "
            f"runner itself, got {sorted(frozen_pin_names)}"
        )
    for relative in PINNED:
        if sha(ROOT / relative) != pins.get(relative):
            raise SystemExit(
                f"frozen source drift (unregistered digest): {relative}"
            )
    freeze["_effective_max_api_requests"] = effective_cap
    freeze["_amendment_path"] = str(amendment_path)
    freeze["_pins_after_amendment"] = dict(pins)
    return freeze


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--freeze", type=Path, default=FREEZE)
    parser.add_argument("--out", type=Path, default=ROOT / "runs/stage5-crewai")
    parser.add_argument("--experiment-id", default="crewai-r19-fullcapture-acquisition-mock-01")
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    args = parser.parse_args(argv)
    freeze = check_freeze(args.freeze)
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires explicit synthetic-data flag")
    if args.mode == "api" and args.experiment_id.endswith("mock-01"):
        raise SystemExit("API mode requires a distinct new experiment ID")
    if args.mode == "api" and args.experiment_id != freeze.get("batch"):
        raise SystemExit("API experiment ID must match the frozen batch ID")
    key = base.os.getenv("OPENAI_API_KEY") or base.os.getenv("DEEPSEEK_API_KEY") or ""
    if args.mode == "api" and not key:
        raise SystemExit("API key absent")
    output = args.out / args.experiment_id
    if output.exists():
        raise SystemExit(f"write-once output already exists: {output}")
    tasks = prior.load_tasks(TASK_FILE)
    if tuple(t["task_id"] for t in tasks) != TASK_IDS:
        raise SystemExit("task registry order mismatch")
    options = argparse.Namespace(provider_soft=1200, provider_hard=3000, provider_target=900,
                                 soft_limit=0, hard_limit=0, target=0,
                                 fixed_reserved_tokens=300, max_output_tokens=512,
                                 model="deepseek-v4-flash")
    if freeze.get("model") != options.model or freeze.get("max_output_tokens") != options.max_output_tokens:
        raise SystemExit("model or output cap differs from freeze")
    budget, calibration = base.resolve_budget(options)
    effective_cap = int(freeze.get("_effective_max_api_requests", MAX_API_REQUESTS))
    request_budget = base.RequestBudget(effective_cap)
    api_client = (OpenAI(api_key=key, base_url="https://api.deepseek.com", max_retries=0)
                  if args.mode == "api" else None)
    # The manifest records the pins it RUN WITH, excluding the runner itself: a manifest
    # cannot contain a stable digest of the file that writes it, so including it would make
    # every later improvement look like a stale record.
    recorded_pins = {
        name: digest
        for name, digest in (freeze.get("_pins_after_amendment") or {}).items()
        if name != "experiments/commands/run_crewai_r19_acquisition.py"
    }
    manifest = {
        "protocol": "crewai-r19-fullcapture-acquisition",
        "purpose": "payload_acquisition_only", "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "tasks": list(TASK_IDS), "methods": ["none"], "repeats": 1,
        "mode": args.mode, "max_api_requests": effective_cap,
        "effective_max_api_requests": effective_cap,
        "freeze_ceiling_max_api_requests": int(freeze.get("max_api_requests", 0)),
        "amendment_path": str(freeze.get("_amendment_path", "")),
        "freeze_sha256": sha(args.freeze), "source_sha256": freeze["source_sha256"],
        "pinned_sources_after_amendment": recorded_pins,
        "budget": calibration.as_manifest(), "model": options.model,
        "capture_schema": ["full_input_messages", "raw_model_output", "normalized_model_output",
                           "response_action_candidate", "host_parsed_action", "guard_allowance",
                           "host_executed_tool", "request_slot", "input_tokens", "output_tokens",
                           "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
                           "cache_tokens_source", "utc_started", "utc_finished",
                           "off_peak_window"],
    }
    output.mkdir(parents=True)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    old_make, old_task = prior._make_llm, prior.TASK_FILE
    captured: list[CapturedOpenAICompatCrewAILLM] = []

    def make_llm(mode: str, task: dict, stage: int, client: Any,
                 request_budget: base.RequestBudget, options: argparse.Namespace,
                 budget: Any, trace: list[str]):
        responses = prior._mock_responses(task, stage) if mode == "mock" else []
        selected_client = (SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(responses)))
                           if mode == "mock" else client)
        llm = CapturedOpenAICompatCrewAILLM(
            client=selected_client, model=options.model, request_budget=request_budget,
            max_output_tokens=options.max_output_tokens, thinking_mode="disabled",
            executed_tool_trace=trace, frozen_tool_names=prior._stage_tools(task, stage),
        )
        captured.append(llm)
        return llm

    try:
        prior._make_llm = make_llm
        prior.TASK_FILE = TASK_FILE
        with (output / "results.jsonl").open("w", encoding="utf-8") as handle:
            for task in tasks:
                before = len(captured)
                row = prior.run_case(task, 0, args.mode, api_client, request_budget, options, budget)
                stages = captured[before:]
                for llm in stages:
                    llm.bind_executed_tools()
                row["full_attempt_capture"] = [record for llm in stages for record in llm.capture]
                row["capture_complete"] = len(row["full_attempt_capture"]) == row["api_request_attempts"]
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                if request_budget.used >= MAX_API_REQUESTS or row["error"]:
                    break
    finally:
        prior._make_llm = old_make
        prior.TASK_FILE = old_task
        if api_client is not None:
            api_client.close()
    print(f"recorded {len(captured)} role clients; requests {request_budget.used}/{effective_cap}"
          f" (freeze ceiling {freeze.get('max_api_requests')})")


if __name__ == "__main__":
    run()
