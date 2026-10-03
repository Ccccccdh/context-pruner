"""Zero-API snapshot gate on one real Django task checkout."""

from pathlib import Path
import sys

from integrations.openhands.same_prefix_fork_v54 import (
    capture_workspace,
    restore_workspace,
    tree_hashes,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations' / 'openhands'))
from validation_tasks_v49 import TASKS, prepare  # noqa: E402


def test_real_django_tree_restores_same_absolute_workspace(tmp_path: Path):
    task = 'django_referenced_window_wrapping'
    workspace = tmp_path / 'workspace'
    original = prepare(ROOT / '.tooling' / 'upstream', workspace, task)
    assert len(original) > 10000
    (workspace / 'delete_probe.txt').write_text('delete me\n', encoding='utf-8')
    frozen = capture_workspace(workspace, tmp_path / 'snapshot', experiment_root=tmp_path)
    assert len(frozen) >= len(original)

    allowed = workspace / TASKS[task]['allowed'][0]
    allowed.write_bytes(allowed.read_bytes() + b'\n# v54 temporary gate\n')
    (workspace / 'new_probe.txt').write_text('new\n', encoding='utf-8')
    (workspace / 'delete_probe.txt').unlink()
    restore_workspace(workspace, tmp_path / 'snapshot', experiment_root=tmp_path,
                      expected_hashes=frozen)
    assert tree_hashes(workspace) == frozen
    assert not (workspace / 'new_probe.txt').exists()
    assert (workspace / 'delete_probe.txt').read_text(encoding='utf-8') == 'delete me\n'
