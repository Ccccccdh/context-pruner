"""Read-only API checks and revision-aware repeat hints for allowed source."""
from __future__ import annotations

import ast

from integrations.openhands.source_navigation_v31 import SourceNavigator


class SourceNavigatorV42(SourceNavigator):
    def inspect_member(self, path, class_name: str, member_name: str):
        """Report only definitions observed in this file; do not assume inheritance."""
        if not class_name.isidentifier() or not member_name.isidentifier():
            raise ValueError('Class and member must be Python identifiers')
        target, revision, source = self._source(path)
        tree = ast.parse(source)
        classes = [node for node in ast.walk(tree)
                   if isinstance(node, ast.ClassDef) and node.name == class_name]
        if len(classes) != 1:
            raise ValueError('Class definition absent or ambiguous in requested file')
        cls = classes[0]
        members = [node for node in cls.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        found = next((node for node in members if node.name == member_name), None)
        return {'path': str(target.relative_to(self.workspace)).replace('\\', '/'),
                'revision': revision[:12], 'class': class_name, 'member': member_name,
                'defined_here': found is not None,
                'signature': (ast.unparse(found.args) if found else None),
                'line': (found.lineno if found else None),
                'defined_methods': [node.name for node in members][:60],
                'scope_note': 'This checks only this class body; inherited and dynamic members are not resolved.'}

    def view_hint(self, path, view_range):
        hint = super().view_hint(path, view_range)
        if hint:
            return hint
        try:
            target, revision, source = self._source(path)
        except (OSError, UnicodeError, ValueError):
            return None
        history = self._views.get((target, revision))
        if not history or not view_range:
            return None
        start, end = view_range
        if end == -1:
            end = source.count('\n') + 1
        prior = [span for span in history['ranges'] if span != tuple(view_range)]
        if any(lo <= start and end <= (source.count('\n') + 1 if hi == -1 else hi)
               for lo, hi in prior):
            return (f'SOURCE NAVIGATION: unchanged revision {revision[:12]}; '
                    f'range {list(view_range)} is already covered by an earlier view. '
                    'Use scoped_symbols or inspect a different dependency if more evidence is needed.')
        return None
