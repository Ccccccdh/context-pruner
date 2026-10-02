"""Resume a frozen runner with bounded retries for Windows atomic file replacement.

No model requests are retried. This compatibility patch is recorded separately
from the frozen manifest, including its hash and each filesystem retry.

v46 addition: the experiment process and every child it starts get TEMP/TMP/TMPDIR
inside the output directory. The v45 run showed the host sandbox refusing writes
to the machine %TEMP%, which surfaced as a file-editor PermissionError for the
Agent and as a scratch-directory cleanup failure in the host test runner. This
wrapper sets the variables before the runner (and therefore before the SDK) is
imported; the runner repeats the assignment defensively.
"""
import hashlib
import json
from pathlib import Path
import sys
import time


def main():
    import os
    root = Path(__file__).resolve().parents[2]
    env_args = (os.environ.get('DSH_RUNNER_ARGS') or '').split()
    argv = list(sys.argv) + env_args
    paid_run = '--run' in argv
    # The runner refuses an output directory that already holds anything other
    # than sdk-persistence/windows-compatibility.json, so this wrapper must NOT
    # pre-create the directory for a first-time --check. It creates scratch
    # space only after confirming the target is a fresh or resumed experiment.
    if '--out' in sys.argv:
        out = Path(sys.argv[sys.argv.index('--out') + 1]).resolve()
    elif '--out' in env_args:
        out = (Path.cwd() / env_args[env_args.index('--out') + 1]).resolve()
    else:
        raise SystemExit('--out is required (argument or DSH_RUNNER_ARGS).')
    if not out.is_relative_to(root):
        raise SystemExit(f'refusing to write outside the project root: {out}')
    # Never pre-create anything inside a directory that may be a first-time
    # --check: the frozen runner treats any entry other than sdk-persistence and
    # windows-compatibility.json as an existing experiment and refuses to start.
    # The runner creates <out>/.runtime-tmp itself before importing the SDK, so
    # pointing TEMP/TMP/TMPDIR at that path here is enough.
    runtime_temp = out / '.runtime-tmp'
    for name in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[name] = str(runtime_temp)
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['TIKTOKEN_CACHE_DIR'] = str(root / '.tooling/tiktoken')
    os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
    persistence = out / 'sdk-persistence'
    if paid_run:
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
    import run_validation_v48 as runner
    import openhands.sdk.io.local as local
    record_path = out / 'windows-compatibility.json'
    record = json.loads(record_path.read_text(encoding='utf-8')) if record_path.exists() else {
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_path': 'integrations/openhands/run_validation_windows_v48.py', 'scope': str(out), 'max_file_retries': 4, 'api_retries_added': 0, 'retries': [],
        'transport': {'deepseek_domain_direct': True, 'tls_verification': True,
                      'scope': 'experiment child only', 'other_proxy_routes_preserved': True},
        'temp_redirect': {'scope': 'experiment process and all children', 'path': str(runtime_temp),
                          'variables': ['TEMP', 'TMP', 'TMPDIR'],
                          'reason': 'machine %TEMP% writes were denied by the host sandbox in v45'}}
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
