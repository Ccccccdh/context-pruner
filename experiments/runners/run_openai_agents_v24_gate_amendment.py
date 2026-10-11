"""The v24 gate amendment, mounted on the frozen confirmation runner without editing it.

What this module is, and why it exists
--------------------------------------
``V24_RECALIBRATION_GATE_AMENDMENT_20261010.json`` replaces the recalibration's 70 percent
first-attempt acceptance line with an operative criterion and records the residual risk it accepts.
That amendment has to reach the batch runner as a **fail-closed** input: the runner refuses to send a
paid request unless the amendment file on disk is the pin, is byte the pin, and the gate the batch
would run under was re-run under the amendment.

The frozen confirmation runner (``run_openai_agents_v24_length_compliance_k1.py``) cannot carry that
check itself, and this is a property of the round rather than a preference:

* its bytes are pinned inside ``V24_LENGTH_COMPLIANCE_K1_MODULE_REVISIONS_20261010.json``
  (``338a6415e5934b9789a92ec53d103b9b9280385640b8d8e1f95439e5e1c9d592``);
* that ledger is write-once by its own construction, and the frozen gate fails closed on any module
  whose digest is absent from it or differs from the digest recorded there;
* editing the runner to admit the amendment would therefore void the module pin, and the only ways
  to restore it would be to edit the ledger, the freeze and the gate artifact - all of them already
  written down and none of them this round's to rewrite.

So the amendment is carried the way the runner's own assertion expects it to be: by a **new** file that
imports the frozen runner unchanged and mounts it.  ``_paid_path_pins_this_runner_or_delegates_it``
is that expectation written into the frozen gate, and the manifest records this delegate's digest,
so the code that actually ran is attached to the batch.

What it does
------------
``--gate-amendment``  re-run the frozen runner's own zero-API gate under this amendment, add this
                      round's reference assertions, and write the amendment gate artifact once.
``--paid``            verify the amendment pin, verify the amendment gate, optionally install the
                      provider key, run the batch through the frozen paid path, and add the
                      amendment block to the finalised manifest in the same pass.
``--probe``           print the amendment pin and its structural checks without writing anything.

None of these modes edits a frozen artifact.  The original v24 gate artifact is read, never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import (  # noqa: E402
    run_openai_agents_v24_length_compliance_k1 as runner,
)

# ------------------------------------------------------------------------------ this round's pins
#: The batch this delegate runs.  A new id: the frozen freeze's own id names a batch that was never
#: sent, so reusing it would make the two runs indistinguishable in the run directory.
EXPERIMENT_ID = "openai-repo-diagnostic-v24-length-compliance-k1-02"
DELEGATE_MODULE = "experiments/runners/run_openai_agents_v24_gate_amendment.py"

#: The integrator's ruling.  The path and the digest are both pins, and both are checked against the
#: file on disk before anything else happens: a missing file, a renamed file or one byte of drift
#: refuses the batch.
AMENDMENT = ROOT / "integrations/openai_agents/V24_RECALIBRATION_GATE_AMENDMENT_20261010.json"
AMENDMENT_SHA256 = "143250ba9d8899b8d744d8c9d06b3aad8b68483c2f6eeba49e389936334afd72"
AMENDMENT_ID = "openai-agents-v24-gate-amendment-01"
AMENDMENT_AMENDS = (
    "integrations/openai_agents/V24_LENGTH_COMPLIANCE_FIX_PLAN_20261010.json"
)

#: The frozen artifacts this amendment sits on top of, and the amendment's own output.  Nothing in
#: this list is written by any mode of this module.
ORIGINAL_GATE = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_ZERO_API_GATE_20261010.json"
)
ORIGINAL_GATE_RUN1_COPY = Path(
    r"C:\Users\LENOVO\v21-artifact-preservation"
    r"\V24_LENGTH_COMPLIANCE_K1_ZERO_API_GATE_RUN1_20261010.json"
)
AMENDMENT_GATE_OUT = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_AMENDED_GATE_20261010.json"
)
FREEZE = ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_CONFIRMATION_FREEZE_20261010.json"

#: The pre-registered wording discipline, imported rather than restated.
REQUIRED_WORDING = runner.REQUIRED_WORDING
FORBIDDEN_WORDING = tuple(runner.FORBIDDEN_WORDING)

#: The frozen runner itself.  Both digests are recomputed from disk at run time; the freeze's pin is
#: the one the gate recorded, and a mismatch is a refusal rather than a warning.
RUNNER_MODULE = "experiments/runners/run_openai_agents_v24_length_compliance_k1.py"

#: The amendment's four operative clauses, each mapped to the machine check that decides it.  The
#: gate is the amendment's own wording; the evaluation column says when and how this round decides
#: it, so a reader can see that no clause is decided by prose.
DECISION_CLAUSES = (
    "zero render failures, zero truncations, zero dropped fields",
    "every arm's strict track at or above the baseline",
    "every arm's semantic correctness at or above the baseline",
    "retry recovery rate at least 90 percent, measured at five of five in the recalibration",
)
SUPPORTED_CRITERIA: dict[str, dict[str, Any]] = {
    "zero_render_failures_zero_truncations_zero_dropped_fields": {
        "gate_clause": DECISION_CLAUSES[0],
        "decided_by": "the frozen runner's own gate assertions a, f and the paid batch's rows",
        "evaluated": "at the amended gate and again by the independent audit on the batch rows",
    },
    "every_arms_strict_track_at_or_above_the_baseline": {
        "gate_clause": DECISION_CLAUSES[1],
        "decided_by": "the per-task strict track recomputed from the rendered lines",
        "evaluated": "by the independent audit, per task and per arm",
    },
    "every_arms_semantic_correctness_at_or_above_the_baseline": {
        "gate_clause": DECISION_CLAUSES[2],
        "decided_by": "the task's own semantic endpoint recomputed from the rendered lines",
        "evaluated": "by the independent audit, per task and per arm",
    },
    "retry_recovery_rate_at_least_90_percent": {
        "gate_clause": DECISION_CLAUSES[3],
        "decided_by": (
            "the fraction of first-attempt rejections that the single billed retry repaired to an "
            "accepted answer, which the amendment measured at five of five in the recalibration"
        ),
        "evaluated": "by the independent audit, from the samples the batch actually wrote",
    },
}

#: What the amendment says must be reported.  Each entry is paired with where this round reports it,
#: so the gate can assert that the record covers every requirement rather than asserting the string.
REPORT_REQUIREMENTS_AND_WHERE = {
    "first_attempt_acceptance_overall_and_per_arm": (
        "the independent audit's first_attempt_acceptance block: overall, per arm, with the "
        "rejection-class decomposition and the per-sample retry labels"
    ),
    "retry_count_per_arm_and_per_pair_with_the_retry_label_of_every_pair": (
        "the audit's per_arm retry counts and its per-pair retrying_arm and retry labels"
    ),
    "as_paired_primary_and_symmetric_matched_prefix_co_primary_in_complete_provider_tokens_and_bytes": (
        "the audit's primary_criterion and co_reported blocks, in complete provider tokens and in "
        "context bytes"
    ),
    "strict_track_and_semantic_correctness_per_arm": (
        "the audit's per-arm strict track and semantic accuracy"
    ),
    "the_disclosure_that_this_is_the_second_run_of_the_same_sealed_task_set": (
        "the freeze, the registration and the manifest's v24_prior_batch_disclosure and "
        "v24_task_set_exhaustion blocks"
    ),
}

#: The default location of the provider key.  It is read into this process's environment only, and
#: is never written into any artifact, log or manifest by this module.
DEFAULT_KEY_FILE = Path(os.path.expanduser("~")) / ".dsh" / ".credentials.yaml"
KEY_NAMES = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")
KEY_REDACTION = "<redacted>"


# ------------------------------------------------------------------------------------ small tools
def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise SystemExit(
            f"refusing to overwrite the existing artifact {path}: write a new one instead"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def delegate_value() -> dict[str, Any]:
    """This module's own identity, recomputed from its bytes."""

    own = Path(__file__).resolve()
    return {
        "module": DELEGATE_MODULE,
        "sha256": sha256_of(own),
        "why_a_delegate": (
            "the frozen confirmation runner's bytes are pinned inside the confirmation module "
            "revision ledger, that ledger is write-once and the frozen gate fails closed on module "
            "drift, so the amendment is carried by a new file that imports the frozen runner "
            "unchanged and mounts it for the paid pass"
        ),
        "the_frozen_runner_is_not_edited": True,
    }


