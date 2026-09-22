"""阶段四 B 工作区状态感知实验入口。"""

from __future__ import annotations

import sys
from pathlib import Path

from experiments.runners.run_stage4a_experiment import main as _run_workspace_experiment


def main(argv: list[str] | None = None) -> Path:
    arguments = list(sys.argv[1:] if argv is None else argv)
    return _run_workspace_experiment(["--phase", "stage4b", *arguments])


if __name__ == "__main__":
    main()
