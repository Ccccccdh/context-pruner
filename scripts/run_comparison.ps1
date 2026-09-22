param(
    [ValidateSet("main", "ablation", "all")]
    [string]$Suite = "all",
    [int]$Repeats = 3,
    [string]$Tasks = "tasks/stage2",
    [string]$Out = "runs/stage2",
    [string]$ExperimentId = "",
    [switch]$Mock
)

$ErrorActionPreference = "Stop"

if (-not $Mock -and -not ($env:DEEPSEEK_API_KEY -or $env:OPENAI_API_KEY)) {
    throw "Please set DEEPSEEK_API_KEY or OPENAI_API_KEY first, or add -Mock."
}

$arguments = @(
    "-B", "-m", "experiments.runners.run_suite",
    "--suite", $Suite,
    "--tasks", $Tasks,
    "--repeats", $Repeats,
    "--out", $Out
)
if ($ExperimentId) {
    $arguments += @("--experiment-id", $ExperimentId)
}
if ($Mock) {
    $arguments += "--mock"
}

python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Stage 2 experiment suite failed."
}
