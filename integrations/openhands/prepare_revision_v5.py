"""Create v5 experiment files once; refuse to overwrite frozen source files."""
from pathlib import Path
p = Path(__file__).resolve().parent
runner_path = p / 'run_comparison_v5.py'
wrapper_path = p / 'run_windows_retry_v5.py'
if runner_path.exists() or wrapper_path.exists():
    raise SystemExit('Revision files already exist; do not overwrite frozen sources.')
s = (p / 'run_comparison_v4.py').read_text(encoding='utf-8')
s = s.replace('pruner_v4', 'pruner_v5').replace('three-arm-v9', 'three-arm-v10')
s = s.replace('openhands_v4', 'openhands_v5').replace('ContextPrunerCondenserV4', 'ContextPrunerCondenserV5')
s = s.replace("ROOT / 'context_pruner/adapters/openhands_v5.py'", "ROOT / 'context_pruner/adapters/openhands_v5.py', ROOT / 'context_pruner/adapters/openhands_v4.py'")
runner_path.write_text(s, encoding='utf-8', newline='\n')
w = (p / 'run_windows_retry.py').read_text(encoding='utf-8')
w = w.replace('import run_comparison_v4 as runner', '''import os
    root = Path(__file__).resolve().parents[2]
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['TIKTOKEN_CACHE_DIR'] = str(root / '.tooling/tiktoken')
    os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
    import run_comparison_v5 as runner''')
w = w.replace("'scope': str(out)", "'source_path': 'integrations/openhands/run_windows_retry_v5.py', 'scope': str(out)")
wrapper_path.write_text(w, encoding='utf-8', newline='\n')
