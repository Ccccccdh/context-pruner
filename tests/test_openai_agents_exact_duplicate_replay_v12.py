from experiments.runners.openai_agents_exact_duplicate_replay_v12 import (
    deduplicate,
    replay_gate,
)


def test_recorded_real_payload_hashes_and_unique_source_survive():
    for repeat in range(3):
        gate = replay_gate(repeat)
        assert gate["recorded_fixture_ok"]
        assert gate["all_invariants_ok"]
        assert all(event["actual_output_hashes_match"] for event in gate["events"])
        assert gate["projected_byte_saving_rate"] > 0.03


def test_near_duplicate_is_not_elided():
    items = [
        {"type": "function_call", "call_id": "a"},
        {"type": "function_call_output", "call_id": "a", "output": "source\nline"},
        {"type": "function_call", "call_id": "b"},
        {"type": "function_call_output", "call_id": "b", "output": "source\nLine"},
    ]
    assert deduplicate(items) == (items, [])
