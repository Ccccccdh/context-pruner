@echo off
setlocal
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Missing .venv\Scripts\python.exe
  exit /b 1
)

if "%DEEPSEEK_API_KEY%%OPENAI_API_KEY%"=="" (
  echo [ERROR] Set DEEPSEEK_API_KEY or OPENAI_API_KEY in this terminal first.
  exit /b 1
)

".venv\Scripts\python.exe" -B -m experiments.runners.run_suite ^
  --suite main ^
  --methods none,pruner_v1 ^
  --baseline none ^
  --tasks tasks/stage2 ^
  --task-ids long_context_008,tool_chain_005,tool_chain_010 ^
  --repeats 5 ^
  --execution-order pair-interleaved ^
  --model deepseek-v4-flash ^
  --base-url https://api.deepseek.com ^
  --temperature 0 ^
  --max-turns 20 ^
  --max-output-tokens 512 ^
  --context-soft-limit 800 ^
  --context-hard-limit 1000 ^
  --context-target 650 ^
  --api-max-retries 5 ^
  --api-retry-base-delay 1 ^
  --api-retry-max-delay 20 ^
  --api-timeout 60 ^
  --archive-db runs/deepseek-real-v041/archive-evaluation-alignment.sqlite3 ^
  --no-fault-injection ^
  --out runs/deepseek-real-v041 ^
  --experiment-id evaluation-alignment-3x5-v041 ^
  %*

exit /b %errorlevel%


