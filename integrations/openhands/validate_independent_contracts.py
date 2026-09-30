"""Host-only solvability check. Never imported by the experiment runner."""
from pathlib import Path
import json
from independent_tasks import TASKS, prepare, evaluate

ROOT = Path(__file__).resolve().parents[2]

def main():
    results = {}
    for task in TASKS:
        out = ROOT / '.tooling/independent-contract-reference' / task
        ws = out / 'workspace'
        prepare(ROOT / '.tooling/upstream', ws, task)
        if task == 'click_aliases':
            p = ws / 'src/click/core.py'
            s = p.read_text(encoding='utf-8')
            a = s.index('    def add_command(self, cmd: Command, name: str | None = None) -> None:')
            b = s.index('    @t.overload', a)
            s = s[:a] + '''    def add_command(self, cmd: Command, name: str | None = None, aliases=None) -> None:
        name = name or cmd.name
        if name is None:
            raise TypeError("Command has no name.")
        proposed = list(aliases) if aliases is not None else []
        old = getattr(self, "_command_aliases", {})
        for alias in proposed:
            if not isinstance(alias, str) or not alias:
                raise ValueError("Invalid alias")
            if alias != name and (alias in self.commands or alias in old):
                raise ValueError("Alias collision")
        if name in old:
            raise ValueError("Canonical name collides with alias")
        _check_nested_chain(self, name, cmd, register=True)
        remaining = {alias: target for alias, target in old.items() if target != name}
        remaining.update({alias: name for alias in proposed if alias != name})
        self.commands[name] = cmd
        self._command_aliases = remaining

''' + s[b:]
            for typ in ('command', 'group'):
                start = s.index('        from .decorators import ' + typ, s.index('class Group('))
                end = s.index('        return decorator', start) + len('        return decorator')
                part = s[start:end]
                part = part.replace('        func: t.Callable', '        aliases = kwargs.pop("aliases", None)\n\n        func: t.Callable', 1)
                part = part.replace('self.add_command(cmd)', 'self.add_command(cmd, aliases=aliases)')
                s = s[:start] + part + s[end:]
            s = s.replace('        return self.commands.get(cmd_name)', '        return self.commands.get(getattr(self, "_command_aliases", {}).get(cmd_name, cmd_name))', 1)
            s = s.replace('    for name in multi.list_commands(ctx):', '    for name in sorted(set(multi.list_commands(ctx)) | set(getattr(multi, "_command_aliases", {}))):', 1)
            p.write_text(s, encoding='utf-8')
        else:
            p = ws / 'src/packaging/requirements.py'
            s = p.read_text(encoding='utf-8')
            s += '''
    def is_applicable(self, *, environment=None, extras=()):
        if self.marker is None:
            return True
        values = tuple(extras) or ("",)
        return any(self.marker.evaluate({**(environment or {}), "extra": canonicalize_name(extra)}) for extra in values)

    def matches(self, name, version, *, environment=None, extras=(), prereleases=None):
        from .version import Version
        if self.url:
            raise ValueError("URL identity requires more than name/version")
        parsed = version if isinstance(version, Version) else Version(version)
        return (canonicalize_name(name) == canonicalize_name(self.name)
                and self.specifier.contains(parsed, prereleases=prereleases)
                and self.is_applicable(environment=environment, extras=extras))

def select_requirements(requirements, *, environment=None, extras=()):
    values = tuple(extras)
    result = []
    for item in requirements:
        req = item if isinstance(item, Requirement) else Requirement(item)
        if req.is_applicable(environment=environment, extras=values):
            result.append(req)
    return result
'''
            p.write_text(s, encoding='utf-8')
        results[task] = evaluate(ws, task, out / 'evaluation')
        assert results[task]['passed'], results[task]
    target = ROOT / '.tooling/independent-contract-reference/results.json'
    target.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))

if __name__ == '__main__':
    main()
