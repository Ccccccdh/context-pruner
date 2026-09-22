"""Minimal standard-library client for the Context-Pruner HTTP sidecar."""

from __future__ import annotations

import json
import os
import urllib.request


BASE_URL = os.getenv("CONTEXT_PRUNER_URL", "http://127.0.0.1:8765")
TOKEN = os.getenv("CONTEXT_PRUNER_AUTH_TOKEN", "")


def post(path: str, payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    messages = [
        {
            "role": "system",
            "content": "Hard constraint: preserve project Aurora-17.",
        }
    ]
    for index in range(12):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": f"old analysis {index} " + "background " * 28,
                },
                {
                    "role": "user",
                    "content": f"old note {index} " + "routine " * 24,
                },
            ]
        )
    messages.append(
        {
            "role": "user",
            "content": "Current task: decide using delay=96-hours and channel=priority.",
        }
    )
    result = post(
        "/v1/sessions/example-agent/before-model",
        {
            "messages": messages,
            "task_state": "Preserve Aurora-17 and current delivery facts",
            "reserved_tokens": 200,
            "budget": {"soft": 1200, "hard": 1800, "target": 900},
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
