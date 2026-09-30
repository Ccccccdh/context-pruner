"""Prospectively specified feature contracts across three public projects.

These are evaluator-authored tasks, not upstream bugs or independent blind tasks.
They were not used to tune v11 or the v31 navigation code.
"""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys

SOURCES = {
    'click': {'directory': 'click-8.2.1', 'commit': 'fd183b2ced1cb5857784fe7fb22f4982f671f098'},
    'packaging': {'directory': 'packaging-25.0', 'commit': 'f58537628042c7f29780b9d33f31597e7fc9d664'},
    'dotenv': {'directory': 'python-dotenv-v1.2.1', 'commit': 'eaf2a9129ccec6febda0f741eb3bb852c3f947bd'},
}
COMMIT = {name: item['commit'] for name, item in SOURCES.items()}

TASKS = {
    'click_path_resolution': {
        'project': 'click',
        'allowed': ['src/click/core.py'],
        'regression': ['tests/test_commands.py', 'tests/test_context.py'],
        'problem': '''Add Group.resolve_path(ctx, names) to Click 8.2.1. Given a nonempty tuple/list of registered command names, return a new tuple of Command objects along that path. Resolve each name through the current Group.get_command(ctx, name), not by direct dictionary access, so CommandCollection and overrides retain their semantics. For nested lookup construct a child context of the current context's concrete type with info_name=registered name, parent=current context and resilient_parsing=True; perform each lookup inside that context's scope(cleanup=False), with no callback invocation or parsing. Missing names and attempts to descend through a non-Group return None. A string, empty sequence, or any non-string/empty component raises ValueError before any lookup. Leave command registration, invocation, context stack, and callback resources unchanged. Only edit the allowed source file; separate tests assess this contract.''',
        'tests': r'''
import click
import pytest

class ScopedGroup(click.Group):
    def get_command(self, ctx, name):
        assert click.get_current_context() is ctx
        return super().get_command(ctx, name)

class CustomContext(click.Context):
    pass

def build():
    root = ScopedGroup('root')
    root.context_class = CustomContext
    child = ScopedGroup('internal')
    leaf = click.Command('internal-leaf',callback=lambda: (_ for _ in ()).throw(AssertionError('invoked')))
    child.add_command(leaf,name='registered')
    root.add_command(child,name='nested')
    return root,child,leaf

def test_nested_registered_names_and_context():
    root,child,leaf=build()
    ctx=CustomContext(root,info_name='root')
    before=click.get_current_context(silent=True)
    assert root.resolve_path(ctx,['nested','registered'])==(child,leaf)
    assert root.resolve_path(ctx,['nested'])==(child,)
    assert click.get_current_context(silent=True) is before

@pytest.mark.parametrize('names',[[], 'nested', [''], [3], ['nested',''], ['missing',3]])
def test_invalid_names_are_rejected_before_lookup(names):
    root,_,_=build()
    with pytest.raises(ValueError):root.resolve_path(CustomContext(root),names)

def test_missing_or_descending_past_leaf():
    root,_,_=build();ctx=CustomContext(root)
    assert root.resolve_path(ctx,['missing']) is None
    assert root.resolve_path(ctx,['nested','missing']) is None
    assert root.resolve_path(ctx,['nested','registered','later']) is None

def test_command_collection_lookup():
    one=click.Group('one');cmd=click.Command('actual');one.add_command(cmd,name='alias')
    collection=click.CommandCollection(sources=[one]);ctx=click.Context(collection)
    assert collection.resolve_path(ctx,['alias'])==(cmd,)
''',
    },
    'packaging_partition': {
        'project': 'packaging',
        'allowed': ['src/packaging/specifiers.py'],
        'regression': ['tests/test_specifiers.py'],
        'problem': '''Add SpecifierSet.partition(candidates, *, prereleases=None) to packaging 25.0. Consume an iterable exactly once and return two fresh lists (accepted, rejected), preserving original candidate objects, duplicate occurrences and order within each list. For each str or packaging.version.Version candidate, use the set's normal contains semantics with the supplied prereleases override; the default None follows the set's normal policy. Invalid version strings raise InvalidVersion as ordinary contains does, and unsupported types raise TypeError rather than silently coercing them. Do not mutate candidates, this SpecifierSet or prerelease policy. Do not modify existing filter/contains behavior. Only edit the allowed source file; separate tests assess this contract.''',
        'tests': r'''
import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version, InvalidVersion

def test_order_duplicates_and_identity():
    spec=SpecifierSet('>=1,<3')
    a=Version('1.1');b=Version('3.0');items=[a,'2.0',b,a,'0.9']
    accepted,rejected=spec.partition(iter(items))
    assert accepted==[a,'2.0',a] and rejected==[b,'0.9']
    assert accepted[0] is accepted[2] is a and rejected[0] is b
    accepted.append('extra')
    assert items==[a,'2.0',b,a,'0.9']

def test_prerelease_override_and_policy_unchanged():
    spec=SpecifierSet('>=1,<3')
    original=spec.prereleases
    yes,no=spec.partition(['2.0rc1','2.0'],prereleases=True)
    assert yes==['2.0rc1','2.0'] and no==[]
    yes,no=spec.partition(['2.0rc1','2.0'],prereleases=False)
    assert yes==['2.0'] and no==['2.0rc1']
    assert spec.prereleases==original

@pytest.mark.parametrize('bad',['not-a-version','1..2'])
def test_invalid_version(bad):
    with pytest.raises(InvalidVersion):SpecifierSet('>=1').partition([bad])

@pytest.mark.parametrize('bad',[None,1,object()])
def test_unsupported_type(bad):
    with pytest.raises(TypeError):SpecifierSet('>=1').partition([bad])

def test_empty_generator():
    assert SpecifierSet('>=1').partition(iter(()))==([],[])
''',
    },
    'dotenv_value_origins': {
        'project': 'dotenv',
        'allowed': ['src/dotenv/main.py'],
        'regression': ['tests/test_parser.py', 'tests/test_variables.py'],
        'problem': '''Add dotenv.main.dotenv_values_with_lines(dotenv_path, *, interpolate=True, encoding='utf-8') to python-dotenv 1.2.1. For a named .env file return an insertion-ordered mapping from each key to (value, starting physical line number of the final valid binding for that key). Values must exactly match dotenv_values(dotenv_path, interpolate=interpolate, encoding=encoding), including interpolation, None for valueless keys, duplicate-key overwrite behavior and multiline quoted values. Invalid bindings do not contribute. Do not set environment variables or change the file; missing file returns an empty mapping. Do not alter existing dotenv_values/load_dotenv behavior. Only edit the allowed source file; separate tests assess this contract.''',
        'tests': r'''
import os
import pytest
from dotenv.main import dotenv_values, dotenv_values_with_lines

def test_values_duplicates_and_lines(tmp_path,monkeypatch):
    path=tmp_path/'case.env'
    path.write_text('FIRST=1\n# note\nSECOND=${FIRST}\nFIRST=final\nNOVALUE\n',encoding='utf-8')
    monkeypatch.delenv('FIRST',raising=False)
    before=dict(os.environ)
    values=dotenv_values_with_lines(path)
    assert list(values)==['FIRST','SECOND','NOVALUE']
    assert {k:v for k,(v,_) in values.items()}==dotenv_values(path)
    assert [line for _,line in values.values()]==[4,3,5]
    assert dict(os.environ)==before

def test_no_interpolation_and_multiline(tmp_path):
    path=tmp_path/'multi.env'
    path.write_text('A="one\ntwo"\nB=${A}\n',encoding='utf-8')
    values=dotenv_values_with_lines(path,interpolate=False)
    assert values['A']==('one\ntwo',1)
    assert values['B']==('${A}',3)

def test_invalid_and_missing(tmp_path):
    path=tmp_path/'invalid.env'
    path.write_text('BAD value\nGOOD=ok\n',encoding='utf-8')
    assert dotenv_values_with_lines(path)=={'GOOD':('ok',2)}
    assert dotenv_values_with_lines(tmp_path/'absent.env')=={}

def test_encoding(tmp_path):
    path=tmp_path/'latin.env';path.write_bytes('NAME=café\n'.encode('latin-1'))
    assert dotenv_values_with_lines(path,encoding='latin-1')['NAME']==('café',1)
''',
    },
}


