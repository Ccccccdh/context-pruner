"""New cross-module packaging contract for a long-context feasibility pilot."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from validation_tasks_v32 import SOURCES, COMMIT, hashes

TASKS = {
    'requirement_candidate_assessment': {
        'project': 'packaging',
        'allowed': ['src/packaging/requirements.py'],
        'research_files': ['src/packaging/requirements.py', 'src/packaging/markers.py',
                           'src/packaging/specifiers.py', 'src/packaging/utils.py',
                           'src/packaging/version.py'],
        'regression': ['tests/test_requirements.py', 'tests/test_markers.py', 'tests/test_specifiers.py'],
        'problem': '''Add Requirement.assess_candidate(name, version, *, environment=None, extra="") to packaging 25.0. This is a read-only diagnostic method returning a dictionary with exactly four boolean keys in order: name, version, marker, accepted. Name compares the candidate to this requirement using the package's existing canonical name semantics. Version checks this requirement's specifier policy, including prereleases; an empty specifier accepts any valid Version. Marker uses the package's normal metadata marker evaluation with the caller's environment overrides and the supplied extra (the extra argument takes precedence over an environment "extra" key). accepted is true only if all three dimensions are true. Compute all three dimensions independently even if an earlier one is false. Parse the candidate version and raise InvalidVersion for malformed versions, even when the name mismatches. Do not mutate the passed environment, this Requirement, or the candidate. Direct URL requirements have no specifier restriction. Existing Requirement parsing/stringification, marker and specifier behavior must remain unchanged. Investigate the existing Requirement, Marker, SpecifierSet, Version and canonical name implementations before editing. Only edit src/packaging/requirements.py; host tests assess the contract.''',
        'tests': r'''
import pytest
from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version


def test_name_version_marker_independent_and_no_mutation():
    req = Requirement('Demo_Pkg>=2,<4; python_version < "3.12" and extra == "speed"')
    env = {'python_version': '3.11', 'extra': 'wrong'}
    before = str(req)
    assert req.assess_candidate('demo-pkg', '2.5', environment=env, extra='speed') == {
        'name': True, 'version': True, 'marker': True, 'accepted': True}
    assert req.assess_candidate('another', '1.0', environment=env, extra='speed') == {
        'name': False, 'version': False, 'marker': True, 'accepted': False}
    assert env == {'python_version': '3.11', 'extra': 'wrong'} and str(req) == before


def test_prerelease_and_environment():
    req = Requirement('demo>=2; python_version >= "3.12"')
    assert req.assess_candidate('demo', Version('2.0rc1'), environment={'python_version': '3.11'}) == {
        'name': True, 'version': False, 'marker': False, 'accepted': False}
    assert req.assess_candidate('demo', '2.0', environment={'python_version': '3.12'})['accepted']


def test_empty_specifier_url_and_bad_version():
    req = Requirement('example @ https://example.org/example.whl')
    assert req.assess_candidate('Example', '0.0')['accepted']
    with pytest.raises(InvalidVersion):
        req.assess_candidate('other', 'not-a-version')


def test_extra_override_and_key_order():
    req = Requirement('demo; extra == "fast"')
    env = {'extra': 'slow'}
    result = req.assess_candidate('demo', '1', environment=env, extra='fast')
    assert list(result) == ['name', 'version', 'marker', 'accepted']
    assert result == {'name': True, 'version': True, 'marker': True, 'accepted': True}
    assert env == {'extra': 'slow'}
''',
    }
}


def prepare(upstream_root: Path, workspace: Path, task: str):
    spec = TASKS[task]
    upstream = upstream_root / SOURCES[spec['project']]['directory']
    workspace.mkdir(parents=True)
    for name in ('src', 'tests'):
        shutil.copytree(upstream / name, workspace / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for path in upstream.iterdir():
        if path.is_file() and (path.name.startswith('LICENSE') or path.name.startswith('README')):
            shutil.copy2(path, workspace / path.name)
    (workspace / 'TASK.md').write_text(spec['problem'] + '\nAllowed file: ' + spec['allowed'][0], encoding='utf-8')
    return hashes(workspace)


def evaluate(workspace: Path, task: str, destination: Path, regression_only: bool = False):
    destination.mkdir(parents=True, exist_ok=True)
    test_path = destination / 'test_acceptance.py'
    test_path.write_text(TASKS[task]['tests'], encoding='utf-8')
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
    env.update(PYTHONPATH=str(workspace / 'src'), PYTHONDONTWRITEBYTECODE='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    paths = ([] if regression_only else [str(test_path)]) + [
        str(workspace / path) for path in TASKS[task]['regression']]
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                             '--basetemp', str(destination / 'tmp'), *paths], cwd=workspace,
                            env=env, capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    (destination / 'pytest.txt').write_text(output, encoding='utf-8')
    last = output.strip().splitlines()[-1] if output.strip() else 'no pytest output'
    return {'passed': result.returncode == 0, 'returncode': result.returncode, 'summary': last}