def amendment_block() -> dict[str, Any]:
    """The pin: where the ruling is, what it is hashed at, and that the file is byte the pin."""

    present = AMENDMENT.is_file()
    live = sha256_of(AMENDMENT) if present else ""
    payload: dict[str, Any] = {}
    if present:
        payload = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    return {
        "path": runner._relative(AMENDMENT),
        "sha256": live,
        "pinned_sha256": AMENDMENT_SHA256,
        "digest_matches_the_pin": bool(present and live == AMENDMENT_SHA256),
        "present": present,
        "amendment_id": str(payload.get("amendment_id", "")),
        "amends": str(payload.get("amends", "")),
        "decided_by": str(payload.get("decided_by", "")),
        "citable": payload.get("citable"),
        "api_requests_spent_when_written": payload.get("api_requests_spent_when_written"),
    }


def verify_amendment(*, require_gate: bool = True) -> dict[str, Any]:
    """Every check the amendment pin implies.  Any failed check is a refusal, not a warning.

    The checks are deliberately about this batch's own decision rather than about the file alone:

    * the amendment exists and is byte the pin;
    * the ruling's own machine-readable shape is the one this delegate's decision map was written
      for - the replaced line, the four clauses and the four required report items are read out of
      the artifact and compared, so an edit that changes the gate changes what is checked;
    * the delegate running is the delegate the amended gate recorded;
    * the frozen runner on disk still matches the digest the gate pinned, so a runner edited after
      the gate cannot be the one that sends the requests;
    * the amended gate exists, is green and allowed the paid run, references the original gate by
      digest, and carries every clause's check.
    """

    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    pin = amendment_block()
    record(
        "the_amendment_file_is_present_and_is_the_pinned_bytes",
        pin["present"] and pin["digest_matches_the_pin"],
        {
            "path": pin["path"],
            "sha256": pin["sha256"],
            "pinned_sha256": pin["pinned_sha256"],
        },
    )
    if not (pin["present"] and pin["digest_matches_the_pin"]):
        return {
            "ok": False,
            "checks": checks,
            "failed": [entry["check"] for entry in checks if not entry["ok"]],
            "amendment": pin,
        }

    payload = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    ruling = dict(payload.get("ruling") or {})
    clauses = [str(entry) for entry in ruling.get("the_gate_for_this_round_is_therefore") or []]
    replaced = str(ruling.get("the_70_percent_line_is_replaced_as_the_gate") or "")
    residual = str(ruling.get("the_residual_risk_stated_plainly") or "")
    forbidden = [str(entry) for entry in payload.get("what_may_still_not_be_done") or []]
    required = [str(entry) for entry in payload.get("required_in_the_confirmation_report") or []]
    record(
        "the_amendment_is_the_ruling_this_delegate_was_written_for",
        pin["amendment_id"] == AMENDMENT_ID
        and pin["amends"] == AMENDMENT_AMENDS
        and payload.get("citable") is False
        and int(payload.get("api_requests_spent_when_written", -1)) == 0,
        {
            "amendment_id": pin["amendment_id"],
            "amends": pin["amends"],
            "expected_amendment_id": AMENDMENT_ID,
            "expected_amends": AMENDMENT_AMENDS,
            "citable": payload.get("citable"),
            "api_requests_spent_when_written": payload.get("api_requests_spent_when_written"),
        },
    )
    record(
        "the_amendment_reads_out_the_four_operative_clauses_this_delegate_decides",
        clauses == list(DECISION_CLAUSES),
        {"clauses_in_the_amendment": clauses, "clauses_this_delegate_decides": list(DECISION_CLAUSES)},
    )
    record(
        "the_amendment_replaces_the_first_attempt_line_rather_than_meeting_it",
        "no longer the first attempt rate" in replaced
        and "frozen hard stop" in replaced
        and "retry repairs the answer" in replaced
        and "roughly half of the confirmation samples will spend the billed retry" in residual
        and "44 percent" in residual
        and "7 to 21 percentage points" in residual,
        {
            "the_70_percent_line_is_replaced_as_the_gate": replaced,
            "the_residual_risk_stated_plainly": residual,
        },
    )
    record(
        "the_amendment_records_what_may_still_not_be_done",
        any("weakening any literal" in entry for entry in forbidden)
        and any("dropping a task" in entry for entry in forbidden)
        and any("silently proceeding" in entry for entry in forbidden)
        and any("70 percent first attempt line" in entry for entry in forbidden),
        {"what_may_still_not_be_done": forbidden},
    )
    record(
        "every_report_item_the_amendment_requires_has_a_recorded_home_in_this_round",
        len(required) == len(REPORT_REQUIREMENTS_AND_WHERE)
        and all(
            key in REPORT_REQUIREMENTS_AND_WHERE
            for key in REPORT_REQUIREMENTS_AND_WHERE
        ),
        {
            "required_in_the_confirmation_report": required,
            "where_each_is_reported": REPORT_REQUIREMENTS_AND_WHERE,
        },
    )
    ledger = runner.load_revision_ledger()
    recorded_runner = str(ledger.get(RUNNER_MODULE, ""))
    live_runner = sha256_of(ROOT / RUNNER_MODULE)
    record(
        "the_frozen_runner_on_disk_is_still_the_module_the_confirmation_ledger_pins",
        bool(recorded_runner) and recorded_runner == live_runner,
        {
            "module": RUNNER_MODULE,
            "recorded_sha256": recorded_runner,
            "live_sha256": live_runner,
            "why": (
                "the amendment is carried outside the runner on purpose; if the runner's bytes "
                "moved, the batch would be run by code the gate did not pin"
            ),
        },
    )
    if not require_gate:
        return {
            "ok": all(entry["ok"] for entry in checks),
            "checks": checks,
            "failed": [entry["check"] for entry in checks if not entry["ok"]],
            "amendment": pin,
            "amended_gate": None,
            "delegate": delegate_value(),
        }

    gate_present = AMENDMENT_GATE_OUT.is_file()
    gate = json.loads(AMENDMENT_GATE_OUT.read_text(encoding="utf-8")) if gate_present else {}
    recorded = dict(gate.get("recorded_frozen_gate") or {})
    delegate = delegate_value()
    gate_checks = {
        "the_amended_gate_artifact_is_present": gate_present,
        "the_amended_gate_allowed_the_paid_run": gate.get("paid_run_allowed") is True,
        "the_amended_gate_is_zero_api": gate.get("api_calls") == 0,
        "the_amended_gate_pins_this_amendment_by_digest": str(recorded.get("amendment_sha256", ""))
        == AMENDMENT_SHA256,
        "the_amended_gate_reports_the_amendment_digest_it_read": str(
            ((gate.get("amendment_reference_assertion") or {}).get("amendment") or {}).get(
                "sha256", ""
            )
        )
        == AMENDMENT_SHA256,
        "the_amended_gate_references_the_original_gate_by_digest": (
            str(recorded.get("sha256", "")) == sha256_of(ORIGINAL_GATE)
            if ORIGINAL_GATE.is_file()
            else False
        ),
        "the_original_gate_recorded_paid_run_allowed": recorded.get("paid_run_allowed") is True,
        "the_amended_gate_pins_the_delegate_that_is_running_now": str(
            (gate.get("delegate") or {}).get("sha256", "")
        )
        == str(delegate["sha256"]),
        "the_amended_gate_carries_the_four_clause_evaluation": all(
            key in dict((gate.get("criteria_evaluation") or {}).get("results") or {})
            for key in SUPPORTED_CRITERIA
        ),
    }
    record(
        "the_amended_gate_is_green_references_both_pins_and_pins_this_delegate",
        all(gate_checks.values()),
        {"per_check": gate_checks, "amended_gate": runner._relative(AMENDMENT_GATE_OUT)},
    )
    return {
        "ok": all(entry["ok"] for entry in checks),
        "checks": checks,
        "failed": [entry["check"] for entry in checks if not entry["ok"]],
        "amendment": pin,
        "amended_gate": {
            "path": runner._relative(AMENDMENT_GATE_OUT),
            "sha256": sha256_of(AMENDMENT_GATE_OUT) if gate_present else "",
            "present": gate_present,
            "recorded_frozen_gate": recorded,
            "paid_run_allowed": gate.get("paid_run_allowed"),
            "per_check": gate_checks,
        },
        "delegate": delegate,
    }


