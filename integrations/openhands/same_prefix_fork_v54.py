"""Isolate an OpenHands conversation fork at a frozen model-call boundary.

This module does not call a model.  It exists to make the SDK's ``fork()``
workspace sharing explicit before a paid same-prefix experiment is attempted.
The caller must give each branch an agent whose scoped tools point at the
branch workspace, and must snapshot the source workspace before any branch
continues.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import os
from pathlib import Path
import shutil
from typing import Any

from openhands.sdk.workspace import LocalWorkspace


def tree_hashes(root: Path) -> dict[str, str]:
    """Hash every regular file; callers should pass a bounded task workspace."""
    root = root.resolve()
    paths = [path for path in root.rglob('*') if path.is_file()]

    def digest(path: Path) -> tuple[str, str]:
        return (str(path.relative_to(root)).replace('\\', '/'),
                hashlib.sha256(path.read_bytes()).hexdigest())

    # The Django checkout has ~20k small files.  Serial open/read hashing took
    # most of the 14-minute real-tree gate; file I/O overlaps well on Windows.
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(digest, sorted(paths)))


def _within_experiment(path: Path, experiment_root: Path) -> Path:
    experiment_root = experiment_root.resolve()
    path = path.resolve()
    if path == experiment_root or not path.is_relative_to(experiment_root):
        raise ValueError('workspace operation outside experiment root')
    return path


def _reject_links(root: Path) -> None:
    # Resolving every ordinary file is exceptionally slow on Windows Django
    # trees (~20k files).  The resolved root is already checked by
    # _within_experiment; rejecting every directory/file link means lexical
    # descendants cannot escape it during copy or removal.
    for path in root.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError(f'linked entry in workspace: {path.relative_to(root)}')


def _copytree_parallel(source: Path, destination: Path) -> None:
    """Copy a link-free source tree with concurrent small-file transfers."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    files: list[tuple[Path, Path]] = []
    for current, directories, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        target_dir = destination / current_path.relative_to(source)
        for name in directories:
            item = current_path / name
            if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
                raise ValueError(f'linked entry in workspace: {item.relative_to(source)}')
            (target_dir / name).mkdir()
        for name in filenames:
            item = current_path / name
            if item.is_symlink():
                raise ValueError(f'linked entry in workspace: {item.relative_to(source)}')
            files.append((item, target_dir / name))

    def transfer(pair: tuple[Path, Path]) -> None:
        shutil.copy2(*pair)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(transfer, files))


def capture_workspace(workspace: Path, snapshot: Path, *, experiment_root: Path) -> dict[str, str]:
    """Capture a same-path branch checkpoint inside an explicit experiment root."""
    workspace = _within_experiment(workspace, experiment_root)
    snapshot = _within_experiment(snapshot, experiment_root)
    if snapshot.is_relative_to(workspace) or workspace.is_relative_to(snapshot):
        raise ValueError('workspace and snapshot must be separate')
    if snapshot.exists() or not workspace.is_dir():
        raise ValueError('snapshot exists or workspace is missing')
    _reject_links(workspace)
    before = tree_hashes(workspace)
    _copytree_parallel(workspace, snapshot)
    if tree_hashes(snapshot) != before:
        raise RuntimeError('snapshot differs from workspace')
    return before


def restore_workspace(workspace: Path, snapshot: Path, *, experiment_root: Path,
                      expected_hashes: dict[str, str]) -> None:
    """Restore one scratch workspace before a sequential fork continuation."""
    workspace = _within_experiment(workspace, experiment_root)
    snapshot = _within_experiment(snapshot, experiment_root)
    if snapshot.is_relative_to(workspace) or workspace.is_relative_to(snapshot):
        raise ValueError('workspace and snapshot must be separate')
    if not snapshot.is_dir() or tree_hashes(snapshot) != expected_hashes:
        raise ValueError('frozen snapshot missing or changed')
    _reject_links(snapshot)
    if workspace.exists():
        _reject_links(workspace)
        shutil.rmtree(workspace)
    _copytree_parallel(snapshot, workspace)
    if tree_hashes(workspace) != expected_hashes:
        raise RuntimeError('restored workspace differs from checkpoint')


