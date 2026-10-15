"""Read-only post-hoc diagnosis. Standard library only; never invokes a model.

Prices reproduce the frozen experiment's estimates, not current billing rates.
Outputs must be outside the original formal run to preserve its evidence.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def aggregate(rows):
    result = {key: sum(row[key] for row in rows) for key in (
        "prompt_tokens", "completion_tokens", "cache_hit_tokens", "cache_miss_tokens",
        "hit_cost_usd", "miss_cost_usd", "output_cost_usd", "cost_offpeak_usd",
        "cost_peak_usd", "elapsed_seconds")}
    result["requests"] = len(rows)
    return result


def diagnose(run, out):
    run, out = run.resolve(), out.resolve()
    if out == run or run in out.parents:
        raise ValueError("diagnostic output must be outside frozen formal evidence")
    if out.exists():
        raise FileExistsError("use a new diagnostic output; do not overwrite evidence")
    input_files = sorted(path for path in run.rglob("*") if path.is_file())
    before = {str(path.relative_to(run)): sha(path) for path in input_files}
    summary, audit = load(run / "paid/summary.json"), load(run / "final-audit.json")
    events = [json.loads(line) for line in (run / "paid/http/requests.jsonl").read_text().splitlines()]
    dispatch = {event["index"]: event for event in events if event["event"] == "dispatch"}
    complete = [event for event in events if event["event"] == "complete"]
    assert len(dispatch) == len(complete) == len(summary["ledger"]) == 304
    assert len({event["index"] for event in complete}) == 304
    by_sample = defaultdict(list)
    for event in complete:
        by_sample[event["sample_id"].rsplit("/", 1)[0]].append(event)
    rows, samples, leases = [], [], []
    for sample in summary["samples"]:
        sid, arm = sample["sample_id"], sample["arm"]
        base = run / "paid" / sid
        tools, boundaries = load(base / "tools.json"), load(base / "model_boundaries.json")
        checkpoint = load(base / "checkpoint.json")
        calls = sorted(by_sample[sid], key=lambda event: event["index"])
        assert len(calls) == len(boundaries) == sample["http_requests"]
        stages = [tool for tool in tools if tool["payload"].get("kind") == "stage_complete"]
        research_summary = stages[0]["payload"]["summary"]
        versions = {tool["payload"]["entity"]: tool["payload"]["source_version"]
                    for tool in tools if tool["name"] == "read_document" and tool.get("revision") == "current"}
        order = next(tool["payload"] for tool in tools if tool["name"] == "work_order")
        labels = {entity: {record["code"]: record["label"] for record in load(base / f"workspace/docs/{entity}.json")["records"]}
                  for entity in versions}
        selected_labels = [labels[query["entity"]][query["code"]] for query in order["queries"]]
        sample_rows, previous_system = [], None
        for n, (event, boundary) in enumerate(zip(calls, boundaries)):
            assert event["status"] == "returned" and not event["usage_errors"]
            assert event["sample_id"] == boundary["request_scope"]
            assert event["sdk_input_sha256"] == boundary["input_sha256"]
            assert boundary["paired"] is True
            request_path, response_path = run / "paid/http" / event["request_file"], run / "paid/http" / event["response_file"]
            body, response = load(request_path), load(response_path)
            assert hashlib.sha256(canonical(body).encode()).hexdigest() == event["body_sha256"]
            assert response["usage"] == event["raw_usage"]
            assert dispatch[event["index"]]["body_sha256"] == event["body_sha256"]
            start = boundary["tool_count_before_model"]
            stop = boundaries[n + 1]["tool_count_before_model"] if n + 1 < len(boundaries) else len(tools)
            executed = tools[start:stop]
            raw_calls = response["choices"][0]["message"].get("tool_calls", [])
            assert [call["function"]["name"] for call in raw_calls] == [tool["name"] for tool in executed]
            past = tools[:start]
            if any(tool["name"] == "write_artifact" for tool in past):
                phase = "after_write"
            elif any(tool["payload"].get("kind") == "work_order" for tool in past):
                phase = "order_available"
            elif any(tool["payload"].get("kind") == "stage_complete" for tool in past):
                phase = "closed_before_order"
            else:
                phase = "research"
            usage = event["usage"]
            hit, miss, output = usage["prompt_cache_hit_tokens"], usage["prompt_cache_miss_tokens"], usage["completion_tokens"]
            assert hit + miss == usage["prompt_tokens"]
            assert usage["prompt_tokens"] + output == usage["total_tokens"]
            system = [message for message in body["messages"] if message["role"] == "system"]
            visible_text = canonical(boundary["visible"])
            message = response["choices"][0]["message"].get("content") or ""
            row = {
                "index": event["index"], "sample_id": sid, "task": sample["task"], "arm": arm,
                "repeat": sample["repeat"], "turn": n + 1, "phase": phase,
                "request_file": event["request_file"], "response_file": event["response_file"],
                "prompt_tokens": usage["prompt_tokens"], "completion_tokens": output,
                "cache_hit_tokens": hit, "cache_miss_tokens": miss,
                "hit_cost_usd": hit * .003 / 1e6, "miss_cost_usd": miss * .15 / 1e6,
                "output_cost_usd": output * .6 / 1e6,
                "cost_offpeak_usd": event["offpeak_usage_priced_usd"],
                "cost_peak_usd": event["peak_usage_priced_usd"],
                "elapsed_seconds": event["elapsed_seconds"],
                "system_changed_from_previous": previous_system is not None and system != previous_system,
                "visible_current_versions": {entity: version in visible_text for entity, version in versions.items()},
                "next_tools": [tool["name"] for tool in executed],
                "rereads_issued": sum(bool(tool.get("reread")) for tool in executed if tool["name"] == "read_document"),
                "retrievals_issued": sum(tool["name"] == "retrieve_evidence" for tool in executed),
                "visible_groups": boundary["group_statuses"],
                "response_content_mentions_provenance": any(word in message.lower() for word in ("source_version", "provenance", "hash", "sha")),
                "provenance_excerpt": message[:600] if any(word in message.lower() for word in ("source_version", "provenance", "hash", "sha")) else None,
            }
            assert math.isclose(row["hit_cost_usd"] + row["miss_cost_usd"] + row["output_cost_usd"], row["cost_offpeak_usd"], abs_tol=1e-12)
            assert math.isclose(2 * row["cost_offpeak_usd"], row["cost_peak_usd"], abs_tol=1e-12)
            previous_system = system
            rows.append(row)
            sample_rows.append(row)
        first_order = next(row for row in sample_rows if row["phase"] == "order_available")
        samples.append({
            "sample_id": sid, "arm": arm, "task": sample["task"], "repeat": sample["repeat"],
            "passed": sample["independent"]["passed"], "totals": aggregate(sample_rows),
            "summary_characters": len(research_summary),
            "summary_contains_selected_labels": sum(label in research_summary for label in selected_labels),
            "summary_contains_current_versions": {entity: version in research_summary for entity, version in versions.items()},
            "first_order_visible_versions": first_order["visible_current_versions"],
            "rereads": sample["natural_rereads"], "retrievals": sum(row["retrievals_issued"] for row in sample_rows),
            "recovery_sent": sample["recovery_original_delivered"],
        })
        for gid, group in checkpoint["groups"].items():
            spans = [boundary for boundary in boundaries if gid in boundary["group_statuses"]]
            active = [boundary["clock"] for boundary in spans if boundary["group_statuses"][gid] == "active"]
            archived = [boundary["clock"] for boundary in spans if boundary["group_statuses"][gid] == "archived"]
            leases.append({"sample_id": sid, "arm": arm, "group_id": gid,
                           "first_seen_clock": spans[0]["clock"] if spans else None,
                           "first_archived_clock": archived[0] if archived else None,
                           "active_boundary_count": len(active), "active_clocks": active,
                           "sources": group["sources"]})
    arms = {arm: aggregate([row for row in rows if row["arm"] == arm]) for arm in ("A0", "A1", "A2")}
    phases = {arm: {phase: aggregate([row for row in rows if row["arm"] == arm and row["phase"] == phase])
                    for phase in ("research", "closed_before_order", "order_available", "after_write")}
              for arm in arms}
    deltas = {}
    for arm in ("A1", "A2"):
        deltas[arm] = {key: arms[arm][key] - arms["A0"][key] for key in arms[arm]}
    totals = aggregate(rows)
    assert totals["requests"] == 304
    assert totals["prompt_tokens"] == audit["usage"]["prompt_tokens"]
    assert totals["completion_tokens"] == audit["usage"]["completion_tokens"]
    assert math.isclose(totals["cost_offpeak_usd"], audit["cost_offpeak_usd"], abs_tol=1e-12)
    for arm in arms:
        assert arms[arm]["prompt_tokens"] == audit["arms"][arm]["prompt_tokens"]
        assert arms[arm]["cache_miss_tokens"] == audit["arms"][arm]["cache_miss_tokens"]
        assert math.isclose(arms[arm]["cost_offpeak_usd"], audit["arms"][arm]["cost_offpeak_usd"], abs_tol=1e-12)
    pairs = []
    for sample in samples:
        if sample["arm"] != "A2":
            continue
        baseline = next(other for other in samples if other["arm"] == "A0" and other["task"] == sample["task"] and other["repeat"] == sample["repeat"])
        pairs.append({"task": sample["task"], "repeat": sample["repeat"],
                      "A2_minus_A0_cost_usd": sample["totals"]["cost_offpeak_usd"] - baseline["totals"]["cost_offpeak_usd"],
                      "A2_minus_A0_requests": sample["totals"]["requests"] - baseline["totals"]["requests"]})
    result = {"kind": "posthoc_offline_diagnosis_not_new_confirmation", "provider_calls_made": 0,
              "prices": {"basis": "frozen V5 estimates; not invoice", "offpeak_per_million": {"hit": .003, "miss": .15, "output": .6}, "peak_multiplier": 2},
              "source_run": str(run), "totals": totals, "arms": arms, "phases": phases,
              "deltas_vs_A0": deltas, "pairs_A2_A0": pairs,
              "tool_counts": {arm: dict(Counter(tool for row in rows if row["arm"] == arm for tool in row["next_tools"])) for arm in arms},
              "samples": samples, "source_group_visibility": leases}
    after_files = sorted(path for path in run.rglob("*") if path.is_file())
    after = {str(path.relative_to(run)): sha(path) for path in after_files}
    assert before == after, "formal evidence changed during diagnosis"
    result["formal_files_verified_unchanged"] = len(before)
    out.mkdir(parents=True)
    (out / "diagnosis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "requests.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (out / "input_hashes.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out), "requests_reconciled": len(rows), "samples": len(samples), "provider_calls_made": 0,
                      "formal_files_verified_unchanged": len(before), "arms": arms,
                      "tool_counts": result["tool_counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    diagnose(args.run, args.out)
