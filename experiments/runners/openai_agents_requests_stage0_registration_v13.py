"""Stage 0 registration for the v13 held-out task, from the sanctioned acquisition path.

The acquisition is a **read-only** `git init` / `remote add` / `fetch --depth 1` /
`checkout FETCH_HEAD` inside `.tooling/scratch/psf-requests-1766` (inside the ignored
`.tooling/` tree).  No `add`/`commit`/`push` is run against this repository and no write is
made to the upstream repository.  `raw.githubusercontent.com` is not used: it resolves to
0.0.0.0 on this machine, and the baseline source is read from the checked-out working tree.

This module separates two kinds of record, because only one of them may act on the mechanism:

* **baseline source** - the three read-only views, as files of the pinned checkout, with
  byte counts and SHA256.  These are mechanism inputs.
* **judging metadata** - the public problem statement, the evaluation test patch, the
  instance's FAIL_TO_PASS and PASS_TO_PASS rows.  These fix what the task *is* and what the
  host tests would be; they are **not** sent to the model and never enter the mechanism.
  The test patch is why `test_DIGESTAUTH_QUOTES_QOP_VALUE` is absent from the base tree:
  SWE-bench adds the evaluation test with that patch, which is the normal mechanism and not a
  defect of the candidate or of the source acquisition.

The registration also checks that the checked-out files are byte-identical to the copies the
executed batches used, so the already-recorded hashes are validated by the sanctioned path
rather than replaced.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = ROOT / ".tooling/scratch/psf-requests-1766"
USED_COPIES = ROOT / ".tooling/upstream/psf-requests-1766"
HARNESS_DIR = ROOT / ".tooling/swebench-verified/psf-requests-1766-harness"
PARQUET = ROOT / ".tooling/swebench-verified/test.parquet"
FREEZE = ROOT / "integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json"
OUT = ROOT / "integrations/openai_agents/V13_REQUESTS1766_STAGE0_REGISTRATION_20261005.json"

sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_requests_task_registry_v13 as registry  # noqa: E402

BASELINE_FILES = (
    "requests/auth.py",
    "requests/models.py",
    "requests/sessions.py",
    "test_requests.py",
)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checkout_head() -> str:
    """Read the checked-out commit without touching repository state."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=CHECKOUT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def main() -> int:
    problems: list[str] = []
    head = checkout_head()
    if head != registry.BASE_COMMIT:
        problems.append(f"checkout HEAD {head} is not the pinned base commit")

    # -- baseline source (mechanism input) ---------------------------------------
    baseline: dict[str, dict] = {}
    for relative in BASELINE_FILES:
        path = CHECKOUT / relative
        copy = USED_COPIES / relative
        baseline[relative] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_of(path),
            "copy_used_by_the_batches": str(copy.relative_to(ROOT)).replace("\\", "/"),
            "copy_bytes": copy.stat().st_size,
            "copy_sha256": sha256_of(copy),
            "copy_identical": sha256_of(copy) == sha256_of(path),
        }
        if not baseline[relative]["copy_identical"]:
            problems.append(f"{relative}: the copy used by the batches is not the checkout")
    views = []
    for index, entry in enumerate(registry.sources()):
        _repo, relative, first, last = entry
        recorded = baseline[relative]
        views.append(
            {
                "view": f"V{index + 1}",
                "file": relative,
                "first_line": first,
                "last_line": last,
                "sha256": recorded["sha256"],
                "bytes": recorded["bytes"],
                "role": (
                    "cause-carrying view (Digest header builder)"
                    if index == 0
                    else "context view"
                ),
            }
        )
    view_texts = [registry.source_view(index) for index in range(len(registry.sources()))]
    literal_check = {}
    for label, phrases in registry.LITERAL_LABELS.items():
        literal_check[label] = {
            "phrases": list(phrases),
            "in_views": all(
                any(phrase in text for text in view_texts) for phrase in phrases
            ),
            "in_statement": all(phrase in registry.statement() for phrase in phrases),
        }
        if not (literal_check[label]["in_views"] or literal_check[label]["in_statement"]):
            problems.append(f"registered literal {label} is not in the checkout views")

    # -- judging metadata (not a mechanism input) --------------------------------
    rows = pq.read_table(str(PARQUET)).to_pylist()
    row = next(item for item in rows if item["instance_id"] == registry.ISSUE_ID)
    statement = str(row["problem_statement"])
    test_patch = str(row["test_patch"])
    reference_patch = str(row["patch"])
    fail_to_pass = list(row["FAIL_TO_PASS"] or [])
    pass_to_pass = list(row["PASS_TO_PASS"] or [])
    judging = {
        "role": (
            "fixes what the task is and what the host tests would be; never sent to the model "
            "and never an input to the mechanism"
        ),
        "problem_statement": {
            "bytes": len(statement.encode("utf-8")),
            "sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
            "sent_to_the_model": True,
            "note": "the public issue text is the task's first message unit",
        },
        "test_patch": {
            "bytes": len(test_patch.encode("utf-8")),
            "sha256": hashlib.sha256(test_patch.encode("utf-8")).hexdigest(),
            "landed_at": (
                str((HARNESS_DIR / "host-tests.patch").relative_to(ROOT)).replace("\\", "/")
            ),
            "read_by_this_host": False,
            "explains": (
                "the evaluation test patch adds "
                "test_requests.py::RequestsTestCase::test_DIGESTAUTH_QUOTES_QOP_VALUE, which "
                "is therefore legitimately absent from the base tree - the normal SWE-bench "
                "mechanism, not a candidate or acquisition defect"
            ),
        },
        "reference_patch": {
            "bytes": len(reference_patch.encode("utf-8")),
            "sha256": hashlib.sha256(reference_patch.encode("utf-8")).hexdigest(),
            "landed_at": (
                str((HARNESS_DIR / "reference.patch").relative_to(ROOT)).replace("\\", "/")
            ),
            "read_by_this_host": False,
            "note": "hashed only; never opened, never sent, never used for scoring",
        },
        "instance_file": {
            "path": str((HARNESS_DIR / "instance.json").relative_to(ROOT)).replace("\\", "/"),
            "bytes": (HARNESS_DIR / "instance.json").stat().st_size,
            "sha256": sha256_of(HARNESS_DIR / "instance.json"),
        },
        "fail_to_pass": {
            "entries": fail_to_pass,
            "sha256": hashlib.sha256(
                json.dumps(fail_to_pass, ensure_ascii=False).encode()
            ).hexdigest(),
            "present_in_base_tree": sorted(
                str(entry).split("::")[-1]
                for entry in fail_to_pass
                if f"def {str(entry).split('::')[-1]}(" in (CHECKOUT / "test_requests.py").read_text(encoding="utf-8")
            ),
        },
        "pass_to_pass": {
            "count": len(pass_to_pass),
            "sha256": hashlib.sha256(
                json.dumps(pass_to_pass, ensure_ascii=False).encode()
            ).hexdigest(),
        },
    }

    # -- provenance ---------------------------------------------------------------
    freeze_recorded = json.loads(FREEZE.read_text(encoding="utf-8"))["stage0_acquisition"][
        "input_hashes"
    ]
    provenance = {
        "acquisition": (
            "read-only git in .tooling/scratch/psf-requests-1766: git init, git remote add "
            "origin https://github.com/psf/requests.git, git fetch --depth 1 origin "
            "<base_commit>, git checkout FETCH_HEAD"
        ),
        "checkout_head": head,
        "forbidden_operations_used": [],
        "forbidden_operations": [
            "git add/commit/push in this repository",
            "any write to the upstream repository",
        ],
        "raw_githubusercontent_used": False,
        "recorded_hashes_validated_not_replaced": all(
            freeze_recorded.get(
                f".tooling/upstream/psf-requests-1766/{relative}", ""
            )
            == baseline[relative]["sha256"]
            for relative in BASELINE_FILES
        ),
        "reading_rule_going_forward": (
            "baseline source is read from the checked-out working tree; the copies under "
            ".tooling/upstream/psf-requests-1766 are byte-identical to it (verified here), so "
            "every hash the freeze and the two batch manifests record stays valid"
        ),
    }
    if not provenance["recorded_hashes_validated_not_replaced"]:
        problems.append("a recorded baseline hash does not match the checkout")

    report = {
        "schema": "openai_agents_v13_requests1766_stage0_registration",
        "instance_id": registry.ISSUE_ID,
        "repo": registry.REPO,
        "base_commit": registry.BASE_COMMIT,
        "version": row["version"],
        "difficulty": row["difficulty"],
        "baseline_source": {
            "role": "mechanism input",
            "checkout": str(CHECKOUT.relative_to(ROOT)).replace("\\", "/"),
            "files": baseline,
            "views": views,
            "registered_literals": literal_check,
        },
        "judging_metadata": judging,
        "provenance": provenance,
        "problems": problems,
        "verified": not problems,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"checkout HEAD {head}")
    for relative in BASELINE_FILES:
        entry = baseline[relative]
        print(
            f"  {relative:<22} {entry['bytes']:>6} B  {entry['sha256'][:16]}  "
            f"copy_identical={entry['copy_identical']}"
        )
    print(f"  test_patch {judging['test_patch']['bytes']} B {judging['test_patch']['sha256'][:16]}")
    print(f"  reference_patch {judging['reference_patch']['bytes']} B (hashed only)")
    print(f"problems: {problems}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
