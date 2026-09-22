@echo off
setlocal
cd /d "%~dp0..\.."
.\.venv\Scripts\python.exe -B -m experiments.runners.run_autogen_team_experiment ^
  --mode api ^
  --confirm-send-synthetic-data ^
  --protocol tasks/stage5_autogen_team/confirmatory_v107_protocol.json ^
  --repeats 5 ^
  --model deepseek-flash ^
  --base-url https://api.deepseek.com ^
  --thinking-mode disabled ^
  --max-output-tokens 512 ^
  --max-visible-output-chars 180 ^
  --max-api-retries 2 ^
  --max-api-requests 72 ^
  --out runs/stage5-autogen-team-api ^
  --experiment-id deepseek-autogen-team-confirmatory-3x5-v107 %*


