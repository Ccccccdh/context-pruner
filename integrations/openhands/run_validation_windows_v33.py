"""Resume a frozen runner with bounded retries for Windows atomic file replacement.

No model requests are retried. This compatibility patch is recorded separately
from the frozen manifest, including its hash and each filesystem retry.
"""
import hashlib
import json
from pathlib import Path
import sys
import time


def main():
    import os
    root = Path(__file__).resolve().parents[2]
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['TIKTOKEN_CACHE_DIR'] = str(root / '.tooling/tiktoken')
    os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
    out = Path(sys.argv[sys.argv.index('--out') + 1]).resolve()
    persistence = out / 'sdk-persistence'
    persistence.mkdir(parents=True, exist_ok=True)
    os.environ['OH_PERSISTENCE_DIR'] = str(persistence)
    from urllib.request import getproxies
    detected = getproxies()
    # Preserve other proxy routes in this child, including Windows registry
    # settings, and make only the authorized DeepSeek domain direct.
    for scheme in ('http', 'https', 'all'):
        name = scheme.upper() + '_PROXY'
        if detected.get(scheme) and not (os.environ.get(name) or os.environ.get(name.lower())):
            os.environ[name] = detected[scheme]
    entries = [x.strip() for x in (os.environ.get('NO_PROXY') or os.environ.get('no_proxy') or '').split(',') if x.strip()]
    if 'api.deepseek.com' not in entries:
        entries.append('api.deepseek.com')
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = ','.join(entries)
    import run_validation_v33 as runner
    import openhands.sdk.io.local as local
    record_path = out / 'windows-compatibility.json'
    record = json.loads(record_path.read_text(encoding='utf-8')) if record_path.exists() else {
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_path': 'integrations/openhands/run_validation_windows_v33.py', 'scope': str(out), 'max_file_retries': 4, 'api_retries_added': 0, 'retries': [],
        'transport': {'deepseek_domain_direct': True, 'tls_verification': True,
                      'scope': 'experiment child only', 'other_proxy_routes_preserved': True}}
    original = local.atomic_write_text

    def guarded_write(path, content):
        for attempt in range(5):
            try:
                return original(path, content)
            except PermissionError:
                if attempt == 4 or not Path(path).resolve().is_relative_to(out):
                    raise
                record['retries'].append({'path': str(Path(path).relative_to(out)), 'attempt': attempt + 1})
                time.sleep(0.05 * 2 ** attempt)

    local.atomic_write_text = guarded_write
    try:
        return runner.main()
    finally:
        record_path.write_text(json.dumps(record, indent=2), encoding='utf-8', newline='\n')


if __name__ == '__main__':
    raise SystemExit(main())
