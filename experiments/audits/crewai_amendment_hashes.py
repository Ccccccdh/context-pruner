"""Amendment-aware hash resolution for the CrewAI freeze audits (agent-side parity).

Every freeze hashes the files its batch ran under, so a plain equality check is the
strongest possible statement: *nothing changed since the freeze*.  That is what the crewai
audits do, and it is what makes them report a mismatch once a pinned source is edited.

Freeze files themselves are never edited.  Post-freeze changes are recorded in

    integrations/crewai/R16_SOURCE_PIN_AMENDMENT_20261005.json

with the path, the reason, the hash the freeze records, the hash the file has now, and
which batch used which revision.  This module turns that record into a lookup so a check
can tell the two cases apart:

* the file changed to **exactly** the content the amendment registers -> a declared,
  reasoned, machine-checked change (the batch's numbers are unaffected, because the
  manifest pins the freeze file's own SHA256 and the runner's frozen hash);
* the file changed to anything else, or the amendment is missing -> still a mismatch.

The suppression is fail-closed and tamper-evident: it only ever accepts a digest the
amendment states explicitly, and any further edit re-triggers the mismatch.  It never
turns a drift into a pass for a *pin test*: ``tests/test_crewai_handoff_v16.py`` keeps the
strict equality assertion on purpose, so the drift itself stays visible.

This mirrors ``experiments/audits/amendment_hashes.py`` on the agents side.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AMENDMENT = ROOT / "integrations/crewai/R16_SOURCE_PIN_AMENDMENT_20261005.json"
LEDGER = ROOT / "integrations/crewai/R16_REVISION_AWARE_PIN_LEDGER_20261005.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECTIONS = ("files", "additional_files_edited_after_freeze", "artifacts_changed_after_freeze")


def amended_digests(amendment_path: Path | None = None) -> dict[str, set[str]]:
    """Map ``relative path -> digests the amendment registers as current content``."""
    path = Path(amendment_path) if amendment_path is not None else AMENDMENT
    table: dict[str, set[str]] = {}
    if not path.is_file():
        return table
    record = json.loads(path.read_text(encoding="utf-8"))
    for entry in record.get("amendments", []):
        for section in _SECTIONS:
            for item in entry.get(section) or []:
                _record(table, item.get("path"), item.get("new_sha256"))
        for item in entry.get("edits") or []:
            field = str(item.get("field", ""))
            relative = (
                field.split("source_sha256.", 1)[-1]
                if "source_sha256." in field else field
            )
            _record(table, relative, item.get("new_sha256"))
    return table


def _record(table: dict[str, set[str]], path: object, digest: object) -> None:
    text = str(path or "").replace("\\", "/")
    value = str(digest or "")
    if text and _SHA256.match(value):
        table.setdefault(text, set()).add(value)


def is_recorded_amendment(
    relative: str, current: str, table: dict[str, set[str]]
) -> bool:
    """True when ``current`` is exactly a digest the amendment registers for ``relative``."""
    return current in table.get(str(relative).replace("\\", "/"), ())


def source_pin_issues(
    freeze: dict,
    digest_of,
    amendment_path: Path | None = None,
) -> list[str]:
    """Pin issues for every source a freeze records, amendment-registered drifts honoured."""
    table = amended_digests(amendment_path)
    issues: list[str] = []
    for relative, wanted in (freeze.get("source_sha256") or {}).items():
        if not wanted:
            continue  # the freeze file's own entry: a file cannot contain its own digest
        current = digest_of(ROOT / relative)
        if current == wanted:
            continue
        if is_recorded_amendment(relative, current, table):
            continue
        issues.append(f"frozen source mismatch: {relative}")
    return issues


def registered_drifts(amendment_path: Path | None = None) -> dict[str, str]:
    """The paths this amendment registers a post-freeze digest for."""
    path = Path(amendment_path) if amendment_path is not None else AMENDMENT
    if not path.is_file():
        return {}
    record = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["path"]).replace("\\", "/"): str(item["new_sha256"])
        for entry in record.get("amendments", [])
        for item in entry.get("files") or []
    }


def check_pin(
    relative: str,
    frozen_sha256: str,
    digest_of,
    amendment_path: Path | None = None,
) -> tuple[bool, str]:
    """Fail-closed pin verdict for one frozen source.

    Three outcomes, and no fourth:

    * ``ok`` -- the file still hashes to what the freeze recorded (nothing changed);
    * ``ok (registered drift)`` -- the file hashes exactly to the digest the amendment
      registers for it.  This is a *declared, reasoned, machine-checked* change, and it is
      accepted **only** for that exact digest;
    * ``mismatch`` -- anything else, including an unregistered further edit, which is
      reported rather than skipped.

    The amendment is the only source of accepted post-freeze digests; a caller must never
    special-case a path to bypass this function.
    """
    current = digest_of(ROOT / relative)
    if current == frozen_sha256:
        return True, "ok"
    table = amended_digests(amendment_path)
    if is_recorded_amendment(relative, current, table):
        return True, "ok (registered drift)"
    registered = sorted(table.get(str(relative).replace("\\", "/"), ()))
    if registered:
        return False, (
            f"mismatch: {relative} hashes {current}, which is not the digest the "
            f"amendment registers ({', '.join(d[:12] for d in registered)}); an "
            "unregistered further edit is a gate failure"
        )
    return False, f"mismatch: {relative} hashes {current}, expected {frozen_sha256}"


def frozen_sources_valid(amendment_path: Path | None = None) -> bool:
    """Whether the amendment itself declares the frozen experiment sources still valid."""
    path = Path(amendment_path) if amendment_path is not None else AMENDMENT
    if not path.is_file():
        return True
    record = json.loads(path.read_text(encoding="utf-8"))
    return bool(record.get("frozen_experiment_sources_valid"))


def ledger_agrees(amendment_path: Path | None = None,
                  ledger_path: Path | None = None) -> bool:
    """Whether the ledger's registered digests equal the amendment's, exactly."""
    amendment_file = Path(amendment_path) if amendment_path is not None else AMENDMENT
    ledger_file = Path(ledger_path) if ledger_path is not None else LEDGER
    if not amendment_file.is_file() or not ledger_file.is_file():
        return False
    table = amended_digests(amendment_file)
    ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    registered = {
        str(path).replace("\\", "/"): set(digests)
        for path, digests in (ledger.get("registered_current_digests") or {}).items()
    }
    return registered == table


__all__ = [
    "AMENDMENT",
    "LEDGER",
    "amended_digests",
    "check_pin",
    "frozen_sources_valid",
    "is_recorded_amendment",
    "ledger_agrees",
    "registered_drifts",
    "source_pin_issues",
]
