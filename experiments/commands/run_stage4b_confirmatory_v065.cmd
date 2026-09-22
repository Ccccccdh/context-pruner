@echo off
setlocal
cd /d "%~dp0..\.."
.venv\Scripts\python.exe -B -m experiments.runners.run_stage4b_experiment --tasks tasks/stage4b/tasks.json --methods none,pruner_v1 --repeats 3 --model deepseek-v4-flash --base-url https://api.deepseek.com --temperature 0 --max-output-tokens 700 --max-turns 30 --context-soft-limit 1800 --context-hard-limit 4000 --context-target 1500 --out runs/stage4b-confirmatory-real --experiment-id reserved-budget-3x3-v065