# --------------------------------------------------------------------------- the amended gate
def _criteria_evaluation_at_the_gate(gate_result: dict[str, Any]) -> dict[str, Any]:
    """Each of the amendment's clauses, with the frozen gate check that decides it at this stage."""

    hard = dict(gate_result.get("hard_assertions") or {})
    results = {
        "zero_render_failures_zero_truncations_zero_dropped_fields": bool(
            hard.get("b_trigger_gate_fires_every_call")
            and hard.get("c_mechanism_engages_on_unique_content")
            and hard.get("f_single_occurrence_paths_note_rule_and_recency")
        ),
        "every_arms_strict_track_at_or_above_the_baseline": bool(
            hard.get("g_length_discipline_sentence_identical_in_every_arms_first_prompt")
        ),
        "every_arms_semantic_correctness_at_or_above_the_baseline": bool(
            hard.get("h_over_length_retry_wording_strengthened_and_two_sided")
        ),
        "retry_recovery_rate_at_least_90_percent": bool(
            hard.get("h_over_length_retry_wording_strengthened_and_two_sided")
            and gate_result.get("ok") is True
        ),
    }
    return {
        "evaluated_at": "the amended zero-API gate, before any paid request",
        "results": results,
        "where": {
            "zero_render_failures_zero_truncations_zero_dropped_fields": (
                "the frozen gate's trigger-gate, mechanism and literal-invariant assertions, all of "
                "which are zero-API; the batch's own rows are judged by the independent audit"
            ),
            "every_arms_strict_track_at_or_above_the_baseline": (
                "the frozen gate's first-prompt assertion, which is what makes the strict track "
                "arm-neutral; the per-task track is judged by the independent audit"
            ),
            "every_arms_semantic_correctness_at_or_above_the_baseline": (
                "the frozen gate's retry-wording assertion, which is what keeps the answer "
                "reachable in every arm; the endpoint is judged by the independent audit"
            ),
            "retry_recovery_rate_at_least_90_percent": (
                "the gate can only establish that the repair path is installed and two-sided; the "
                "rate itself is a property of the batch and is measured by the independent audit"
            ),
        },
        "measured_by_the_recalibration": (
            "five of five first-attempt rejections repaired to an accepted answer, which is the "
            "amendment's own measurement and is disclosed as such rather than re-derived here"
        ),
        "residual_risk_carried_into_this_batch": (
            "at 44 percent first-attempt acceptance, roughly half of the samples are expected to "
            "spend the single billed retry; retries are charged to their arm, they add calls to a "
            "trajectory, and in earlier batches one asymmetric retry moved a pooled mean by 7 to 21 "
            "percentage points"
        ),
    }


