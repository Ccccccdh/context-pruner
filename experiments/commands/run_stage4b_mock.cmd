@echo off
setlocal
cd /d "%~dp0..\.."
.venv\Scripts\python.exe -B -m experiments.runners.run_stage4b_experiment --mock --tasks tasks/stage4b/tasks.json --methods none,pruner_v1 --max-turns 30 --context-soft-limit 800 --context-hard-limit 1100 --context-target 700 --out runs/stage4b-mock --experiment-id stage4b-lifecycle-mock-final-v061


