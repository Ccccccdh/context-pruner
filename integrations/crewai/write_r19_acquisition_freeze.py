"""Write the prospective r19 acquisition freeze exactly once after zero-API gates."""

from __future__ import annotations

import json

from experiments.commands.run_crewai_r19_acquisition import (
    FREEZE, MAX_API_REQUESTS, PINNED, ROOT, TASK_IDS, sha,
)


def main() -> None:
    if FREEZE.exists():
        raise SystemExit(f"refusing to rewrite freeze: {FREEZE}")
    freeze = {
        "batch": "crewai-r19-fullcapture-acquisition-api-01",
        "purpose": "payload_acquisition_only",
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "tasks": list(TASK_IDS), "methods": ["none"], "repeats": 1,
        "model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com",
        "max_output_tokens": 512, "max_api_requests": MAX_API_REQUESTS,
        "request_arithmetic": {
            "expected": "3 tasks x (4 first-role tools + handoff + 1 decider tool + answer) = 21 agent requests",
            "hard_cap": "3 tasks x 2 roles x CrewAI max_iter 8 = 48; every attempted provider call consumes the global slot before send",
            "on_cap": "preserve partial rows; no continuation in the same ID",
        },
        "capture_required": [
            "request_slot", "full_input_messages", "raw_model_output",
            "normalized_model_output", "response_action_candidate",
            "host_parsed_action", "host_executed_tool", "guard_installed",
            "guard_allowance", "v18_offline_verdict", "input_tokens",
            "output_tokens", "status",
        ],
        "source_sha256": {relative: sha(ROOT / relative) for relative in PINNED},
        "freeze_discipline": "write once; reject source drift and batch ID mismatch before an API request",
        "note": "These three new tasks are acquisition/development data; any later confirmation needs separately chosen tasks after the mechanism is frozen.",
    }
    FREEZE.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(FREEZE, sha(FREEZE))


if __name__ == "__main__":
    main()
