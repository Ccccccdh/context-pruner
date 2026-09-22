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
  --tasks tasks\stage4a\tasks.json ^
  --methods none,pruner_v1 ^
  --repeats 1 ^
  --model deepseek-v4-flash ^
  --base-url https://api.deepseek.com ^
  --temperature 0 ^
  --max-turns 30 ^
  --context-soft-limit 1600 ^
  --context-hard-limit 2100 ^
  --context-target 1250 ^
  --out runs\stage4a-real ^
  --experiment-id stage4a-real-smoke-final-v050 %*

endlocal