def fork_to_isolated_workspace(
    source: Any,
    *,
    workspace: Path,
    agent: Any,
    expected_source_hashes: dict[str, str] | None = None,
) -> Any:
    """Copy the workspace, fork event state, and bind the fork to that copy.

    ``LocalConversation.fork`` currently creates its child with the *source*
    workspace.  Rebinding both the conversation and its state is necessary:
    one is read by tool creation and the other by the SDK runner.  This uses
    the SDK state attribute deliberately and must be re-gated on SDK upgrades.
    """
    source_root = Path(source.workspace.working_dir).resolve()
    workspace = workspace.resolve()
    if source_root == workspace or workspace.is_relative_to(source_root):
        raise ValueError('fork workspace must be separate from source')
    if workspace.exists():
        raise FileExistsError(workspace)
    before = tree_hashes(source_root)
    if expected_source_hashes is not None and before != expected_source_hashes:
        raise ValueError('source workspace changed since checkpoint')
    _reject_links(source_root)
    _copytree_parallel(source_root, workspace)
    if tree_hashes(workspace) != before:
        raise RuntimeError('fork workspace copy differs from checkpoint')

    fork = fork_with_agent(source, agent)
    local = LocalWorkspace(working_dir=workspace)
    fork.workspace = local
    fork._state.workspace = local
    if Path(fork.workspace.working_dir).resolve() != workspace:
        raise RuntimeError('conversation workspace rebinding failed')
    if Path(fork._state.workspace.working_dir).resolve() != workspace:
        raise RuntimeError('state workspace rebinding failed')
    if tree_hashes(source_root) != before:
        raise RuntimeError('source workspace changed while forking')
    return fork


def fork_with_agent(source: Any, agent: Any) -> Any:
    """Copy a prefix while retaining branch-only LLM runtime and condenser.

    The SDK serializes the supplied agent during ``fork()``.  That round trip
    drops PrivateAttr ledgers and budget policy objects used by this project.
    Replacing the freshly forked agent before continuation preserves those
    branch-local runtime objects while the event prefix remains SDK-copied.
    """
    if agent is source.agent:
        raise ValueError('fork requires a distinct branch agent')
    fork = source.fork(agent=agent, reset_metrics=True)
    fork.agent = agent
    fork._state.agent = agent
    fork._agent_ready = False
    fork._bind_conversation_context(agent.llm)
    return fork


def branch_ledger_from_prefix(prefix_ledger: dict[str, Any]) -> dict[str, Any]:
    """Start an independent branch ledger that carries the prefix's spending.

    The branch ledger holds **only the branch's own requests**: the prefix's
    requests are already in the prefix ledger, and copying them into every
    branch would double-count them in the budget policy (once as ledger rows,
    once as the carried counters the policy adds) and would also inflate the
    estimated-input total.  What the branch really inherits is the prefix's
    *consumption*, recorded here as ``shared_prefix_*`` so the policy and the
    audit can continue the same 36-request / 2M-input ceiling.
    """
    if not isinstance(prefix_ledger.get('calls'), list):
        raise ValueError('prefix ledger calls missing')
    estimated = prefix_ledger.get('estimated_input')
    if not isinstance(estimated, int) or estimated < 0:
        raise ValueError('prefix estimated input missing')
    if any(not isinstance(call, dict) or call.get('kind') not in {'agent', 'summary'}
           for call in prefix_ledger['calls']):
        raise ValueError('prefix ledger has unknown request kind')
    if 'shared_prefix_call_count' in prefix_ledger:
        raise ValueError('prefix ledger is itself a branch ledger')
    return {
        'calls': [],
        'estimated_input': 0,
        'shared_prefix_call_count': len(prefix_ledger['calls']),
        'shared_prefix_estimated_input': estimated,
        'shared_prefix_agent_requests': sum(call['kind'] == 'agent'
                                            for call in prefix_ledger['calls']),
        'shared_prefix_summary_requests': sum(call['kind'] == 'summary'
                                              for call in prefix_ledger['calls']),
    }


def attach_branch_callback(fork: Any, callback: Any) -> None:
    """Observe branch events after the SDK's own persistence callback.

    The installed SDK's ``fork()`` currently omits caller callbacks.  This
    wrapper keeps the original event handler first so the SDK persists each
    event exactly once, then forwards it to the branch's budget/quality ledger.
    """
    if getattr(fork, '_v54_callback_attached', False):
        raise RuntimeError('branch callback already attached')
    original = fork._on_event

    def observed(event: Any) -> None:
        original(event)
        callback(event)

    fork._on_event = observed
    fork._v54_callback_attached = True
