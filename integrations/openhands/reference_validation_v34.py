"""Host-only reference implementation; never copied into an Agent workspace."""
from pathlib import Path


def apply_reference(workspace: Path):
    path = workspace / 'src/packaging/requirements.py'
    text = path.read_text(encoding='utf-8')
    text = text.replace('from .utils import canonicalize_name\n',
                        'from .utils import canonicalize_name\nfrom .version import Version\n', 1)
    marker = '    def _iter_parts(self, name: str) -> Iterator[str]:\n'
    assert text.count(marker) == 1
    method = '''    def assess_candidate(
        self, name: str, version: str | Version, *,
        environment: dict[str, str] | None = None, extra: str = "",
    ) -> dict[str, bool]:
        parsed_version = version if isinstance(version, Version) else Version(version)
        name_matches = canonicalize_name(self.name) == canonicalize_name(name)
        version_matches = self.specifier.contains(parsed_version) if self.specifier else True
        marker_environment = dict(environment or {})
        marker_environment["extra"] = extra
        marker_matches = self.marker.evaluate(marker_environment, context="metadata") if self.marker else True
        return {
            "name": name_matches,
            "version": version_matches,
            "marker": marker_matches,
            "accepted": name_matches and version_matches and marker_matches,
        }

'''
    path.write_text(text.replace(marker, method + marker, 1), encoding='utf-8')
