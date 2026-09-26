"""Frozen bug tasks on python-dotenv v1.2.1; no reference patch in agent input."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

COMMIT = "eaf2a9129ccec6febda0f741eb3bb852c3f947bd"
TASKS = {
    "quoted_roundtrip": {
        "source": "https://github.com/theskumar/python-dotenv/issues/661",
        "allowed": "src/dotenv/main.py",
        "problem": "set_key with quote_mode='always' or 'auto' must round-trip literal backslashes, including consecutive backslashes and apostrophes. Reading the written file with dotenv_values(interpolate=False) must reproduce the exact value. Preserve existing quote modes and export behavior.",
        "tests": r'''
import pytest
from dotenv import set_key, dotenv_values
@pytest.mark.parametrize('value', [r'one\\two', r'\\server\share', "x\\\\'y", 'plain', "it's fine"])
@pytest.mark.parametrize('mode', ['always', 'auto'])
@pytest.mark.parametrize('export', [False, True])
def test_roundtrip(tmp_path, value, mode, export):
    p = tmp_path / 'sample.env'
    p.write_text('OTHER=preserved\n', encoding='utf-8')
    result = set_key(p, 'VALUE', value, quote_mode=mode, export=export)
    assert result == (True, 'VALUE', value)
    values = dotenv_values(p, interpolate=False)
    assert values['VALUE'] == value
    assert values['OTHER'] == 'preserved'
''',
    },
    "empty_inline_comment": {
        "source": "https://github.com/theskumar/python-dotenv/pull/663",
        "allowed": "src/dotenv/parser.py",
        "problem": "An empty unquoted value followed by whitespace and an inline comment, e.g. KEY= # explanation, must parse as the empty string. Hash characters without preceding whitespace, e.g. KEY=#literal and KEY=a#b, remain literal. Preserve quoting, exports, and ordinary comments.",
        "tests": r'''
import io
import pytest
from dotenv import dotenv_values
@pytest.mark.parametrize('line', ['KEY= # note', 'KEY=  # note', 'KEY=\t# note', 'export KEY = # note'])
def test_empty_comment(line):
    assert dotenv_values(stream=io.StringIO(line + '\nNEXT=yes\n')) == {'KEY': '', 'NEXT': 'yes'}
@pytest.mark.parametrize('line,value', [('KEY=#literal', '#literal'), ('KEY=a#b', 'a#b'), ('KEY=abc # note', 'abc'), ('KEY=" # literal"', ' # literal'), ("KEY=' # literal'", ' # literal'), ('KEY=', '')])
def test_preserved(line, value):
    assert dotenv_values(stream=io.StringIO(line))['KEY'] == value
''',
    },
    "crlf_error_recovery": {
        "source": "https://github.com/theskumar/python-dotenv/pull/669",
        "allowed": "src/dotenv/parser.py",
        "problem": "Parser recovery after an invalid binding must consume a CRLF as one complete newline. For an invalid first line followed by GOOD=ok, the invalid binding's original.string must include the whole CRLF and the valid binding must start at line 2 with original.string exactly GOOD=ok plus its newline. Preserve LF and CR behavior and repeated invalid lines.",
        "tests": r'''
import io
import pytest
from dotenv.parser import parse_stream
@pytest.mark.parametrize('newline', ['\r\n', '\n', '\r'])
@pytest.mark.parametrize('count', [1, 2, 3])
def test_recovery(newline, count):
    invalid = 'BAD value'
    data = (invalid + newline) * count + 'GOOD=ok' + newline
    bindings = list(parse_stream(io.StringIO(data)))
    assert len(bindings) == count + 1
    for i, binding in enumerate(bindings[:-1]):
        assert binding.error
        assert binding.original.string == invalid + newline
        assert binding.original.line == i + 1
    valid = bindings[-1]
    assert not valid.error and valid.key == 'GOOD' and valid.value == 'ok'
    assert valid.original.string == 'GOOD=ok' + newline
    assert valid.original.line == count + 1
''',
    },
}

def hashes(workspace):
    return {p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in workspace.rglob('*') if p.is_file() and '__pycache__' not in p.parts
            and '.pytest_cache' not in p.parts}

def prepare(upstream, workspace, task):
    workspace.mkdir(parents=True)
    for name in ('src', 'tests'):
        shutil.copytree(upstream / name, workspace / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('README.md', 'LICENSE'):
        shutil.copy2(upstream / name, workspace / name)
    (workspace / 'TASK.md').write_text(TASKS[task]['problem'], encoding='utf-8')
    return hashes(workspace)

def evaluate(workspace, task, destination):
    destination.mkdir(parents=True, exist_ok=True)
    test_path = destination / 'test_acceptance.py'
    test_path.write_text(TASKS[task]['tests'], encoding='utf-8')
    # The evaluation process receives no provider credentials or project .env.
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
    env.update(PYTHONPATH=str(workspace / 'src'), PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    # A fresh explicit base avoids elevated/sandbox pytest temp ownership clashes.
    base_temp = destination / 'tmp'
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                             '--basetemp', str(base_temp),
                             str(test_path), str(workspace / 'tests/test_parser.py'),
                             str(workspace / 'tests/test_variables.py')], cwd=workspace, env=env,
                            capture_output=True, text=True, timeout=60)
    (destination / 'pytest.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
    return {'passed': result.returncode == 0, 'returncode': result.returncode,
            'summary': (result.stdout + result.stderr).strip().splitlines()[-1]}

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    result = {}
    for task in TASKS:
        out = root / '.tooling/openhands-task-baselines' / task
        if not out.exists():
            prepare(root / '.tooling/upstream/python-dotenv-v1.2.1', out / 'workspace', task)
        result[task] = evaluate(out / 'workspace', task, out / 'evaluation')
    print(json.dumps(result, indent=2))
