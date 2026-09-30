"""Held-out feature contracts, frozen before the first model request.

These are evaluator-authored feature tasks on public source, not upstream issues.
Acceptance files and reference implementations are never placed in agent workspaces.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
from task_suite import hashes

SOURCES = {
    'click': {'directory': 'click-8.2.1', 'commit': 'fd183b2ced1cb5857784fe7fb22f4982f671f098',
              'url': 'https://github.com/pallets/click/tree/8.2.1'},
    'packaging': {'directory': 'packaging-25.0', 'commit': 'f58537628042c7f29780b9d33f31597e7fc9d664',
                  'url': 'https://github.com/pypa/packaging/tree/25.0'},
}
COMMIT = {key: value['commit'] for key, value in SOURCES.items()}
TASKS = {
    'click_aliases': {
        'project': 'click',
        'allowed': ['src/click/core.py', 'src/click/decorators.py', 'src/click/shell_completion.py'],
        'regression': ['tests/test_commands.py', 'tests/test_shell_completion.py'],
        'problem': '''Implement command aliases in Click 8.2.1. Group.add_command(cmd, name=None, aliases=None) accepts an iterable of nonempty string aliases. Group.command(..., aliases=...) and Group.group(..., aliases=...) forward aliases to registration, not to Command constructors. Canonical names alone remain in Group.commands and list_commands and ordinary help. get_command and invocation resolve aliases to the same command, including nested groups and chain groups. Shell completion offers canonical names and aliases with the same short help, filtering by prefix and excluding hidden commands. An alias identical to its own canonical name is harmless, duplicate aliases collapse. Reject empty/non-string aliases with ValueError. Reject an alias colliding with any existing canonical name or alias, and reject a new canonical name colliding with an existing alias, with ValueError; the whole registration must be atomic on validation failure. Preserve existing replacement behavior for a canonical command: replacing it removes its old aliases. Iterators must be consumed once. Preserve existing Click behavior outside this extension. Only edit the listed source files; tests are run externally.''',
        'tests': r'''
import click
import pytest
from click.testing import CliRunner

def setup_group(chain=False):
    root = click.Group('root', chain=chain)
    cmd = click.Command('deploy', callback=lambda: click.echo('deployed'), help='Deploy application.')
    root.add_command(cmd, aliases=iter(['d', 'ship', 'd', 'deploy']))
    return root, cmd

@pytest.mark.parametrize('name', ['deploy', 'd', 'ship'])
def test_resolve_and_invoke(name):
    root, cmd = setup_group()
    assert root.get_command(click.Context(root), name) is cmd
    result = CliRunner().invoke(root, [name])
    assert result.exit_code == 0 and result.output == 'deployed\n'
    assert root.list_commands(click.Context(root)) == ['deploy']
    assert set(root.commands) == {'deploy'}

def test_help_and_completion():
    root, cmd = setup_group()
    root.add_command(click.Command('hidden', hidden=True), aliases=['secret'])
    help_text = CliRunner().invoke(root, ['--help']).output
    assert 'deploy' in help_text and 'ship' not in help_text and 'secret' not in help_text
    items = {item.value: item.help for item in root.shell_complete(click.Context(root), '')}
    assert {'deploy', 'd', 'ship'} <= set(items)
    assert 'hidden' not in items and 'secret' not in items
    assert items['d'] == items['ship'] == items['deploy']
    assert [i.value for i in root.shell_complete(click.Context(root), 'sh')] == ['ship']

def test_decorators_nested():
    @click.group()
    def root(): pass
    @root.group(aliases=['a'])
    def admin(): pass
    @admin.command(aliases=['s'])
    def status(): click.echo('ready')
    result = CliRunner().invoke(root, ['a', 's'])
    assert result.exit_code == 0 and result.output == 'ready\n'

def test_chain():
    root, _ = setup_group(chain=True)
    result = CliRunner().invoke(root, ['d', 'ship'])
    assert result.exit_code == 0 and result.output == 'deployed\ndeployed\n'

@pytest.mark.parametrize('aliases', [[''], [None], [42], ['okay', '']])
def test_invalid_atomic(aliases):
    root = click.Group('root')
    with pytest.raises(ValueError): root.add_command(click.Command('new'), aliases=aliases)
    assert root.commands == {}
    assert root.get_command(click.Context(root), 'okay') is None

@pytest.mark.parametrize('name,aliases', [('new', ['deploy']), ('new', ['d']), ('ship', []), ('new', ['ok', 'd'])])
def test_collision_atomic(name, aliases):
    root, cmd = setup_group()
    with pytest.raises(ValueError): root.add_command(click.Command(name), aliases=aliases)
    assert root.commands == {'deploy': cmd}
    assert root.get_command(click.Context(root), 'ok') is None
    assert root.get_command(click.Context(root), 'd') is cmd

def test_replacement_and_explicit_name():
    root, old = setup_group()
    new = click.Command('internal')
    root.add_command(new, name='deploy', aliases=['go'])
    ctx = click.Context(root)
    assert root.get_command(ctx, 'deploy') is new and root.get_command(ctx, 'go') is new
    assert root.get_command(ctx, 'd') is None and root.get_command(ctx, 'ship') is None
    root.add_command(click.Command('other'), aliases=None)
    assert root.list_commands(ctx) == ['deploy', 'other']
''',
    },
    'packaging_selection': {
        'project': 'packaging',
        'allowed': ['src/packaging/requirements.py', 'src/packaging/markers.py'],
        'regression': ['tests/test_requirements.py', 'tests/test_markers.py', 'tests/test_specifiers.py'],
        'problem': '''Implement two dependency-selection APIs in packaging 25.0. Requirement.matches(name, version, *, environment=None, extras=(), prereleases=None) returns a bool. It checks canonicalized project names, SpecifierSet.contains with the requested prerelease policy, and whether this requirement's marker applies. A direct URL requirement raises ValueError because name/version cannot establish URL identity. Invalid versions raise InvalidVersion (validate even if name does not match). Both str and Version versions work. Requirement.is_applicable(*, environment=None, extras=()) evaluates its marker, or returns True if absent. Empty extras evaluates extra=''; a nonempty iterable of extras evaluates the marker once per normalized extra and ORs results. Normalization follows packaging's existing canonical extra rules. The supplied environment is copied, never mutated; extras override an environment-provided extra key. Other missing environment keys continue to use Marker.evaluate defaults. Add module-level select_requirements(requirements, *, environment=None, extras=()) returning a list of applicable Requirement objects. Input is a one-shot iterable of requirement strings or Requirement objects, parsed strings become objects, supplied objects retain identity, order and duplicates are preserved. Consume extras once and share the materialized values across all requirements. Malformed requirements retain InvalidRequirement behavior. No I/O, dependency additions, or changes to existing parsing/equality behavior. Only edit listed source files; tests are external.''',
        'tests': r'''
import pytest
import packaging.requirements as mod
from packaging.requirements import Requirement, InvalidRequirement
from packaging.version import Version, InvalidVersion

@pytest.mark.parametrize('name,version,expected', [('My_Pkg','1.5',True), ('my.pkg',Version('1.5'),True), ('other','1.5',False), ('MY-pkg','2',False)])
def test_matches(name, version, expected):
    assert Requirement('my-pkg>=1,<2').matches(name, version) is expected

@pytest.mark.parametrize('policy,expected', [(None,False), (False,False), (True,True)])
def test_prerelease(policy, expected):
    assert Requirement('pkg>=1').matches('pkg', '2rc1', prereleases=policy) is expected

@pytest.mark.parametrize('name', ['pkg', 'other'])
def test_invalid_version(name):
    with pytest.raises(InvalidVersion): Requirement('pkg>=1').matches(name, 'not a version')

def test_direct_url():
    with pytest.raises(ValueError): Requirement('pkg @ https://example.org/pkg.whl').matches('pkg','1')

@pytest.mark.parametrize('extras,expected', [([],False), (['a'],True), (['b'],False), (['b','A'],True), (iter(['b','A']),True)])
def test_extra_or(extras, expected):
    req = Requirement('pkg; extra == "a"')
    env = {'extra':'a', 'python_version':'3.11'}
    before = dict(env)
    assert req.is_applicable(environment=env, extras=extras) is expected
    assert env == before
    assert req.matches('pkg','1',environment=env,extras=['a']) is True

def test_normalization_and_empty():
    assert Requirement('pkg; extra == "my-feature"').is_applicable(extras=['My_Feature']) is True
    assert Requirement('pkg; extra == ""').is_applicable() is True
    assert Requirement('pkg').is_applicable(extras=iter([])) is True

@pytest.mark.parametrize('py,expected', [('3.9',False), ('3.11',True)])
def test_environment(py, expected):
    req = Requirement('pkg>=1; python_version >= "3.10" and extra == "fast"')
    assert req.matches('pkg','1.4',environment={'python_version':py},extras=['fast']) is expected

def test_select_identity_order_and_generator():
    obj = Requirement('base')
    gated = Requirement('fast; extra == "speed"')
    source = iter([obj, 'skip; python_version < "3"', gated, obj, 'last'])
    chosen = mod.select_requirements(source, environment={'python_version':'3.11'}, extras=iter(['speed']))
    assert [r.name for r in chosen] == ['base','fast','base','last']
    assert chosen[0] is obj and chosen[1] is gated and chosen[2] is obj

def test_select_empty_and_invalid():
    assert mod.select_requirements(iter([])) == []
    with pytest.raises(InvalidRequirement): mod.select_requirements(['not ??? valid'])

def test_multiple_gated_items_share_extras():
    chosen = mod.select_requirements(['a; extra == "x"','b; extra == "x"'],extras=iter(['X']))
    assert [r.name for r in chosen] == ['a','b']
''',
    },
}

def prepare(upstream_root, workspace, task):
    spec = TASKS[task]
    upstream = upstream_root / SOURCES[spec['project']]['directory']
    workspace.mkdir(parents=True)
    for name in ('src', 'tests'):
        shutil.copytree(upstream / name, workspace / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for p in upstream.iterdir():
        if p.is_file() and (p.name.startswith('LICENSE') or p.name.startswith('README')):
            shutil.copy2(p, workspace / p.name)
    (workspace / 'TASK.md').write_text(spec['problem'] + '\nAllowed files: ' + ', '.join(spec['allowed']), encoding='utf-8')
    return hashes(workspace)

def evaluate(workspace, task, destination, regression_only=False):
    destination.mkdir(parents=True, exist_ok=True)
    test_path = destination / 'test_acceptance.py'
    test_path.write_text(TASKS[task]['tests'], encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
    env.update(PYTHONPATH=str(workspace / 'src'), PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    paths = ([] if regression_only else [str(test_path)]) + [str(workspace / p) for p in TASKS[task]['regression']]
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                             '--basetemp', str(destination / 'tmp'), *paths], cwd=workspace, env=env,
                            capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    (destination / 'pytest.txt').write_text(output, encoding='utf-8')
    return {'passed': result.returncode == 0, 'returncode': result.returncode,
            'summary': output.strip().splitlines()[-1] if output.strip() else 'no output'}
