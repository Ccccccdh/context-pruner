@echo off
setlocal
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set "PROJECT_PYTHON=.venv\Scripts\python.exe"

if not exist "%PROJECT_PYTHON%" (
  echo Missing project Python: %PROJECT_PYTHON%
  exit /b 1
)

"%PROJECT_PYTHON%" -B -m experiments.runners.run_langgraph_experiment ^
  --tasks tasks\stage2 ^
  --task-ids long_context_003,long_context_005,long_context_009,recovery_003,recovery_007,recovery_008,tool_chain_004,tool_chain_005,tool_chain_009 ^
  --methods none,pruner_v1 ^
  --repeats 3 ^
  --model deepseek-v4-flash ^
  --base-url https://api.deepseek.com ^
  --temperature 0 ^
  --max-turns 20 ^
  --context-soft-limit 800 ^
  --context-hard-limit 1000 ^
  --context-target 650 ^
  --out runs\stage3-langgraph-real ^
  --experiment-id langgraph-real-9x3-v046 %*

endlocal


