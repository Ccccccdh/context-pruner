"""Bounded, complete failure index for fixed test runs.

The input is a saved test log, never a model-generated claim. Every FAIL/ERROR
heading is kept, while each traceback is reduced to its final diagnostic lines.
An import failure with no unittest heading keeps its traceback tail instead.
"""
from __future__ import annotations

import re
from pathlib import Path

_HEADING = re.compile(r'(?m)^(FAIL|ERROR): (.+)$')
_SEPARATOR = re.compile(r'(?m)^={20,}$')
_RESULT = re.compile(r'(?m)^(Ran \d+ tests?[^\n]*|FAILED \([^\n]*\)|OK)$')


def format_test_feedback(log: str, *, max_chars: int = 14000,
                         detail_chars: int = 700,
                         workspace_root: str | Path | None = None) -> str:
    """Index every named failure and include short, actionable diagnostics."""
    if max_chars < 2000 or detail_chars < 100:
        raise ValueError('Feedback limits too small')
    normalized = log.replace('\r\n', '\n').replace('\r', '\n')
    if workspace_root is not None:
        prefix = str(workspace_root).rstrip('\\/')
        normalized = normalized.replace(prefix + '\\', '<workspace>/')
        normalized = normalized.replace(prefix + '/', '<workspace>/')
    results = _RESULT.findall(normalized)
    headings = list(_HEADING.finditer(normalized))
    lines = ['Fixed test run: ' + ('; '.join(results) if results else 'no test summary')]
    if not headings:
        if results and results[-1] == 'OK':
            return '\n'.join(lines)
        tail = '\n'.join(normalized.rstrip().splitlines()[-14:])
        return '\n'.join([*lines, 'No individual test headings; startup/import failure tail:',
                          tail[-max_chars + len('\n'.join(lines)) - 80:]])

    # Headings are always listed in full, even when a long traceback leaves
    # room for fewer details. A name-only index is better than the log tail.
    lines.append(f'Named failures and errors ({len(headings)}):')
    detail_blocks = []
    for index, heading in enumerate(headings, 1):
        label = f'{index}. {heading.group(1)}: {heading.group(2)}'
        lines.append(label)
        end = headings[index].start() if index < len(headings) else len(normalized)
        separator = _SEPARATOR.search(normalized, heading.end(), end)
        if separator:
            end = separator.start()
        block = normalized[heading.end():end]
        useful = [line.strip() for line in block.splitlines()
                  if line.strip() and not set(line.strip()) <= {'-', '='}]
        tail = '\n'.join(useful[-7:])[-detail_chars:]
        detail_blocks.append((index, tail))

    indexed = '\n'.join(lines)
    if len(indexed) >= max_chars:
        # Preserve every heading whenever possible; extremely large suites
        # state how many names could fit rather than silently returning a tail.
        return indexed[:max_chars - 80] + f'\n[heading index truncated; total={len(headings)}]'
    detail_lines = ['Selected diagnostic tails:']
    omitted = 0
    for index, tail in detail_blocks:
        addition = f'[{index}]\n{tail}'
        if len(indexed) + len('\n'.join(detail_lines)) + len(addition) + 64 > max_chars:
            omitted += 1
            continue
        detail_lines.append(addition)
    if omitted:
        detail_lines.append(f'[diagnostic tails omitted for {omitted} indexed failures]')
    return '\n'.join([indexed, *detail_lines])