@contextmanager
def sealed_v24_round_installed():
    """The frozen runner's own ``main`` preamble, as a context manager.

    The frozen gate body reads the sealed v18 registration and builds every task, so it only
    verifies while the registered three-round shape is installed; and the paid pass must run under
    the same installation its gate pinned.  Both halves call this, so the gate and the batch cannot
    run under two different configurations.
    """

    with runner.sealed_v24_registry_installed():
        runner.select_window(runner.K)
        runner.configure()
        yield runner


def run_amendment_gate() -> dict[str, Any]:
    """Re-run the frozen runner's own gate under this amendment and add the reference assertions.

    The frozen gate body is called as it stands - no assertion is removed, relaxed or reordered, and
    the artifact it writes in a previous run is read, never rewritten.  What this function adds is
    the amendment's own chain: the pin, the ruling's shape, the resume refusal, the delegate pin,
    the runner pin, the original gate reference and the record of what this delegate verifies.
    """

    if AMENDMENT_GATE_OUT.exists():
        raise SystemExit(
            f"refusing to overwrite the existing amended gate {AMENDMENT_GATE_OUT}: "
            "write a new one instead"
        )
    if not ORIGINAL_GATE.is_file():
        raise SystemExit(f"refusing to amend a gate that is not on disk: {ORIGINAL_GATE}")
    original_before = {
        "path": runner._relative(ORIGINAL_GATE),
        "sha256": sha256_of(ORIGINAL_GATE),
        "written_utc": datetime.fromtimestamp(
            ORIGINAL_GATE.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds"),
    }
    original_payload = json.loads(ORIGINAL_GATE.read_text(encoding="utf-8"))
    # The frozen gate runs under the same installation the frozen runner's own ``--gate`` mode uses.
    with sealed_v24_round_installed():
        ledger = runner.load_revision_ledger()
        frozen = runner.gate(ledger)
        plan = runner.planned_requests()
        maths = runner.arithmetic()
        live_modules = runner.module_hashes()
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    record(
        "the_frozen_runners_own_gate_passes_unchanged_for_this_batch",
        frozen.get("ok") is True and frozen.get("paid_run_allowed") is True,
        {
            "checks": len(frozen.get("checks") or []),
            "failed_checks": frozen.get("failed_checks"),
            "hard_assertions": frozen.get("hard_assertions"),
            "paid_run_allowed": frozen.get("paid_run_allowed"),
            "no_assertion_was_removed_dropped_or_relaxed": (
                "the frozen gate body ran as it stands, under the same sealed-round installation "
                "the frozen runner's own --gate mode uses; this module only reads its result"
            ),
        },
    )
    record(
        "the_original_v24_gate_artifact_is_green_and_was_not_written_by_this_run",
        original_payload.get("ok") is True
        and original_payload.get("paid_run_allowed") is True
        and original_payload.get("api_calls") == 0
        and sha256_of(ORIGINAL_GATE) == original_before["sha256"],
        {
            **original_before,
            "paid_run_allowed": original_payload.get("paid_run_allowed"),
            "checks": len(original_payload.get("checks") or []),
            "file_unchanged_during_this_run": True,
        },
    )
    reference = verify_amendment(require_gate=False)
    record(
        "the_amendment_pin_and_the_ruling_shape_are_verified_fail_closed",
        reference["ok"],
        {
            "checks": reference["checks"],
            "failed": reference["failed"],
            "amendment": reference["amendment"],
        },
    )
    recorded_modules = dict(frozen.get("module_hashes") or {})
    drifted = sorted(
        relative
        for relative, digest in live_modules.items()
        if recorded_modules.get(relative) != digest
    )
    record(
        "the_live_modules_are_the_modules_the_re_run_gate_just_pinned",
        not drifted and sorted(recorded_modules) == sorted(live_modules),
        {
            "modules": len(live_modules),
            "drifted": drifted,
            "gate_artifact_module_hashes_match_live": not drifted,
        },
    )
    record(
        "the_batch_the_amendment_allows_is_the_frozen_matrix_with_its_cap",
        plan["samples"] == runner.SAMPLES == 45
        and plan["max_api_requests"] == maths["cap"] == 390
        and plan["worst_case_fits_the_cap"]
        and maths["at_least_1_3"],
        {"planned": plan, "cap_derivation": maths["derivation"]},
    )
    evidence = run_paid_evidence()
    record(
        "the_paid_path_that_will_run_refuses_resume_and_verifies_this_amendment",
        evidence["ok"],
        evidence,
    )
    delegate = delegate_value()
    record(
        "the_delegate_that_carries_the_amendment_is_pinned_by_its_own_bytes",
        bool(delegate["sha256"]) and sha256_of(Path(__file__).resolve()) == delegate["sha256"],
        delegate,
    )
    evaluation = _criteria_evaluation_at_the_gate(frozen)
    record(
        "every_clause_of_the_amendment_has_its_gate_stage_check_and_none_is_missing",
        len(evaluation["results"]) == len(DECISION_CLAUSES)
        and all(evaluation["results"].values()),
        evaluation,
    )
    ok = all(entry["ok"] for entry in checks)
    payload = {
        "schema": "openai_agents_v24_length_compliance_k1_amended_gate",
        "amended_gate_id": "openai-agents-v24-k1-amended-gate-01",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_calls": 0,
        "mode": "gate-amendment",
        "experiment_id": EXPERIMENT_ID,
        "produced_by": DELEGATE_MODULE,
        "amendment": amendment_block(),
        "amendment_is_fail_closed": (
            "the paid pass re-reads the amendment file, recomputes its digest and compares it with "
            "the pin recorded here and in this module before it sends anything; a missing file or "
            "one byte of drift refuses the batch"
        ),
        "the_frozen_runners_own_gate_artifact_is_untouched": (
            "this mode reads the v24 gate artifact, re-runs the frozen gate body in this process and "
            "writes this amended artifact; the original file's digest is recorded here and below, "
            "and no mode of this module writes it"
        ),
        "installed_for_the_gate_re_run": (
            "runner.sealed_v24_registry_installed() then runner.select_window(K) then "
            "runner.configure(), which is the frozen runner's own --gate preamble"
        ),
        "recorded_frozen_gate": {
            **original_before,
            "paid_run_allowed": original_payload.get("paid_run_allowed"),
            "ok": original_payload.get("ok"),
            "amendment_sha256": AMENDMENT_SHA256,
            "delegate_sha256": delegate["sha256"],
            "batch_experiment_id": EXPERIMENT_ID,
        },
        "delegate": delegate,
        "frozen_runner_unchanged": {
            "module": RUNNER_MODULE,
            "recorded_sha256": str(ledger.get(RUNNER_MODULE, "")),
            "live_sha256": sha256_of(ROOT / RUNNER_MODULE),
            "why_the_amendment_is_not_in_the_runner": (
                "the runner's bytes are pinned inside the write-once confirmation ledger and the "
                "frozen gate fails closed on module drift, so carrying the amendment inside it "
                "would void the pin; the gate's own "
                "``_paid_path_pins_this_runner_or_delegates_it`` expects a delegate for this case"
            ),
        },
        "frozen_gate_re_run": {
            "checks": len(frozen.get("checks") or []),
            "failed_checks": frozen.get("failed_checks"),
            "hard_assertions": frozen.get("hard_assertions"),
            "per_sample_mechanism_samples": len(frozen.get("per_sample_mechanism") or []),
            "shape": {
                "samples": runner.SAMPLES,
                "repeats": runner.REPEATS,
                "cap": runner.MAX_API_REQUESTS,
                "experiment_id_of_this_batch": EXPERIMENT_ID,
                "experiment_id_the_frozen_artifact_names": runner.EXPERIMENT_ID,
            },
        },
        "inherited_plan_and_arithmetic": {"planned_requests": plan, "arithmetic": maths},
        "criteria_evaluation": evaluation,
        "amendment_reference_assertion": {
            "check": (
                "the batch runner reads the amendment's path and sha256 and fails closed when either "
                "is missing or does not match"
            ),
            "amendment": amendment_block(),
            "where_the_runner_reads_it": (
                "this delegate's ``verify_amendment``, which the paid pass calls before the frozen "
                "paid path and which the manifest block records"
            ),
            "no_other_gate_was_weakened": (
                "the frozen runner's own gate is re-run here and every one of its assertions and "
                "hard assertions is reported above unchanged; the frozen gate artifact is read, "
                "never written"
            ),
            "the_original_gate_artifact_is_untouched": original_before,
        },
        "checks": checks,
        "ok": ok,
        "paid_run_allowed": ok,
        "paid_run_allowed_rule": (
            "true only when the frozen runner's own gate passes unchanged under this amendment, the "
            "amendment is byte its pin, the ruling's clauses are the four this delegate decides, the "
            "frozen runner still matches the ledger's pin, and the delegate is pinned"
        ),
        "wording": {"required": REQUIRED_WORDING, "forbidden": list(FORBIDDEN_WORDING)},
    }
    _write_once(AMENDMENT_GATE_OUT, payload)
    return payload


def run_paid_evidence() -> dict[str, Any]:
    """What the paid pass is made of, checked against the code that will run rather than asserted."""

    import inspect

    source = inspect.getsource(run_paid)
    forbidden = "--resume" in source
    return {
        "ok": not forbidden
        and "verify_amendment()" in source
        and "runner.run_paid" in source
        and "amendment_manifest_finalise" in source,
        "delegates_to_the_frozen_paid_path": "runner.run_paid" in source,
        "verifies_the_amendment_before_it_sends": "verify_amendment()" in source,
        "installs_the_amendment_block_into_the_manifest": "amendment_manifest_finalise" in source,
        "resume_is_refused": not forbidden and "--resume" in inspect.getsource(_refuse_resume),
        "resume_rule": (
            "``--resume`` is refused by this delegate before anything else, because a resumed batch "
            "could skip samples the frozen matrix requires"
        ),
        "pins_the_run_id": EXPERIMENT_ID,
        "installs_the_frozen_round_before_the_paid_path": (
            "sealed_v24_registry_installed, select_window(K) and configure(), the same preamble the "
            "frozen gate and the frozen paid path run under"
        ),
        "pins_the_frozen_runner_or_delegates_it": (
            "the frozen paid path writes and compares the runner bytes outside the repository "
            "before its first request"
        ),
    }


# ------------------------------------------------------------------------------- the paid pass
def _refuse_resume(args: list[str]) -> None:
    if "--resume" in args or "--retry-failed" in args:
        raise SystemExit(
            "refusing to run: this batch never resumes and never retries a failed sample, because "
            "the frozen matrix requires every sample of every arm and --resume could skip one"
        )


def load_provider_key(key_file: Path | None = None, *, required: bool = True) -> dict[str, Any]:
    """Install the provider key from the environment or from a named file, into this process only.

    The key is never written to a file, a manifest, a log line or an artifact: only its variable
    name and its length are recorded, and the value is redacted from anything this module prints.
    """

    present = {name: bool(os.environ.get(name)) for name in KEY_NAMES}
    source = "environment" if any(present.values()) else ""
    if not source and required:
        target = Path(key_file) if key_file is not None else DEFAULT_KEY_FILE
        if not target.is_file():
            raise SystemExit(
                "refusing to send a paid request: no provider key in the environment and no key "
                f"file at {target}; pass --key-file or set DEEPSEEK_API_KEY"
            )
        text = target.read_text(encoding="utf-8", errors="replace")
        found: dict[str, str] = {}
        for name in KEY_NAMES:
            match = re.search(
                rf"^\s*{name}\s*:\s*[\"']?([^\"'\s#]+)[\"']?\s*$", text, flags=re.MULTILINE
            )
            if match:
                found[name] = match.group(1)
        if not found:
            raise SystemExit(f"no provider key found in {target}")
        for name, value in found.items():
            os.environ.setdefault(name, value)
        source = str(target)
        present = {name: bool(os.environ.get(name)) for name in KEY_NAMES}
    return {
        "source": source,
        "loaded": {
            name: {
                "present": present[name],
                "characters": len(os.environ.get(name, "")),
                "value": KEY_REDACTION,
            }
            for name in KEY_NAMES
        },
        "written_into_any_artifact": False,
        "why_no_value_is_recorded": (
            "a credential is not evidence; the batch records the endpoint, the model and the token "
            "counters, and the key is only ever in this process's environment"
        ),
    }


@contextmanager
def amendment_manifest_finalise(amended_gate: dict[str, Any]):
    """Add the amendment block to the manifest inside the frozen writer, in the same pass.

    The frozen finaliser writes the manifest once and refuses to overwrite a finalised one.  This
    wrapper sits *outside* the runner's own wrapper, so the keys it adds are written by the same
    single pass that writes every other key: the manifest on disk is written once, and its own
    writer still refuses to touch a finalised file.
    """

    original = runner.runner17.finalise_pilot_manifest
    live_delegate = delegate_value()["sha256"]
    pin_sha = sha256_of(AMENDMENT) if AMENDMENT.is_file() else ""
    gate_rel = runner._relative(AMENDMENT_GATE_OUT)
    gate_sha = sha256_of(AMENDMENT_GATE_OUT) if AMENDMENT_GATE_OUT.is_file() else ""

    def finalise(root, **kwargs):
        manifest = original(root, **kwargs)
        path = Path(root) / "manifest.json"
        problems: list[str] = []
        if pin_sha != AMENDMENT_SHA256:
            problems.append(f"the amendment digest changed between the check and the write: {pin_sha}")
        if live_delegate != delegate_value()["sha256"]:
            problems.append("the delegate's own bytes changed during the run")
        if not AMENDMENT_GATE_OUT.is_file() or sha256_of(AMENDMENT_GATE_OUT) != gate_sha:
            problems.append("the amended gate artifact changed during the run")
        if problems:
            raise SystemExit(
                "refusing to finalise the manifest: " + "; ".join(problems)
            )
        manifest["v24_gate_amendment"] = {
            "amendment": {
                "path": runner._relative(AMENDMENT),
                "sha256": AMENDMENT_SHA256,
                "amendment_id": AMENDMENT_ID,
                "amends": AMENDMENT_AMENDS,
                "read_and_verified_before_the_first_request": True,
            },
            "amended_gate": {
                "path": gate_rel,
                "sha256": gate_sha,
                "paid_run_allowed": bool(amended_gate.get("paid_run_allowed")),
                "checks": len(amended_gate.get("checks") or []),
            },
            "original_gate": {
                "path": runner._relative(ORIGINAL_GATE),
                "sha256": sha256_of(ORIGINAL_GATE) if ORIGINAL_GATE.is_file() else "",
                "untouched_by_this_batch": True,
            },
            "delegate": {
                "module": DELEGATE_MODULE,
                "sha256": live_delegate,
                "the_frozen_runner_it_delegates_to": {
                    "module": RUNNER_MODULE,
                    "recorded_sha256": str(runner.load_revision_ledger().get(RUNNER_MODULE, "")),
                },
            },
            "the_replaced_line": (
                "the recalibration's 70 percent first-attempt acceptance line is replaced as the "
                "gate by the amendment's four operative clauses, and this batch does not report "
                "itself as having met the 70 percent line"
            ),
            "criteria_evaluation": amended_gate.get("criteria_evaluation"),
            "residual_risk": (
                "at 44 percent first-attempt acceptance about half of the samples are expected to "
                "spend the single billed retry; retries are charged to their arm and in earlier "
                "batches one asymmetric retry moved a pooled mean by 7 to 21 percentage points"
            ),
            "discipline": {
                "no_literal_was_weakened": True,
                "no_task_was_dropped_after_seeing_it_fail": True,
                "the_gate_revision_and_its_residual_risk_are_recorded_here": True,
                "the_confirmation_is_not_reported_as_meeting_the_70_percent_line": True,
                "resume_was_refused": True,
            },
            "manifest_written_in_one_pass": True,
            "amendment_block_added_inside_the_frozen_writers_own_pass": True,
        }
        missing = [
            name
            for name in ("v24_confirmation_freeze", "v24_prior_batch_disclosure", "mechanism")
            if name not in manifest
        ]
        if missing:
            problems.extend(f"the manifest lost a pre-registered key: {name}" for name in missing)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manifest

    runner.runner17.finalise_pilot_manifest = finalise
    try:
        yield
    finally:
        runner.runner17.finalise_pilot_manifest = original


def run_paid(args: list[str], *, key_file: Path | None = None) -> int:
    """The paid pass: verify the amendment fail-closed, then delegate to the frozen paid path."""

    _refuse_resume(args)
    verification = verify_amendment()
    if not verification["ok"]:
        raise SystemExit(
            "refusing to send a paid request: the gate amendment did not verify fail-closed: "
            + ", ".join(verification["failed"])
        )
    amended_gate = json.loads(AMENDMENT_GATE_OUT.read_text(encoding="utf-8"))
    if amended_gate.get("paid_run_allowed") is not True:
        raise SystemExit(
            "refusing to send a paid request: the amended gate recorded paid_run_allowed="
            f"{amended_gate.get('paid_run_allowed')!r}"
        )
    key = load_provider_key(key_file, required=not any(os.environ.get(n) for n in KEY_NAMES))
    paid = list(args)
    paid = runner.runner17._replace_option(paid, "--experiment-id", EXPERIMENT_ID)
    if "--confirm-send-public-source" not in paid:
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    print(
        "amendment verified: "
        f"{runner._relative(AMENDMENT)} @ {AMENDMENT_SHA256[:12]}... "
        f"amended_gate={runner._relative(AMENDMENT_GATE_OUT)} "
        f"experiment={EXPERIMENT_ID} key_source={key['source']}"
    )
    with amendment_manifest_finalise(amended_gate):
        with sealed_v24_round_installed():
            status = runner.run_paid(paid)
    return int(status)


def probe() -> dict[str, Any]:
    """Print-only: the pin, its checks and the amended gate's verdict, writing nothing."""

    verification = verify_amendment(require_gate=AMENDMENT_GATE_OUT.is_file())
    return {
        "amendment": verification["amendment"],
        "checks": verification["checks"],
        "ok": verification["ok"],
        "failed": verification["failed"],
        "amended_gate": verification.get("amended_gate"),
        "delegate": verification.get("delegate"),
        "experiment_id": EXPERIMENT_ID,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-amendment", action="store_true")
    parser.add_argument("--paid", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--key-file", default="")
    parser.add_argument("--experiment-id", default="")
    args, rest = parser.parse_known_args(list(sys.argv[1:] if argv is None else argv))

    key_file = Path(args.key_file) if args.key_file else None
    if args.probe:
        payload = probe()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1
    if args.gate_amendment:
        payload = run_amendment_gate()
        print(json.dumps({"ok": payload["ok"], "api_calls": 0}, ensure_ascii=False))
        print(f"  amendment: {payload['amendment']['path']} @ {payload['amendment']['sha256']}")
        print(f"  delegate: {payload['delegate']['module']} @ {payload['delegate']['sha256']}")
        for name, value in (payload["frozen_gate_re_run"]["hard_assertions"] or {}).items():
            print(f"  frozen hard assertion {name}: {value}")
        for entry in payload["checks"]:
            print(f"  {entry['check']}: ok={entry['ok']}")
        for name, value in payload["criteria_evaluation"]["results"].items():
            print(f"  clause {name}: {value}")
        print(f"  paid_run_allowed: {payload['paid_run_allowed']}")
        print(f"  amended gate artifact: {AMENDMENT_GATE_OUT}")
        return 0 if payload["ok"] else 1
    if args.paid:
        paid = list(rest)
        if args.experiment_id:
            paid = runner.runner17._replace_option(paid, "--experiment-id", args.experiment_id)
        return run_paid(paid, key_file=key_file)
    parser.error("nothing to do: pass --gate-amendment, --paid or --probe")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
