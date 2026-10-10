"""v16 candidate pool: the narrowed exclusion rule, persisted in full (zero API).

Rule text (verbatim, as approved):

    An instance is EXCLUDED when its id appears in a file that belongs to design or execution:
    freeze files, protocols, batch manifests, audits, and anything under ``runs/**``.
    An instance is NOT excluded when its id appears only in ``.tooling/`` drafts, preselection
    lists, memos/status documents or errata - a mere mention is not contamination.
    Any other file class (task registries, runners, tests, task files) counts as design, so the
    default is exclusion; only the classes named above are treated as mere mention.

The artifact persists **every** eligible candidate id with its sort key and the exclusion
decision plus the class and file that decided it, so "the next item" is always recoverable
from the artifact instead of from a top-six list.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[2]
TOOLING = REPO / ".tooling"
PARQUET = TOOLING / "swebench-verified/test.parquet"
V15_PRESELECT = REPO / "integrations/openai_agents/V15_PRESELECT_20261006.json"
V16_PRESELECT = REPO / "integrations/openai_agents/V16_PRESELECT_20261006.json"
OUT = REPO / "integrations/openai_agents/V16_CANDIDATE_POOL_20261006.json"
SALT = "v16-structured-final-2026-10-06"
ID_RE = re.compile(r"django__django-\d+")

RULE_TEXT = (
    "exclude when the id appears in a design-or-execution file: freeze files, protocols, "
    "batch manifests, audits, or anything under runs/**; do not exclude when the id appears "
    "only in .tooling/ drafts, preselection lists, memos/status documents or errata; any other "
    "file class counts as design, so the default is exclusion"
)
EXCLUDE_NAME_PATTERNS = (
    "FREEZE",
    "PROTOCOL",
    "ADMISSION",
    "AUDIT",
    "AUDIT_RESULT",
    "RESULT",  # batch result documents
)
IGNORE_NAME_PATTERNS = (
    "PRESELECT",
    "PRESCREEN",
    "SCREENING",
    "STATUS",
    "MEMO",
    "ERRATA",
    "ERRATUM",
    "HANDOFF",
    "PLAN",
    "NEXT_ROUND",
    "SELECTION",
    "CANDIDATE_GATES",
    "GATE_REVIEW",
    "PRECHECK",
    "REVIEW",
    "DIAGNOSIS",
)
IGNORE_DIRS = (".tooling",)


def classify(path: Path) -> str:
    """Return 'ignore', 'exclude' or 'other' for one file."""
    relative = str(path.relative_to(REPO)).replace("\\", "/")
    if relative.startswith(IGNORE_DIRS):
        return "ignore"
    if "/runs/" in f"/{relative}" or relative.startswith("runs/"):
        return "exclude"
    name = path.name.upper()
    if any(pattern in name for pattern in IGNORE_NAME_PATTERNS):
        return "ignore"
    if any(pattern in name for pattern in EXCLUDE_NAME_PATTERNS):
        return "exclude"
    if path.suffix.lower() in {".py", ".json"}:
        return "other"
    return "other"


def scan_candidates() -> dict[str, list[str]]:
    """id -> list of 'class:relative/path' that mention it (bounded, category-aware)."""
    hits: dict[str, list[str]] = {}
    roots = [REPO / "integrations", REPO / "experiments", REPO / "tests", REPO / "tasks"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".json", ".md", ".txt", ".csv"}:
                continue
            if path.name == Path(__file__).name or path.name.startswith("V16_CANDIDATE_POOL"):
                continue
            kind = classify(path)
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in set(ID_RE.findall(text)):
                hits.setdefault(match, []).append(
                    f"{kind}:{str(path.relative_to(REPO)).replace(chr(92), '/')}"
                )
    return hits


def build() -> dict[str, Any]:
    rows = [
        row
        for row in pq.read_table(str(PARQUET)).to_pylist()
        if row["repo"] == "django/django" and row["difficulty"] == "<15 min fix"
    ]
    old = {
        row["instance_id"]
        for row in json.loads(V15_PRESELECT.read_text(encoding="utf-8"))["selected"]
    }
    recorded = json.loads(V16_PRESELECT.read_text(encoding="utf-8"))
    recorded_six = [
        entry["instance_id"]
        for entry in recorded["development_candidates"]
        + recorded["untouched_confirmation_candidates"]
    ]
    hits = scan_candidates()
    pool = []
    for row in rows:
        instance = row["instance_id"]
        mentions = sorted(hits.get(instance, []))
        design = [item for item in mentions if item.startswith("exclude:")]
        other = [item for item in mentions if item.startswith("other:")]
        ignored = [item for item in mentions if item.startswith("ignore:")]
        if instance in old:
            decision, reason = "excluded", "v15_preselection"
        elif design:
            decision, reason = "excluded", "design_or_execution_reference"
        elif other:
            decision, reason = "excluded", "design_like_file_class_default"
        elif (TOOLING / "swebench-verified" / instance.split("__", 1)[1]).exists():
            decision, reason = "excluded", "already_extracted"
        else:
            decision, reason = "eligible", "no_design_or_execution_reference"
        pool.append(
            {
                "instance_id": instance,
                "base_commit": row["base_commit"],
                "difficulty": row["difficulty"],
                "problem_statement_sha256": hashlib.sha256(
                    str(row["problem_statement"]).encode("utf-8")
                ).hexdigest(),
                "fail_to_pass_count": len(row["FAIL_TO_PASS"] or []),
                "pass_to_pass_count": len(row["PASS_TO_PASS"] or []),
                "selection_key": hashlib.sha256(f"{SALT}\0{instance}".encode()).hexdigest(),
                "decision": decision,
                "reason": reason,
                "design_references": design,
                "design_like_references": other,
                "mention_only_references": ignored,
            }
        )
    pool.sort(key=lambda entry: entry["selection_key"])
    eligible = [entry for entry in pool if entry["decision"] == "eligible"]
    reserved = set(recorded_six[3:6]) | {"django__django-13512"}
    for entry in pool:
        entry["reserved"] = entry["instance_id"] in reserved
        entry["reserved_reason"] = (
            "sealed untouched confirmation candidate"
            if entry["instance_id"] in set(recorded_six[3:6])
            else "unspent v15 candidate"
            if entry["instance_id"] == "django__django-13512"
            else ""
        )
    available = [entry for entry in eligible if not entry["reserved"]]
    return {
        "schema": "openai_agents_v16_candidate_pool",
        "date": "20261006",
        "paid_requests": 0,
        "rule_text": RULE_TEXT,
        "rule_source": "user ruling on the v16 development-set block (2026-10-06)",
        "rule_classes": {
            "exclude": [
                "freeze files",
                "protocols",
                "batch manifests",
                "audits",
                "anything under runs/**",
                "task registries, runners, tests and task files (default: design)",
            ],
            "do_not_exclude": [
                ".tooling/ drafts",
                "preselection lists",
                "memos/status documents",
                "errata",
            ],
        },
        "scan": {
            "roots": ["integrations", "experiments", "tests", "tasks"],
            "note": ".tooling/ is classified 'ignore' and never read for exclusion",
        },
        "salt": SALT,
        "rows_in_scope": len(pool),
        "excluded": sum(1 for entry in pool if entry["decision"] == "excluded"),
        "eligible_count": len(eligible),
        "recorded_six_reproduced": [entry["instance_id"] for entry in pool[:6]] == recorded_six,
        "persisted_all_candidates": True,
        "next_candidate_in_frozen_order": eligible[0]["instance_id"] if eligible else None,
        "reserved_candidates": sorted(reserved),
        "reservation_note": (
            "the sealed untouched confirmation candidates and the unspent v15 candidate are "
            "excluded from development selection by reservation, not by hand-picking; the first "
            "available candidate in frozen order is taken without any manual choice"
        ),
        "next_available_candidate_in_frozen_order": (
            available[0]["instance_id"] if available else None
        ),
        "available_order": [entry["instance_id"] for entry in available],
        "candidates": pool,
        "recovery_note": (
            "every candidate id, its sort key and its exclusion decision are persisted, so the "
            "next item is recoverable from this artifact rather than from a truncated list"
        ),
    }


def main() -> int:
    report = build()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"rule: {report['rule_text']}")
    print(
        f"rows {report['rows_in_scope']} | excluded {report['excluded']} | eligible "
        f"{report['eligible_count']} | first six reproduced: {report['recorded_six_reproduced']}"
    )
    for entry in report["candidates"][:12]:
        first = (entry["design_references"] or entry["design_like_references"] or ["-"])[0]
        print(
            f"  {entry['instance_id']:<26} {entry['decision']:<9} {entry['reason']:<34} {first[:70]}"
        )
    if report["next_candidate_in_frozen_order"]:
        print(f"next in frozen order: {report['next_candidate_in_frozen_order']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
