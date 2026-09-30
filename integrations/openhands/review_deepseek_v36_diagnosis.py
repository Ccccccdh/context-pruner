"""Independently check the v36 diagnosis in a disposable pytest copy; no API."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from validation_tasks_v36 import ROOT, evaluate, hashes, prepare


OUTPUT = ROOT / 'runs/stage5-openhands/pytest-mark-mro-v36-offline-review-01'
SOURCE = ROOT / '.tooling/upstream'
TASK = 'pytest_mark_mro'


def main() -> None:
    assert not OUTPUT.exists(), 'Preserve earlier review output; choose a new path.'
    OUTPUT.mkdir(parents=True)
    workspace = OUTPUT / 'candidate-workspace'
    original = prepare(SOURCE, workspace, TASK)
    path = workspace / 'src/_pytest/mark/structures.py'
    source = path.read_text(encoding='utf-8')
    start = source.index('def get_unpacked_marks(')
    end = source.index('\ndef normalize_mark_list(', start)
    replacement = '''def _own_pytestmark(obj: object) -> list:
    """Read marks stored on obj itself, without class inheritance."""
    mark_list = obj.__dict__.get("pytestmark", []) if inspect.isclass(obj) else getattr(obj, "pytestmark", [])
    return mark_list if isinstance(mark_list, list) else [mark_list]


def get_unpacked_marks(obj: object, *, consider_mro: bool = True) -> List[Mark]:
    """Collect own marks, then class base marks in MRO order if requested."""
    if inspect.isclass(obj) and consider_mro:
        mark_list = [mark for cls in obj.__mro__ for mark in _own_pytestmark(cls)]
    else:
        mark_list = _own_pytestmark(obj)
    return list(normalize_mark_list(mark_list))

'''
    source = source[:start] + replacement + source[end:]
    old = 'obj.pytestmark = [*get_unpacked_marks(obj), mark]'
    assert source.count(old) == 1
    source = source.replace(old, 'obj.pytestmark = [*_own_pytestmark(obj), mark]')
    path.write_text(source, encoding='utf-8', newline='\n')
    changed = sorted(key for key, value in hashes(workspace).items() if original.get(key) != value)
    assert changed == ['src/_pytest/mark/structures.py'], changed
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    public = evaluate(workspace, TASK, OUTPUT / 'public-regression', regression_only=True)
    host = evaluate(workspace, TASK, OUTPUT / 'host-target')
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    result = {
        'kind': 'offline-development-diagnosis',
        'model_api_requests': 0,
        'candidate_source_sha256': before,
        'candidate_source_unchanged_by_tests': before == after,
        'changed_files': changed,
        'public_regression': public,
        'host_target': host,
        'limitation': 'Selected test_mark.py only, not full upstream suite or official SWE-bench harness.',
    }
    (OUTPUT / 'review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert public['passed'] and host['passed'] and before == after


if __name__ == '__main__':
    main()
