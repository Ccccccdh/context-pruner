"""Concise failure causes and regression delta from saved host test logs."""
from __future__ import annotations

from collections import Counter
import re

from integrations.openhands.feedback_v40 import format_test_feedback

_HEADING = re.compile(r'(?m)^(FAIL|ERROR): (.+)$')
_CAUSE = re.compile(r'(?m)^(?:[\w.]+(?:Error|Exception|Failure): .+|AssertionError: .+)$')


def failure_index(log: str):
    normalized = log.replace('\r\n', '\n').replace('\r', '\n')
    headings = list(_HEADING.finditer(normalized))
    found = []
    for i, heading in enumerate(headings):
        block = normalized[heading.end():headings[i + 1].start() if i + 1 < len(headings) else len(normalized)]
        causes = _CAUSE.findall(block)
        found.append((heading.group(1), heading.group(2), causes[-1] if causes else 'cause unavailable'))
    return found


def format_correction_feedback(log: str, *, previous_log: str | None = None,
                               max_chars: int = 14000, workspace_root=None) -> str:
    current = failure_index(log)
    lines = []
    if previous_log is not None:
        previous = failure_index(previous_log)
        old = Counter((kind, name) for kind, name, _ in previous)
        new = Counter((kind, name) for kind, name, _ in current)
        introduced = list((new - old).elements())
        resolved = list((old - new).elements())
        lines.append(f'Regression delta: {len(previous)} -> {len(current)} named failures/errors; '
                     f'new={len(introduced)}, resolved={len(resolved)}.')
        if introduced:
            lines.append('New failures: ' + '; '.join(f'{kind}: {name}' for kind, name in introduced[:10]))
    causes = Counter(cause for _, _, cause in current)
    if causes:
        lines.append('Repeated root diagnostics: ' + '; '.join(
            f'{count}x {cause}' for cause, count in causes.most_common(6)))
    remaining = max(2000, max_chars - len('\n'.join(lines)) - 2)
    lines.append(format_test_feedback(log, max_chars=remaining,
                                      workspace_root=workspace_root))
    return '\n'.join(lines)[:max_chars]
