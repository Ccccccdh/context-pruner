@echo off
setlocal
cd /d "%~dp0..\.."
.venv\Scripts\python.exe -B -m experiments.runners.run_stage4b_experiment --tasks tasks/stage4b/tasks.json --methods none,pruner_v1 --repeats 1 --model deepseek-v4-flash --base-url https://api.deepseek.com --temperature 0 --max-output-tokens 700 --max-turns 30 --context-soft-limit 800 --context-hard-limit 1100 --context-target 700 --out runs/stage4b-real --experiment-id stage4b-lifecycle-smoke-final-v061


