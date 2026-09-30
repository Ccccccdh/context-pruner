"""Revision-aware, bounded facts for a compressed agent conversation."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass
class TaskState:
    objective: str
    edit_scope: str
    max_chars: int = 3000
    verification_revision: str = ''
    verification_result: str = ''
    feedback_revision: str = ''
    feedback_summary: str = ''

    def record_verification(self, revision: str, result: str) -> None:
        self.verification_revision = revision
        self.verification_result = result[:1000]

    def record_feedback(self, revision: str, summary: str) -> None:
        self.feedback_revision = revision
        self.feedback_summary = summary[:1200]

    def render(self, current_revision: str) -> str:
        lines = ['CURRENT TASK STATE (host-observed; source edits invalidate verification):',
                 'Objective: ' + self.objective,
                 'Edit scope: ' + self.edit_scope,
                 'Current source revision: ' + current_revision]
        if self.verification_result:
            status = ('current' if current_revision == self.verification_revision
                      else 'STALE after source edit; rerun verification')
            lines.append(f'Observed verification [{status}; revision={self.verification_revision}]: '
                         + self.verification_result)
        if self.feedback_summary:
            status = ('current' if current_revision == self.feedback_revision
                      else 'from earlier revision; recheck after edits')
            lines.append(f'Host feedback [{status}; revision={self.feedback_revision}]: '
                         + self.feedback_summary)
        return '\n'.join(lines)[:self.max_chars]


def source_revision(paths) -> str:
    """Hash only authorized source bytes, with stable relative labels supplied by caller."""
    digest = sha256()
    for label, path in sorted(paths, key=lambda item: item[0]):
        digest.update(label.encode('utf-8') + b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()[:16]
