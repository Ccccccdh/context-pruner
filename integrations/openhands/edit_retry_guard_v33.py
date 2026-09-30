"""Small, stateful guard against zero-change file edits in OpenHands runs."""


class EditRetryGuard:
    def __init__(self):
        self.consecutive_noops = 0
        self.needs_view = False

    def check(self, action):
        command = getattr(action, 'command', None)
        if command == 'view':
            self.consecutive_noops = 0
            self.needs_view = False
            return None
        if command != 'str_replace':
            if command in {'insert', 'create', 'undo_edit'}:
                self.consecutive_noops = 0
                self.needs_view = False
            return None
        old, new = getattr(action, 'old_str', None), getattr(action, 'new_str', None)
        if old == new:
            self.consecutive_noops += 1
            if self.consecutive_noops >= 2:
                self.needs_view = True
            return ('No edit occurred: old_str equals new_str. View the current narrow function range, '
                    'then replace only the lines that must change. Do not resend the same text.')
        if self.needs_view:
            return ('A narrow view is required after repeated zero-change edits. '
                    'View the current function range before the next replacement.')
        self.consecutive_noops = 0
        return None
