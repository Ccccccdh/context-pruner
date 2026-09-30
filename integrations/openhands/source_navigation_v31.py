"""Bounded source navigation, shared by all experiment arms."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re


class SourceNavigator:
    def __init__(self, workspace, allowed_files, task_text, *, max_bytes=2_000_000):
        self.workspace = Path(workspace).resolve()
        self.allowed = {Path(path).resolve() for path in allowed_files}
        self.max_bytes = max_bytes
        self.terms = [term for term in re.findall(r'[A-Za-z_][A-Za-z_0-9]*', task_text)
                      if len(term) >= 4 and (term[0].isupper() or '_' in term)]
        self._cache = {}
        self._views = {}

    def _source(self, path):
        target = Path(path).resolve()
        if (not target.is_relative_to(self.workspace) or target not in self.allowed
                or target.suffix != '.py' or not target.is_file()):
            raise ValueError('Only allowed Python source files inside this workspace may be searched')
        size = target.stat().st_size
        if size > self.max_bytes:
            raise ValueError('Source file exceeds the bounded symbol-search size')
        raw = target.read_bytes()
        revision = hashlib.sha256(raw).hexdigest()
        return target, revision, raw.decode('utf-8')

    def _symbols(self, path, revision, source):
        key = path, revision
        if key not in self._cache:
            try:
                tree = ast.parse(source)
            except (SyntaxError, ValueError):
                self._cache[key] = []
            else:
                found = []

                def walk(body, parents=()):
                    for node in body:
                        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                            name = '.'.join((*parents, node.name))
                            found.append({'name': name, 'line': node.lineno,
                                          'end_line': node.end_lineno or node.lineno,
                                          'kind': 'class' if isinstance(node, ast.ClassDef) else 'function'})
                            walk(node.body, (*parents, node.name))
                walk(tree.body)
                self._cache[key] = found
        return self._cache[key]

    def search(self, path, query, *, max_hits=8):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 64:
            raise ValueError('Use a 2–64 character symbol query')
        target, revision, source = self._source(path)
        needle = query.strip().casefold()
        symbols = self._symbols(target, revision, source)
        hits = [item for item in symbols if needle in item['name'].casefold()][:max_hits]
        return {'path': str(target.relative_to(self.workspace)).replace('\\', '/'),
                'revision': revision[:12], 'query': query.strip(), 'matches': hits,
                'more_matches': sum(needle in item['name'].casefold() for item in symbols) > len(hits)}

    def view_hint(self, path, view_range):
        try:
            target, revision, source = self._source(path)
        except (OSError, UnicodeError, ValueError):
            return None
        line_count = source.count('\n') + 1
        span = tuple(view_range) if view_range else (1, line_count)
        key = target, revision
        history = self._views.setdefault(key, {'ranges': set(), 'last': None, 'streak': 0})
        repeated = span in history['ranges']
        history['streak'] = history['streak'] + 1 if history['last'] == span else 1
        history['last'] = span
        history['ranges'].add(span)
        if not repeated and len(history['ranges']) > 1:
            return None
        symbols = self._symbols(target, revision, source)
        matched = []
        for term in self.terms:
            options = [item for item in symbols if term.casefold() in item['name'].casefold()]
            options.sort(key=lambda item: (item['name'].casefold() != term.casefold(),
                                           item['name'].count('.'), item['line']))
            matched.extend(options[:4])
        matched = list({item['name']: item for item in matched}.values())[:8]
        if not repeated and not matched:
            return None
        lines = [f'SOURCE NAVIGATION: revision {revision[:12]}, {line_count} lines; '
                 f'range {list(span)} {"already read" if repeated else "recorded"}.']
        if history['streak'] >= 2:
            lines.append('This unchanged range was requested repeatedly. Use a listed symbol line or inspect an unread range before another view.')
        if matched:
            lines.append('Task-related symbols (exact source line spans): ' +
                         '; '.join(f'{item["name"]} {item["line"]}-{item["end_line"]}' for item in matched))
        lines.append('scoped_symbols can locate other definitions by name; results are bounded and read-only.')
        return '\n'.join(lines)
