@echo off
setlocal
cd /d "%~dp0..\.."
set PYTHONUTF8=1
set "PROJECT_PYTHON=.venv\Scripts\python.exe"

if not exist "%PROJECT_PYTHON%" (
  echo Missing project Python: %PROJECT_PYTHON%
  exit /b 1
)

"%PROJECT_PYTHON%" -B -m experiments.runners.run_stage4a_experiment ^
  --mock ^
  --tasks tasks\stage4a\tasks.json ^
  --methods none,pruner_v1 ^
  --repeats 1 ^
  --max-turns 22 ^
  --context-soft-limit 700 ^
  --context-hard-limit 950 ^
  --context-target 550 ^
  --out runs\stage4a ^
  --experiment-id stage4a-mock-smoke-v052 %*

endlocal


