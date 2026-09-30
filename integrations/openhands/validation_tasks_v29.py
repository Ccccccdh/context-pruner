"""New feature contracts on previously seen projects; frozen before model requests.

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
    'click_catalog': {
        'project': 'click',
        'allowed': ['src/click/core.py', 'src/click/types.py', 'src/click/decorators.py'],
        'regression': ['tests/test_commands.py', 'tests/test_info_dict.py', 'tests/test_context.py'],
        'problem': """Add Group.command_catalog(ctx, *, include_hidden=False, max_depth=None) to Click 8.2.1 for documentation generators. Return a new list of flat metadata records, in depth-first pre-order using each group's list_commands(ctx) order; use get_command(ctx, registered_name), skip None. Do not include the root itself. Each record has exactly path (tuple of registered names from root), name (registered name, not Command.name), short_help (command.get_short_help_str()), hidden, deprecated, and params (the to_info_dict() results for command.get_params(child_context), including auto help). Construct child contexts using the current context's concrete type, info_name=registered name, parent=current context, and resilient_parsing=True; run discovery and metadata under that child's scope(cleanup=False) so get_current_context() works. Do not parse arguments, invoke callbacks, evaluate default callables, or execute/close registered resource callbacks. Hidden commands and whole hidden group subtrees are omitted unless include_hidden is True. max_depth=None is unlimited; zero returns []; positive integers include paths of length at most max_depth; negative integers, bools and non-integers raise ValueError before traversal. Detect a group already on the current ancestor path and raise ValueError containing 'cycle'; a shared group under two different branches is not a cycle and must be expanded twice. Do not descend beyond max_depth, so a cycle deeper than the requested bound is not inspected. All returned dict/list/tuple metadata containers are snapshots, including nested parameter type data and option spellings, so mutating a returned record must not mutate commands, params or later catalogs; opaque objects and callable defaults retain identity and must not be evaluated. Preserve Group and CommandCollection lookup semantics, registration order policy, existing info_dict behavior and ordinary invocation. No new dependencies, I/O or global context leaks. Only edit allowed source files; external tests verify the contract.""",
        'tests': r"""
import click
import pytest
from click.testing import CliRunner

class OnceGroup(click.Group):
    def list_commands(self, ctx):
        assert click.get_current_context() is ctx
        return super().list_commands(ctx)

class MetadataCommand(click.Command):
    def get_params(self, ctx):
        assert isinstance(ctx, CustomContext)
        assert ctx.info_name == 'registered'
        assert ctx.parent.command.name == 'root'
        assert ctx.resilient_parsing
        assert click.get_current_context() is ctx
        return super().get_params(ctx)

class CustomContext(click.Context):
    pass

def test_basic_metadata_and_invocation():
    root = click.Group('root')
    leaf = click.Command('internal', help='Leaf sentence. More text.', params=[click.Option(['--mode', '-m'], type=click.Choice(['fast','safe']))], callback=lambda mode: click.echo(mode))
    root.add_command(leaf, 'registered')
    ctx = click.Context(root)
    rows = root.command_catalog(ctx)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {'path','name','short_help','hidden','deprecated','params'}
    assert row['path'] == ('registered',) and row['name'] == 'registered'
    assert row['short_help'] == leaf.get_short_help_str()
    assert row['hidden'] is False and row['deprecated'] is False
    assert [p['name'] for p in row['params']] == ['mode','help']
    result = CliRunner().invoke(root, ['registered','--mode','fast'])
    assert result.exit_code == 0 and result.output == 'fast\n'

def tree():
    root = click.Group('root')
    shared = click.Group('shared')
    shared.add_command(click.Command('leaf'))
    root.add_command(shared, 'b')
    root.add_command(shared, 'a')
    hidden = click.Group('secret', hidden=True)
    hidden.add_command(click.Command('inside'))
    root.add_command(hidden)
    root.add_command(click.Command('z', hidden=True))
    return root

@pytest.mark.parametrize('hidden,expected', [
    (False,[('a',),('a','leaf'),('b',),('b','leaf')]),
    (True,[('a',),('a','leaf'),('b',),('b','leaf'),('secret',),('secret','inside'),('z',)])])
def test_preorder_shared_and_hidden(hidden, expected):
    root=tree()
    assert [r['path'] for r in root.command_catalog(click.Context(root),include_hidden=hidden)] == expected

@pytest.mark.parametrize('depth,expected', [(0,[]),(1,[('a',),('b',)]),(2,[('a',),('a','leaf'),('b',),('b','leaf')])])
def test_depth(depth,expected):
    root=tree()
    assert [r['path'] for r in root.command_catalog(click.Context(root),max_depth=depth)] == expected

@pytest.mark.parametrize('depth', [-1,True,False,1.0,'2',object()])
def test_invalid_depth(depth):
    root=click.Group('root')
    with pytest.raises(ValueError): root.command_catalog(click.Context(root),max_depth=depth)

def test_cycle_and_bounded_cycle():
    root=click.Group('root'); child=click.Group('child')
    root.add_command(child);child.add_command(root,'again')
    with pytest.raises(ValueError,match='cycle'): root.command_catalog(click.Context(root))
    assert [r['path'] for r in root.command_catalog(click.Context(root),max_depth=1)] == [('child',)]

def test_snapshot_and_callable_not_evaluated():
    calls=[]
    def default(): calls.append('default'); return 'fast'
    param=click.Option(['--mode','-m'],type=click.Choice(['fast','safe']),default=default)
    root=click.Group('root',callback=lambda: calls.append('root'))
    root.add_command(click.Command('leaf',params=[param],callback=lambda: calls.append('leaf')))
    ctx=click.Context(root)
    ctx.call_on_close(lambda: calls.append('close'))
    first=root.command_catalog(ctx)
    data=first[0]['params'][0]
    assert data['default'] is default
    data['opts'].append('--evil'); data['type']['choices'] += ('evil',)
    first[0]['path']=('bad',); first.append({})
    second=root.command_catalog(ctx)
    assert second[0]['path']==('leaf',)
    assert '--evil' not in param.opts and '--evil' not in second[0]['params'][0]['opts']
    assert 'evil' not in param.type.choices and 'evil' not in second[0]['params'][0]['type']['choices']
    assert calls == []
    assert click.get_current_context(silent=True) is None

def test_context_scope_and_registered_name():
    root=OnceGroup('root');root.add_command(MetadataCommand('internal'),'registered')
    ctx=CustomContext(root)
    with ctx.scope(cleanup=False):
        rows=root.command_catalog(ctx)
        assert click.get_current_context() is ctx
    assert rows[0]['name']=='registered'
    assert click.get_current_context(silent=True) is None

def test_command_collection_precedence_and_missing():
    first=click.Group('one');second=click.Group('two')
    first.add_command(click.Command('same',help='First.'))
    second.add_command(click.Command('same',help='Second.'));second.add_command(click.Command('other'))
    collection=click.CommandCollection(sources=[first,second])
    rows=collection.command_catalog(click.Context(collection))
    assert [r['path'] for r in rows]==[('other',),('same',)]
    assert rows[1]['short_help']=='First.'
    class Missing(click.Group):
        def list_commands(self,ctx): return ['gone']
    group=Missing('root')
    assert group.command_catalog(click.Context(group))==[]
""",
    },
    'packaging_wheel_ranking': {
        'project':'packaging',
        'allowed':['src/packaging/utils.py','src/packaging/tags.py','src/packaging/specifiers.py','src/packaging/version.py'],
        'regression':['tests/test_utils.py','tests/test_tags.py','tests/test_specifiers.py','tests/test_version.py'],
        'problem': """Add packaging.utils.select_compatible_wheels(filenames, supported_tags, *, project=None, specifier=None, prereleases=None, strict=True) returning a list of the original filename strings, for an offline installer candidate index. filenames and supported_tags are one-shot iterables; consume each once, preserve duplicate filenames. supported_tags accepts Tag objects or compressed tag strings accepted by parse_tag. Expand a string's set sorted by str(tag) into that position's sequence; first occurrence of each Tag wins and defines a zero-based preference rank. Unsupported item types raise TypeError even if filenames is empty; malformed tag strings propagate ValueError. Parse every filename using existing parse_wheel_filename. strict=True propagates InvalidWheelFilename even for a malformed candidate belonging to another project; strict=False skips InvalidWheelFilename only, not unrelated exceptions. project=None imposes no project filter; otherwise canonicalize project and parsed names using existing name rules. specifier=None means SpecifierSet(''); otherwise accept str or SpecifierSet, rejecting other types with TypeError even for empty candidates. Use SpecifierSet.filter(..., prereleases=prereleases) on the versions of candidates already matching project and at least one supported tag, honoring existing whole-pool prerelease fallback rather than per-item contains. Rank each compatible candidate by its best matching tag. Order by ascending tag rank, then descending Version, then descending parsed build tuple (nonempty builds above empty builds, numeric then suffix as existing parser returns), then original input order. Different projects may share the pool when project=None. Retain duplicate records, do not mutate SpecifierSet or supplied iterables, use no I/O or new dependencies, and preserve existing parsing and imports including specifiers importing utils (avoid circular imports). Only edit allowed source files; external tests verify the contract.""",
        'tests': r"""
import pytest
from packaging.tags import Tag
from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.utils import InvalidWheelFilename
from packaging import utils as utils_module

def select_compatible_wheels(*args, **kwargs):
    return utils_module.select_compatible_wheels(*args, **kwargs)

class Once:
    def __init__(self,items): self.items=items; self.used=False
    def __iter__(self):
        assert not self.used, 'iterated twice'
        self.used=True
        return iter(self.items)

def wheel(version,tag='py3-none-any',name='demo',build=None):
    return f'{name}-{version}-'+(f'{build}-' if build else '')+tag+'.whl'

def test_preference_before_version_and_duplicate_stability():
    a=wheel('9');b=wheel('1','cp313-cp313-win_amd64');c=wheel('2','cp313-cp313-win_amd64')
    source=Once([a,b,c,a])
    tags=Once([Tag('cp313','cp313','win_amd64'),'py3-none-any'])
    assert select_compatible_wheels(source,tags)==[c,b,a,a]
    assert source.used and tags.used

def test_build_order_and_version_precedence():
    items=[wheel('1',build='2a'),wheel('2'),wheel('1'),wheel('1',build='10'),wheel('1',build='2b')]
    assert select_compatible_wheels(items,['py3-none-any'])==[items[1],items[3],items[4],items[0],items[2]]

def test_compressed_tags_and_first_rank():
    a=wheel('1','py2-none-any');b=wheel('100','py3-none-any');c=wheel('2','py2.py3-none-any')
    assert select_compatible_wheels([a,b,c],Once(['py2.py3-none-any',Tag('py3','none','any')]))==[c,a,b]

def test_project_and_specifier_normalization():
    items=[wheel('1','py3-none-any','My_Pkg'),wheel('2','py3-none-any','my.pkg'),wheel('3','py3-none-any','other')]
    spec=SpecifierSet('>=1,<2');before=str(spec)
    assert select_compatible_wheels(items,['py3-none-any'],project='MY-pkg',specifier=spec)==[items[0]]
    assert str(spec)==before
    assert select_compatible_wheels(items,['py3-none-any'],project='my-pkg',specifier='>=2')==[items[1]]
    assert select_compatible_wheels(items,['py3-none-any'])==[items[2],items[1],items[0]]

@pytest.mark.parametrize('policy,expected', [(None,True),(True,True),(False,False)])
def test_empty_specifier_prerelease_fallback(policy,expected):
    item=wheel('2a1')
    assert select_compatible_wheels([item],['py3-none-any'],prereleases=policy)==([item] if expected else [])

def test_prerelease_pool_excludes_incompatible_and_other_project():
    pre=wheel('2a1');final=wheel('1');wrong=wheel('5','cp313-none-linux_x86_64');other=wheel('6',name='other')
    assert select_compatible_wheels([pre,wrong,other],['py3-none-any'],project='demo')==[pre]
    assert select_compatible_wheels([pre,final],['py3-none-any'])==[final]
    assert select_compatible_wheels([pre,final],['py3-none-any'],prereleases=True)==[pre,final]

@pytest.mark.parametrize('strict',[True,False])
def test_invalid_candidate_policy(strict):
    valid=wheel('1');bad='other-bad-file.whl'
    if strict:
        with pytest.raises(InvalidWheelFilename): select_compatible_wheels([valid,bad],['py3-none-any'],project='demo',strict=True)
    else:
        assert select_compatible_wheels([bad,valid,bad],['py3-none-any'],strict=False)==[valid]

def test_invalid_inputs_even_empty_pool():
    with pytest.raises(TypeError): select_compatible_wheels([],Once([42]))
    with pytest.raises(ValueError): select_compatible_wheels([],['bad'])
    with pytest.raises(TypeError): select_compatible_wheels([],[],specifier=42)
    with pytest.raises(InvalidSpecifier): select_compatible_wheels([],[],specifier='???')
    assert select_compatible_wheels([],[])==[]
    assert select_compatible_wheels([wheel('1')],[])==[]

def test_unrelated_type_errors_not_swallowed():
    with pytest.raises(AttributeError): select_compatible_wheels([None],['py3-none-any'],strict=False)

def test_explicit_prerelease_specifier_and_compatible_versions():
    items=[wheel('2a1'),wheel('2a2'),wheel('1')]
    assert select_compatible_wheels(items,['py3-none-any'],specifier='>=2a1')==[items[1],items[0]]
""",
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
