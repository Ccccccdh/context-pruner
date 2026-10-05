"""Amendment-aware hash resolution for the OpenAI Agents freeze audits.

Every freeze hashes the files its batch ran under, so a plain equality check is the
strongest possible statement: *nothing changed since the freeze*. That is what the v7 and
v9 audits do, and it is what made them report a mismatch when the applicability-boundary
document was later updated to its closing version.

Freeze files themselves are never edited. Post-freeze changes are recorded in

    integrations/openai_agents/REPO_DIAGNOSTIC_V11_FREEZE_AMENDMENT_20261004.json

with the path, the reason, the hash the freeze records and the hash the file has now. This
module turns that record into a lookup so an audit can tell the two cases apart:

* the file changed to exactly the content the amendment records -> not an issue (the change
  is declared, reasoned and machine-checked, and the batch's numbers are unaffected because
  the manifest pins the freeze file's own SHA256);
* the file changed to anything else, or the amendment file is missing -> still a mismatch.

The suppression is therefore fail-closed and tamper-evident: it only ever accepts a digest
that the amendment states explicitly, and any further edit re-triggers the mismatch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AMENDMENT = (
    ROOT / "integrations/openai_agents/REPO_DIAGNOSTIC_V11_FREEZE_AMENDMENT_20261004.json"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECTIONS = (
    "files",
    "additional_files_edited_after_freeze",
    "artifacts_changed_after_freeze",
)


def amended_digests(amendment_path: Path | None = None) -> dict[str, set[str]]:
    """Map ``relative path -> digests the amendment records as its current content``."""
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
            relative = field.split("source_sha256.", 1)[-1] if "source_sha256." in field else field
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
    """True when ``current`` is exactly a digest the amendment records for ``relative``."""
    return current in table.get(str(relative).replace("\\", "/"), ())


def freeze_hash_issues(
    freeze: dict,
    digest_of,
    amendment_path: Path | None = None,
) -> list[str]:
    """Hash issues for every path a freeze pins, amendment records honoured."""
    table = amended_digests(amendment_path)
    issues: list[str] = []
    for relative, wanted in {**freeze["source_sha256"], **freeze["public_input_sha256"]}.items():
        current = digest_of(ROOT / relative)
        if current == wanted:
            continue
        if is_recorded_amendment(relative, current, table):
            continue
        issues.append(f"hash mismatch: {relative}")
    return issues
