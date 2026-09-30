"""Host-only positive controls; never supplied to the agent workspace."""
from pathlib import Path
import json
from validation_tasks_v29 import TASKS, prepare, evaluate, hashes

ROOT = Path(__file__).resolve().parents[2]

CATALOG = """
    def command_catalog(self, ctx, *, include_hidden=False, max_depth=None):
        if max_depth is not None and (isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0):
            raise ValueError('Invalid max_depth')
        if max_depth == 0:
            return []
        def snapshot(value):
            if isinstance(value, dict):
                return {key: snapshot(item) for key, item in value.items()}
            if isinstance(value, list):
                return [snapshot(item) for item in value]
            if isinstance(value, tuple):
                return tuple(snapshot(item) for item in value)
            return value
        result = []
        def walk(group, current, path, ancestors):
            with current.scope(cleanup=False):
                for name in group.list_commands(current):
                    cmd = group.get_command(current, name)
                    if cmd is None or (cmd.hidden and not include_hidden):
                        continue
                    child_path = path + (name,)
                    if isinstance(cmd, Group) and id(cmd) in ancestors:
                        raise ValueError('Command group cycle')
                    child = type(current)(cmd, info_name=name, parent=current, resilient_parsing=True)
                    with child.scope(cleanup=False):
                        result.append({'path': child_path, 'name': name,
                                       'short_help': cmd.get_short_help_str(), 'hidden': cmd.hidden,
                                       'deprecated': cmd.deprecated,
                                       'params': [snapshot(param.to_info_dict()) for param in cmd.get_params(child)]})
                        if isinstance(cmd, Group) and (max_depth is None or len(child_path) < max_depth):
                            walk(cmd, child, child_path, ancestors | {id(cmd)})
        walk(self, ctx, (), {id(self)})
        return result

"""

WHEELS = """

def select_compatible_wheels(filenames, supported_tags, *, project=None, specifier=None, prereleases=None, strict=True):
    from .specifiers import SpecifierSet
    if specifier is None:
        specifier = SpecifierSet('')
    elif isinstance(specifier, str):
        specifier = SpecifierSet(specifier)
    elif not isinstance(specifier, SpecifierSet):
        raise TypeError('specifier must be str or SpecifierSet')
    ranks = {}
    for item in supported_tags:
        if isinstance(item, Tag):
            expanded = [item]
        elif isinstance(item, str):
            expanded = sorted(parse_tag(item), key=str)
        else:
            raise TypeError('supported tag must be Tag or str')
        for tag in expanded:
            if tag not in ranks:
                ranks[tag] = len(ranks)
    wanted = canonicalize_name(project) if project is not None else None
    candidates = []
    for filename in filenames:
        try:
            name, version, build, tags = parse_wheel_filename(filename)
        except InvalidWheelFilename:
            if strict:
                raise
            continue
        matching = [ranks[tag] for tag in tags if tag in ranks]
        if matching and (wanted is None or name == wanted):
            candidates.append((filename, version, build, min(matching)))
    accepted = set(specifier.filter([row[1] for row in candidates], prereleases=prereleases))
    candidates = [row for row in candidates if row[1] in accepted]
    candidates.sort(key=lambda row: row[2], reverse=True)
    candidates.sort(key=lambda row: row[1], reverse=True)
    candidates.sort(key=lambda row: row[3])
    return [row[0] for row in candidates]
"""

def main():
    base = ROOT / '.tooling/validation-v29-reference'
    assert not base.exists(), 'Positive controls are preserved; use their saved results rather than overwrite'
    results = {}
    for task in TASKS:
        out = base / task
        ws = out / 'workspace'
        original = prepare(ROOT / '.tooling/upstream', ws, task)
        if task == 'click_catalog':
            source = ws / 'src/click/core.py'
            text = source.read_text(encoding='utf-8')
            at = text.index('    def to_info_dict(', text.index('class Group('))
            source.write_text(text[:at] + CATALOG + text[at:], encoding='utf-8')
        else:
            source = ws / 'src/packaging/utils.py'
            source.write_text(source.read_text(encoding='utf-8') + WHEELS, encoding='utf-8')
        result = evaluate(ws, task, out / 'evaluation')
        changed = {name for name in set(original) | set(hashes(ws)) if original.get(name) != hashes(ws).get(name)}
        result['boundary_ok'] = changed <= set(TASKS[task]['allowed'])
        results[task] = result
        print(task, result, flush=True)
        assert result['passed'] and result['boundary_ok'], result
    (base / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
