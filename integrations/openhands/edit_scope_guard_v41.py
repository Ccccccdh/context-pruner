"""Preflight OpenHands editor actions against the immutable task edit scope."""
from __future__ import annotations

from pathlib import Path


EDIT_COMMANDS = frozenset({'str_replace', 'insert', 'create', 'undo_edit'})


def scope_error(command: str, path: str, workspace: Path,
                allowed_files: list[Path]) -> str | None:
    """Return a short actionable refusal before edit retries consume calls."""
    root = Path(workspace).resolve()
    target = Path(path).resolve()
    if not target.is_relative_to(root):
        return 'Outside workspace blocked.'
    if command not in EDIT_COMMANDS:
        return None
    allowed = {Path(item).resolve() for item in allowed_files}
    if target in allowed:
        return None
    display = ', '.join(sorted(item.relative_to(root).as_posix() for item in allowed))
    return (f'Read-only source: {target.relative_to(root).as_posix()}. '
            f'Edit only these task files: {display}. '
            'Read this file for context, then make the needed change in an allowed file.')
