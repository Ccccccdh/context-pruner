"""v16 Stage 1 (zero API): 15315 registration, renderer replay, cost envelope, freeze draft.

Nothing here is frozen, nothing is scored again, and no request is made. The renderer never
truncates: if a sample's fields cannot be rendered inside the frozen bounds the sample counts
as a failure and that share is reported.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from experiments.runners.openai_agents_structured_final_v16 import MAX_CHARS, render

REPO = Path(__file__).resolve().parents[2]
CLONE = REPO / ".tooling/upstream/django-15563"
POOL = REPO / "integrations/openai_agents/V16_CANDIDATE_POOL_20261006.json"
REGISTRATION = REPO / "integrations/openai_agents/V16_SOURCE_REGISTRATION_20261006.json"
PROBES = REPO / "integrations/openai_agents/V16_GATE_PROBES_20261006.json"
OUT = REPO / "integrations/openai_agents/V16_STAGE1_20261006.json"

TASK = {
    "task_id": "django_field_hash_immutability",
    "instance_id": "django__django-15315",
    "path": "django/db/models/fields/__init__.py",
    "issue": "django-15315",
    "cause_token": "__hash__",
    "fix_token": "creation_counter",
    "facts": ("__hash__", "__eq__", "creation_counter"),
    "forbidden": ("no bug", "not a bug"),
    "request": (
        "Diagnose why a Field instance cannot be used as a stable dictionary key once it has "
        "been assigned to a model class. Read the field comparison and hashing methods, the "
        "field class header with the creation counters, and the contribution method in order; "
        "name the method pair whose value semantics depend on the model and the counter the fix "
        "must base the hash on."
    ),
}

RUNS = REPO / "runs/stage5-openai-agents-api"
BATCHES = (
    ("v12", "openai-repo-diagnostic-v12-exact-duplicate-dev-01"),
    ("v13", "openai-repo-diagnostic-v13-requests1766-confirm-01"),
    ("v14", "openai-repo-diagnostic-v14-multitask-confirm-01"),
)


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(CLONE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def commit_of(instance_id: str) -> str:
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    entry = next(item for item in pool["candidates"] if item["instance_id"] == instance_id)
    return str(entry["base_commit"])


def line_of(text: str, pattern: str, start: int = 1) -> int | None:
    for number, line in enumerate(text.splitlines(), 1):
        if number >= start and re.search(pattern, line):
            return number
    return None


def view(text: str, commit: str, path: str, first: int, last: int, role: str) -> dict[str, Any]:
    lines = text.splitlines()
    first = max(1, first)
    last = min(len(lines), last)
    body = "\n".join(f"{number}: {line}" for number, line in enumerate(lines[first - 1 : last], first))
    rendered = f"django/django/{path} (baseline)\n{body}"
    return {
        "file": path,
        "first_line": first,
        "last_line": last,
        "role": role,
        "commit_path": f"{commit}:{path}",
        "blob_git_id": git("rev-parse", f"{commit}:{path}").stdout.strip(),
        "file_content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "view_content_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "view_bytes": len(rendered.encode("utf-8")),
        "text": rendered,
    }


def register_15315() -> dict[str, Any]:
    commit = commit_of(TASK["instance_id"])
    result = git("show", f"{commit}:{TASK['path']}")
    if result.returncode != 0:
        return {"registered": False, "reason": "source_unreadable", "commit": commit}
    text = result.stdout
    tree = ast.parse(text)
    field = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Field"), None
    )
    if field is None:
        return {"registered": False, "reason": "Field class missing", "commit": commit}
    methods = {node.name: node.lineno for node in field.body if isinstance(node, ast.FunctionDef)}
    if "__hash__" not in methods or "__eq__" not in methods:
        return {"registered": False, "reason": "field hash methods missing", "commit": commit}
    counter = line_of(text, r"creation_counter = 0") or methods["__hash__"]
    contribute = methods.get("contribute_to_class") or methods["__eq__"]
    views = [
        view(text, commit, TASK["path"], methods["__eq__"] - 12, methods["__hash__"] + 12,
             "cause: the value-semantics comparison and hash pair"),
        view(text, commit, TASK["path"], counter - 12, counter + 12,
             "context: the creation counters the fix must use"),
        view(text, commit, TASK["path"], contribute - 12, contribute + 20,
             "context: where the field is bound to a model class"),
    ]
    joined = "\n".join(entry["text"] for entry in views)
    problems = []
    for token in (TASK["cause_token"], TASK["fix_token"]):
        if token not in joined:
            problems.append(f"contract token {token!r} is not in the views")
    for fact in TASK["facts"]:
        if fact not in joined:
            problems.append(f"required fact {fact!r} is not in the views")
    from experiments.runners.openai_agents_v16_source_registration import TASKS

    pattern = f"RESULT issue={TASK['issue']} cause=.*{TASK['cause_token']}.* fix=.*{TASK['fix_token']}.*"
    return {
        "registered": not problems,
        "task_id": TASK["task_id"],
        "instance_id": TASK["instance_id"],
        "base_commit": commit,
        "problem_statement_sha256": hashlib.sha256(
            next(
                item
                for item in json.loads(POOL.read_text(encoding="utf-8"))["candidates"]
                if item["instance_id"] == TASK["instance_id"]
            )["problem_statement_sha256"].encode()
        ).hexdigest(),
        "registered_tasks_before": len(TASKS),
        "views": [{key: value for key, value in entry.items() if key != "text"} for entry in views],
        "view_texts": [entry["text"] for entry in views],
        "contract": {
            "json_fields": ["issue", "cause", "fix", "facts"],
            "issue_value": TASK["issue"],
            "required_facts": list(TASK["facts"]),
            "forbidden_facts": list(TASK["forbidden"]),
            "frozen_regex": pattern,
            "max_answer_chars": MAX_CHARS,
            "renderer": "experiments/runners/openai_agents_structured_final_v16.py",
        },
        "defect_evidence": {
            "probe_artifact": str(PROBES.relative_to(REPO)).replace("\\", "/"),
            "probe_observed": "present",
            "methods": methods,
        },
        "problems": problems,
    }


def contract_for(batch: str, task: str) -> tuple[str, str, tuple[str, ...]]:
    from experiments.runners import openai_agents_multitask_registry_v14 as registry
    from experiments.runners import openai_agents_requests_task_registry_v13 as registry13

    if batch == "v14":
        entry = registry.task(task)
        match = re.search(r"issue=([^ ]+)", str(entry["contract"]))
        return match.group(1), entry["answer_pattern"], tuple(entry["required_terms"])
    if batch == "v13":
        return (
            "requests-1766",
            r"RESULT issue=requests-1766 cause=.*qop.* fix=.*quot.*",
            tuple(registry13.REQUIRED_TERMS),
        )
    return (
        "django-16263",
        r"RESULT issue=django-16263 cause=.*existing_annotations.*subquery.* fix=.*referenced.*",
        ("existing_annotations", "subquery", "referenced"),
    )


def replay() -> dict[str, Any]:
    """Apply the deterministic renderer to every recorded answer and judge four conditions."""
    rows = []
    for batch, directory in BATCHES:
        for row in [
            json.loads(line)
            for line in (RUNS / directory / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]:
            answer = str(row.get("final_output", ""))
            issue, pattern, facts = contract_for(batch, str(row.get("scenario")))
            cause = answer.split("cause=", 1)[1].split(" fix=", 1)[0] if "cause=" in answer else ""
            fix = answer.split(" fix=", 1)[1] if " fix=" in answer else ""
            declared = [fact for fact in facts if fact in answer]
            proposal = json.dumps(
                {"issue": issue, "cause": cause, "fix": fix, "facts": declared or list(facts)}
            )
            outcome = render(proposal, expected_issue=issue)
            rendered = outcome.output
            conditions = {
                "facts_complete": bool(rendered)
                and all(fact in rendered for fact in declared or facts),
                "frozen_regex": bool(re.fullmatch(pattern, rendered, flags=re.IGNORECASE)),
                "single_line_prefix": rendered.startswith(f"RESULT issue={issue} cause=")
                and "\n" not in rendered,
                "within_160": bool(rendered) and len(rendered) <= MAX_CHARS,
            }
            first_failure = next((name for name, ok in conditions.items() if not ok), None)
            rows.append(
                {
                    "batch": batch,
                    "task": str(row.get("scenario")),
                    "repeat": int(row["repeat"]),
                    "method": str(row["method"]),
                    "source_chars": len(answer),
                    "rendered_chars": len(rendered),
                    "render_accepted": outcome.accepted,
                    "render_reason": outcome.reason,
                    "conditions": conditions,
                    "first_failure": first_failure,
                    "literals_dropped": [fact for fact in facts if fact in answer and rendered and fact not in rendered],
                }
            )
    total = len(rows)
    passed = [row for row in rows if all(row["conditions"].values())]
    failures = [row for row in rows if row not in passed]
    return {
        "samples": total,
        "passed": len(passed),
        "pass_rate": len(passed) / total if total else 0.0,
        "failures": failures,
        "failure_decomposition": {
            name: sum(1 for row in failures if row["first_failure"] == name)
            for name in ("facts_complete", "frozen_regex", "single_line_prefix", "within_160")
        },
        "literals_dropped_anywhere": sum(1 for row in rows if row["literals_dropped"]),
        "truncation_observed": sum(1 for row in rows if row["rendered_chars"] < row["source_chars"]),
        "rows": rows,
    }


def negative_controls() -> list[dict[str, Any]]:
    issue = "django-15315"
    pattern = f"RESULT issue={issue} cause=.*__hash__.* fix=.*creation_counter.*"
    cases = [
        (
            "artificially_over_long",
            json.dumps(
                {
                    "issue": issue,
                    "cause": "__hash__ and __eq__ depend on the model " + "x" * 140,
                    "fix": "base the hash on creation_counter",
                    "facts": ["__hash__", "creation_counter"],
                }
            ),
            "render_rejected",
        ),
        (
            "missing_literal",
            json.dumps(
                {"issue": issue, "cause": "the model changes", "fix": "fix it", "facts": ["__hash__"]}
            ),
            "render_rejected",
        ),
    ]
    out = []
    for name, proposal, expectation in cases:
        outcome = render(proposal, expected_issue=issue)
        out.append(
            {
                "control": name,
                "render_accepted": outcome.accepted,
                "reason": outcome.reason,
                "chars": outcome.chars,
                "expected": expectation,
                "ok": not outcome.accepted,
            }
        )
    good = render(
        json.dumps(
            {
                "issue": issue,
                "cause": "__hash__ and __eq__ depend on the model",
                "fix": "base the hash on creation_counter",
                "facts": ["__hash__", "creation_counter"],
            }
        ),
        expected_issue=issue,
    )
    tampered = good.output.replace("__hash__", "__hash_", 1)
    out.append(
        {
            "control": "one_character_tampered_output",
            "render_accepted": good.accepted,
            "tampered_matches_regex": bool(re.fullmatch(pattern, tampered, flags=re.IGNORECASE)),
            "expected": "regex_must_fail",
            "ok": good.accepted and not re.fullmatch(pattern, tampered, flags=re.IGNORECASE),
        }
    )
    return out


def cost_envelope(replay_report: dict[str, Any]) -> dict[str, Any]:
    worst: list[dict[str, Any]] = []
    for batch, directory in BATCHES:
        rows = [
            json.loads(line)
            for line in (RUNS / directory / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for row in rows:
            if str(row["method"]) != "pruner_v1":
                continue
            by_call = list(row.get("actual_input_tokens_by_call") or [])
            outputs = list(row.get("actual_output_tokens_by_call") or [])
            resend = int(by_call[-1]) if by_call else int(row.get("actual_peak_input_tokens", 0) or 0)
            answer_out = int(outputs[-1]) if outputs else int(row.get("actual_output_tokens", 0) or 0)
            worst.append(
                {
                    "batch": batch,
                    "task": str(row.get("scenario")),
                    "repeat": int(row["repeat"]),
                    "extra_requests": 1,
                    "extra_tokens": resend + answer_out,
                    "baseline_total": int(row.get("all_arm_total_tokens", 0) or 0),
                }
            )
    return {
        "rendering_is_host_side": True,
        "rendering_is_billed": False,
        "retry_budget_per_sample": 1,
        "worst_case_extra_requests": len(worst),
        "worst_case_extra_tokens": sum(item["extra_tokens"] for item in worst),
        "per_sample_worst_case_tokens": worst,
        "observed_replay_over_160_share": (
            replay_report["failure_decomposition"]["within_160"] / replay_report["samples"]
            if replay_report["samples"]
            else 0.0
        ),
        "expected_retry_rate_basis": (
            "the replay's own share of answers that fail the length condition while the other "
            "three conditions hold; the retry only fires for those, and the renderer itself "
            "never truncates, so the host adds no retries of its own"
        ),
    }


def freeze_draft(registration: dict[str, Any]) -> dict[str, Any]:
    renderer = REPO / "experiments/runners/openai_agents_structured_final_v16.py"
    return {
        "status": "DRAFT_NOT_FROZEN",
        "may_run_paid_batch": False,
        "host_configuration": (
            "v16 all-arm structured final answer: JSON issue/cause/fix/facts plus a deterministic "
            "RESULT renderer, identical for every arm, at most one billed format retry"
        ),
        "bounds_are_not_relaxed": "at most 160 characters remains an acceptance condition",
        "tasks": ["django_sqlite_version_floor", "django_orderedset_reversed", registration.get("task_id")],
        "renderer": {
            "module": str(renderer.relative_to(REPO)).replace("\\", "/"),
            "sha256": hashlib.sha256(renderer.read_bytes()).hexdigest(),
            "truncates": False,
            "fidelity_clause": (
                "if a sample's fields cannot be rendered inside facts + frozen regex + 160 "
                "characters, the sample counts as a failure and the share is reported; no "
                "truncation and no dropped field may be used to fit the bound"
            ),
        },
        "criteria": {
            "strict_track": "required facts + frozen regex + single line/prefix + <=160, per sample",
            "length_track": "reported, not an acceptance condition of its own",
            "coverage": "share of samples whose rendered answer satisfies all four conditions",
        },
        "request_budget": {
            "acquisition": {"tasks": 3, "arms": ["none"], "repeats": 1, "max_api_requests": 30,
                            "purpose": "payload_acquisition_only", "citable_as_saving": False,
                            "citable_as_quality_equivalence": False},
            "development_pilot": {"tasks": 3, "repeats": 3, "arms": 3, "samples": 27,
                                  "max_api_requests": 300},
            "confirmation": {"tasks": 3, "repeats": 3, "arms": 3, "samples": 27,
                             "max_api_requests": 300, "candidates": ["django__django-11820",
                             "django__django-12193", "django__django-13089"]},
        },
        "summary_cap": "declared per sample in the freeze and charged to the arm that makes the call",
        "failure_lines": [
            "strict track below baseline on any task or pooled",
            "paired complete-total provider-token saving not positive in the majority or not positive pooled",
            "zero actual duplicate replacements",
            "a required literal loses presence or the renderer drops a field",
            "request or summary cap exceeded",
            "independent audit complete=false",
        ],
        "fidelity_reporting": [
            "report strict track and length track together, never as a single quality number",
            "no host truncation; failed renders count as failures",
            "the phrase 'in this host configuration' is required in conclusions",
        ],
        "never_rejudge": "v12 (0/1), v13 (3/3), v14 (7/9 with acceptance.met=false) stand unchanged",
    }


def main() -> int:
    registration = register_15315()
    replay_report = replay()
    controls = negative_controls()
    envelope = cost_envelope(replay_report)
    draft = freeze_draft(registration)
    report = {
        "schema": "openai_agents_v16_stage1",
        "date": "20261006",
        "paid_requests": 0,
        "registration_15315": {key: value for key, value in registration.items() if key != "view_texts"},
        "development_set": {
            "registered": 2 + (1 if registration.get("registered") else 0),
            "status": "3/3" if registration.get("registered") else "2/3",
            "task_ids": [
                "django_sqlite_version_floor",
                "django_orderedset_reversed",
                *([registration["task_id"]] if registration.get("registered") else []),
            ],
        },
        "renderer_replay": {key: value for key, value in replay_report.items() if key != "rows"},
        "renderer_replay_rows": replay_report["rows"],
        "negative_controls": controls,
        "negative_controls_ok": all(control["ok"] for control in controls),
        "cost_envelope": envelope,
        "freeze_draft": draft,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(
        f"15315 registered: {registration.get('registered')} | dev set "
        f"{report['development_set']['status']}"
    )
    print(
        f"renderer replay: {replay_report['passed']}/{replay_report['samples']} pass "
        f"({replay_report['pass_rate']:.1%}) | decomposition {replay_report['failure_decomposition']} "
        f"| literals dropped {replay_report['literals_dropped_anywhere']} | truncated "
        f"{replay_report['truncation_observed']}"
    )
    for control in controls:
        print(f"  control {control['control']:<32} ok={control['ok']} reason={control.get('reason')}")
    print(
        f"cost envelope: worst {envelope['worst_case_extra_requests']} extra requests, "
        f"{envelope['worst_case_extra_tokens']} tokens"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
