"""Host-only reference implementations for the v32 prospective contracts."""
from pathlib import Path


def apply_reference(workspace, task):
    workspace=Path(workspace)
    if task=='click_path_resolution':
        path=workspace/'src/click/core.py'
        text=path.read_text(encoding='utf-8')
        start=text.index('class Group(Command):')
        pos=text.index('    def __init__(',start)
        method='''    def resolve_path(self, ctx: Context, names: t.Sequence[str]) -> tuple[Command, ...] | None:
        if isinstance(names, str) or not isinstance(names, (list, tuple)) or not names:
            raise ValueError("names must be a nonempty list or tuple")
        if any(not isinstance(name, str) or not name for name in names):
            raise ValueError("each name must be a nonempty string")
        current: Command = self
        current_ctx = ctx
        resolved: list[Command] = []
        for index, name in enumerate(names):
            if not isinstance(current, Group):
                return None
            with current_ctx.scope(cleanup=False):
                found = current.get_command(current_ctx, name)
            if found is None:
                return None
            resolved.append(found)
            if index < len(names) - 1:
                current_ctx = current_ctx.__class__(
                    found, info_name=name, parent=current_ctx, resilient_parsing=True
                )
                current = found
        return tuple(resolved)

'''
        path.write_text(text[:pos]+method+text[pos:],encoding='utf-8')
    elif task=='packaging_partition':
        path=workspace/'src/packaging/specifiers.py'
        text=path.read_text(encoding='utf-8')
        start=text.index('class SpecifierSet(BaseSpecifier):')
        pos=text.index('    def __init__(',start)
        method='''    def partition(self, candidates, *, prereleases=None):
        accepted, rejected = [], []
        for candidate in candidates:
            if not isinstance(candidate, (str, Version)):
                raise TypeError("candidates must be strings or Version objects")
            (accepted if self.contains(candidate, prereleases=prereleases) else rejected).append(candidate)
        return accepted, rejected

'''
        path.write_text(text[:pos]+method+text[pos:],encoding='utf-8')
    elif task=='dotenv_value_origins':
        path=workspace/'src/dotenv/main.py'
        text=path.read_text(encoding='utf-8')
        method='''

def dotenv_values_with_lines(dotenv_path: StrPath, *, interpolate: bool = True,
                             encoding: str = "utf-8") -> Dict[str, Tuple[Optional[str], int]]:
    values = dotenv_values(dotenv_path, interpolate=interpolate, encoding=encoding)
    if not values:
        return {}
    lines: Dict[str, int] = {}
    with open(dotenv_path, encoding=encoding) as stream:
        for binding in parse_stream(stream):
            if not binding.error and binding.key is not None:
                lines[binding.key] = binding.original.line
    return {key: (value, lines[key]) for key, value in values.items()}
'''
        path.write_text(text+method,encoding='utf-8')
    else:
        raise KeyError(task)
