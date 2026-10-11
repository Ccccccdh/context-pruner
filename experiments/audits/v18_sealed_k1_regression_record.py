"""Record the focused regression the v23 K=1 round requires, with its complete output.

The round's discipline says a pass rests on the complete output of one run, so this script runs the
focused test set once, keeps the whole stdout, and separates the failures into

* the one existing failure this round is allowed to carry -
  ``tests/test_openai_agents_v16_live_runner_zero_api.py::RegistryTests::
  test_problem_statement_hash_reconciliation_is_recorded``, which asserts the v16 *development*
  registry records a reconciliation for a task whose statement hash was double-hashed, a property of
  a different task set that this round does not touch, and
* anything else, which is a failure of the round.

The full suite is deliberately not run: earlier rounds recorded that it produces no output for
about fifty minutes, which is not a usable gate.

Environment (recorded in the artifact): the interpreter is the one that carries the OpenAI Agents
SDK's dependencies, with the repository virtual environment's site-packages on ``PYTHONPATH``,
``TEMP`` and ``TMP`` inside the repository, and pytest's cache disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "integrations/openai_agents/V18_SEALED_K1_FOCUSED_REGRESSION_20261010.json"
TMP = ROOT / ".tooling/tmp"

PYTEST_PYTHON = ROOT / ".venv-openhands/Scripts/python.exe"
SITE_PACKAGES = ROOT / ".venv/Lib/site-packages"

TESTS = (
    "tests/test_openai_agents_v18_sealed_window_k1.py",
    "tests/test_openai_agents_v22_window_dose.py",
    "tests/test_openai_agents_v21.py",
    "tests/test_openai_agents_v20.py",
    "tests/test_openai_agents_v17.py",
    "tests/test_openai_agents_v16_live_runner_zero_api.py",
    "tests/test_openai_agents_structured_final_v16.py",
)

#: The only existing failure this round may carry, named exactly.
ALLOWED_FAILURES = (
    "tests/test_openai_agents_v16_live_runner_zero_api.py::RegistryTests::"
    "test_problem_statement_hash_reconciliation_is_recorded",
)

SUMMARY = re.compile(r"^(FAILED|ERROR) (\S+)", flags=re.MULTILINE)
COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> dict:
    TMP.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SITE_PACKAGES) + os.pathsep + str(ROOT)
    env["TEMP"] = str(TMP)
    env["TMP"] = str(TMP)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    argv = [
        str(PYTEST_PYTHON),
        "-m",
        "pytest",
        *TESTS,
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(TMP / "pytest-v23-basetemp"),
        "-q",
        "--tb=line",
        "-rf",
    ]
    result = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True, check=False)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    failures = sorted({match.group(2) for match in SUMMARY.finditer(stdout)})
    counts = {name: int(value) for value, name in COUNT.findall(stdout)}
    unexpected = [name for name in failures if name not in ALLOWED_FAILURES]
    allowed_seen = [name for name in failures if name in ALLOWED_FAILURES]
    return {
        "schema": "openai_agents_v18_sealed_k1_focused_regression",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_requests_spent": 0,
        "produced_by": "experiments/audits/v18_sealed_k1_regression_record.py",
        "command": argv,
        "environment": {
            "interpreter": str(PYTEST_PYTHON),
            "why_not_the_repository_venv": (
                "the repository virtual environment carries the OpenAI Agents SDK but not pytest; "
                "this interpreter carries pytest but not the SDK, so the SDK's site-packages are on "
                "PYTHONPATH"
            ),
            "pythonpath": [str(SITE_PACKAGES), str(ROOT)],
            "temp": str(TMP),
            "basetemp": str(TMP / "pytest-v23-basetemp"),
            "cache_provider_disabled": True,
            "full_suite_not_run_because": (
                "an earlier round recorded that the full suite produces no output for about fifty "
                "minutes; a focused set is what this gate can honestly run"
            ),
        },
        "tests_selected": list(TESTS),
        "test_file_sha256": {
            relative: sha256_of(ROOT / relative) for relative in TESTS
        },
        "exit_code": int(result.returncode),
        "summary_counts": counts,
        "failed_node_ids": failures,
        "allowed_existing_failures": list(ALLOWED_FAILURES),
        "allowed_failures_observed": allowed_seen,
        "unexpected_failures": unexpected,
        "stderr": stderr,
        "stdout": stdout,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "ok": not unexpected and result.returncode in (0, 1),
        "rule": (
            "the focused set passes except, at most, the one existing failure named above; any "
            "other failure fails this round"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="print only, write nothing")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    payload = run()
    tail = payload["stdout"].splitlines()[-12:]
    for line in tail:
        print(line)
    print("-" * 90)
    print(f"exit_code={payload['exit_code']} counts={payload['summary_counts']}")
    print(f"failed_node_ids={payload['failed_node_ids']}")
    print(f"allowed_failures_observed={payload['allowed_failures_observed']}")
    print(f"unexpected_failures={payload['unexpected_failures']}")
    print(f"ok={payload['ok']}")
    if not args.probe:
        out = Path(args.out)
        if out.exists():
            raise SystemExit(f"refusing to overwrite the existing artifact {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