def hashes(workspace):
    return {p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in workspace.rglob('*') if p.is_file() and '__pycache__' not in p.parts
            and '.pytest_cache' not in p.parts}


def prepare(upstream_root, workspace, task):
    spec=TASKS[task]
    upstream=upstream_root/SOURCES[spec['project']]['directory']
    workspace.mkdir(parents=True)
    for name in ('src','tests'):
        shutil.copytree(upstream/name,workspace/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for path in upstream.iterdir():
        if path.is_file() and (path.name.startswith('LICENSE') or path.name.startswith('README')):
            shutil.copy2(path,workspace/path.name)
    (workspace/'TASK.md').write_text(spec['problem']+'\nAllowed files: '+', '.join(spec['allowed']),encoding='utf-8')
    return hashes(workspace)


def evaluate(workspace, task, destination, regression_only=False):
    destination.mkdir(parents=True,exist_ok=True)
    test_path=destination/'test_acceptance.py'
    test_path.write_text(TASKS[task]['tests'],encoding='utf-8')
    env={key:value for key,value in os.environ.items() if key.upper() in
         {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC','PATHEXT','SYSTEMDRIVE'}}
    env.update(PYTHONPATH=str(workspace/'src'),PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    paths=([] if regression_only else [str(test_path)])+[str(workspace/path) for path in TASKS[task]['regression']]
    result=subprocess.run([sys.executable,'-B','-m','pytest','-q','-p','no:cacheprovider',
                           '--basetemp',str(destination/'tmp'),*paths],cwd=workspace,env=env,
                          capture_output=True,text=True,timeout=120)
    output=result.stdout+result.stderr
    (destination/'pytest.txt').write_text(output,encoding='utf-8')
    last=output.strip().splitlines()[-1] if output.strip() else 'no pytest output'
    return {'passed':result.returncode==0,'returncode':result.returncode,'summary':last}
