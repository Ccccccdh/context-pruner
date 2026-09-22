@echo off
setlocal
cd /d "%~dp0..\.."
.\.venv\Scripts\python.exe -B -m experiments.runners.run_autogen_team_experiment ^
  --mode api ^
  --confirm-send-synthetic-data ^
  --tasks tasks/stage5_autogen_team/external_validity_v108_tasks.json ^
  --protocol tasks/stage5_autogen_team/external_validity_v109_protocol.json ^
  --repeats 3 ^
  --model deepseek-flash ^
  --base-url https://api.deepseek.com ^
  --thinking-mode disabled ^
  --max-output-tokens 512 ^
  --max-visible-output-chars 180 ^
  --max-api-retries 2 ^
  --max-api-requests 84 ^
  --out runs/stage5-autogen-team-api ^
  --experiment-id deepseek-autogen-team-external-validity-6x3-v109 %*


