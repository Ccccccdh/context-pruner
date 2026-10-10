"""Register the third v16 development task from pinned public base blobs; zero API."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

from experiments.runners import openai_agents_v16_source_registration as prior

ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT / "integrations/openai_agents/V16_CANDIDATE_POOL_20261006.json"
PROBES = ROOT / "integrations/openai_agents/V16_GATE_PROBES_20261006.json"
OUT = ROOT / "integrations/openai_agents/V16_THIRD_SOURCE_REGISTRATION_20261007.json"
INSTANCE = "django__django-15315"
TASK = "django_field_hash_immutability"
VIEWS = (
    ("django/db/models/fields/__init__.py", 153, 171),
    ("django/db/models/fields/__init__.py", 515, 550),
    ("django/db/models/fields/__init__.py", 775, 790),
)
FACTS = ("Field", "__hash__", "self.model", "creation_counter")


def build() -> dict:
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    probes = json.loads(PROBES.read_text(encoding="utf-8"))
    candidate = next(row for row in pool["candidates"] if row["instance_id"] == INSTANCE)
    assert candidate["decision"] == "eligible" and not candidate["reserved"]
    assert probes["controls_ok"] and probes["target"]["defect_present"]
    assert probes["target"]["instance_id"] == INSTANCE
    row = next(row for row in pq.read_table(str(prior.PARQUET)).to_pylist()
               if row["instance_id"] == INSTANCE)
    commit = row["base_commit"]
    assert commit == candidate["base_commit"] == probes["target"]["probe"]["commit"]
    statement = str(row["problem_statement"])
    statement_sha = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    assert statement_sha == candidate["problem_statement_sha256"]
    views = [prior.view_payload(commit, *view) for view in VIEWS]
    searchable = statement + "\n" + "\n".join(view["text"] for view in views)
    assert all(fact in searchable for fact in FACTS)
    assert "self.model = cls" in views[2]["text"]
    assert "self.model._meta.app_label" in views[1]["text"]
    assert "self.model._meta.model_name" in views[1]["text"]
    assert views[1]["blob_sha256_git"] == probes["target"]["probe"]["blob_git_id"]
    issue = "django-15315"
    return {
        "schema": "openai_agents_v16_third_source_registration",
        "role": "development_candidate_after_registered_mechanical_probe",
        "paid_requests": 0,
        "task_id": TASK, "instance_id": INSTANCE, "repo": row["repo"],
        "base_commit": commit, "difficulty": row["difficulty"],
        "problem_statement_sha256": statement_sha,
        "candidate_pool_sha256": hashlib.sha256(POOL.read_bytes()).hexdigest(),
        "gate_probe_sha256": hashlib.sha256(PROBES.read_bytes()).hexdigest(),
        "views": [{key: value for key, value in view.items() if key != "text"}
                  for view in views],
        "tool_names": ["read_field_initialization", "read_field_equality_hash",
                       "read_field_model_binding"],
        "request": (
            "Diagnose why a Field used as a dictionary key before model assignment is not "
            "found after assignment. Read the initialization, equality/hash, and model-binding "
            "views in order. Name the mutable input to __hash__ and an immutable hash basis."
        ),
        "read_calls": 6, "expected_model_calls": 7,
        "contract": {
            "shape": "RESULT issue=<issue> cause=<cause> fix=<fix>",
            "json_fields": list(prior.CONTRACT_FIELDS),
            "issue_value": issue,
            "required_facts": list(FACTS),
            "cause_token": "__hash__",
            "fix_token": "creation_counter",
            "frozen_regex": (
                r"RESULT issue=django-15315 cause=.*__hash__.*self\.model.* "
                r"fix=.*creation_counter.*"
            ),
            "max_answer_chars": prior.MAX_ANSWER_CHARS,
            "renderer": "experiments/runners/openai_agents_structured_final_v16.py",
        },
        "defect_evidence": {
            "probe_id": "P4_field_hash_immutability",
            "observed": "present",
            "reason": "hash includes self.model metadata, while contribute_to_class later sets self.model",
        },
        "not_reference_fix": True,
    }


def main() -> None:
    if OUT.exists():
        raise SystemExit("registration is write-once")
    artifact = build()
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"registered {TASK} with {len(artifact['views'])} pinned views")


if __name__ == "__main__":
    main()
