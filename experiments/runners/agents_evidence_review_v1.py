"""Read-only historical audit and matched-input replay; no provider path."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def load_tooling(name):
    key = "agents_review_v1_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, ROOT / ".tooling" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def summarize_batch(payload):
    matrix = payload["preregistration"]["matrix"]
    expected = len(matrix["tasks"]) * len(matrix["arms"]) * matrix["repeats"]
    rows = payload["samples"]
    arms = {}
    for arm in matrix["arms"]:
        samples = [s for s in rows if s["arm"] == arm]
        filters = [t["history_filter"] for s in samples for t in s["transcript"]
                   if "history_filter" in t]
        usage = [u for s in samples for u in s["provider_usage"]]
        arms[arm] = {
            "samples": len(samples), "passed": sum(bool(s["accepted"]) for s in samples),
            "changed_samples": sum(any(t["history_filter"].get("before_digest") !=
                                        t["history_filter"].get("after_digest")
                                        for t in s["transcript"] if "history_filter" in t)
                                   for s in samples),
            "positive_saving_samples": sum(any((t.get("history_filter", {}).get("tokens_saved") or 0) > 0
                                                 for t in s["transcript"]) for s in samples),
            "local_before": sum(f.get("tokens_before") or 0 for f in filters),
            "local_after": sum(f.get("tokens_after") or 0 for f in filters),
            "local_saved": sum(f.get("tokens_saved") or 0 for f in filters),
            "requests": sum(s["model_calls"] for s in samples),
            "provider_prompt": sum(u.get("prompt_tokens") or 0 for u in usage),
            "provider_completion": sum(u.get("completion_tokens") or 0 for u in usage),
            "provider_total": sum(u.get("total_tokens") or 0 for u in usage),
            "provider_cache_hit": sum(u.get("prompt_cache_hit_tokens") or 0 for u in usage),
            "provider_cache_miss": sum(u.get("prompt_cache_miss_tokens") or 0 for u in usage),
        }
    return {"schema": "agents_evidence_review_v1", "provider_requests_this_review": 0,
            "matrix": {"derived_samples": expected, "declared_samples": matrix["samples"],
                       "declared_count_matches": matrix["samples"] == expected,
                       "actual_samples": len(rows), "actual_count_matches": len(rows) == expected},
            "arms": arms,
            "interpretation": "Descriptive arm outcomes; low intervention coverage; no causal or SDK lifecycle claim."}


def compare_checkpoint(messages):
    filt = load_tooling("agents_k_axis_filter")
    original = deepcopy(messages)
    n = filt.prompt_token_estimate(original)
    arms = {"none": {"messages": deepcopy(original), "tokens_after": n,
                     "digest": filt.message_digest(original), "fallback": False}}
    for k in (1, 0):
        transformed, decision = filt.build(k).filter_messages(deepcopy(original))
        arms[f"k{k}"] = {"messages": transformed, "tokens_after": decision["tokens_after"],
                           "digest": filt.message_digest(transformed), "decision": decision,
                           "fallback": False}
        fallback = decision["tokens_after"] >= n
        guarded = deepcopy(original if fallback else transformed)
        arms[f"k{k}_guarded"] = {"messages": guarded,
                                   "tokens_after": filt.prompt_token_estimate(guarded),
                                   "digest": filt.message_digest(guarded), "fallback": fallback}
    assert messages == original
    return {"input_digest": filt.message_digest(original), "local_tokens_before": n, "arms": arms}


def reconstruct_checkpoints(root, payload):
    """Replay delivered ranges, not requested ranges; prove reconstruction with recorded digest."""
    runner = load_tooling("agents_repair_runner")
    filt = load_tooling("agents_k_axis_filter")
    checkpoints = []
    for sample in payload["samples"]:
        if sample["arm"] == "none":
            continue  # no historical input digest in this arm
        task = runner.Task.load(sample["task"])
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=task.upstream_dir,
                              capture_output=True, text=True, check=True).stdout.strip()
        base_ok = head == task.base_commit
        messages = [{"role": "system", "content": runner.SYSTEM_PROMPT.format(max_reads=6, max_rounds=8)},
                    {"role": "user", "content": f"Bug report:\n{task.problem_statement}\n\n"
                     f"You are in a checkout of {task.instance_id} at {task.base_commit[:12]}. "
                     "Read what you need, then hand in the patch."}]
        for entry in sample["transcript"]:
            historical = entry.get("history_filter", {})
            digest = filt.message_digest(messages)
            matched = base_ok and digest == historical.get("before_digest")
            row = {"id": f"{sample['task']}-{sample['arm']}-r{sample['repeat']}-c{entry['round']}",
                   "task": sample["task"], "source_arm": sample["arm"], "repeat": sample["repeat"],
                   "round": entry["round"], "base_commit_matches": base_ok,
                   "recorded_digest": historical.get("before_digest"), "reconstructed_digest": digest,
                   "matched": matched, "historical_changed": historical.get("before_digest") != historical.get("after_digest")}
            if matched:
                row["comparison"] = compare_checkpoint(messages)
            else:
                row["rejection"] = "base commit or recorded input digest mismatch; not usable as exact checkpoint"
            checkpoints.append(row)
            if entry["parsed_kind"] != "read" or "read" not in entry:
                break
            delivered = entry["read"]
            if delivered.get("error"):
                result = {"error": delivered["error"], "path": delivered["path"]}
            else:
                result = runner.read_range(task.upstream_dir, delivered["path"],
                                           delivered["first_line"], delivered["last_line"])
            messages.extend([{"role": "assistant", "content": entry["reply"]},
                             {"role": "user", "content": json.dumps(result, ensure_ascii=False)}])
    return checkpoints


def write_review(out: Path):
    out.mkdir(parents=True, exist_ok=False)
    source = ROOT / "integrations/openai_agents/AGENTS_STEP5B_K_AXIS_BATCH_20261012.json"
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    summary = summarize_batch(payload)
    checkpoints = reconstruct_checkpoints(ROOT, payload)
    manifest = {}
    for row in checkpoints:
        comparison = row.pop("comparison", None)
        if comparison is None:
            continue
        path = out / (row["id"] + ".json")
        path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        row["comparison_file"] = path.name
        row["arms"] = {k: {f: v[f] for f in ("tokens_after", "digest", "fallback")}
                       for k, v in comparison["arms"].items()}
        manifest[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    summary["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    summary["checkpoint_counts"] = {"all": len(checkpoints), "matched": sum(r["matched"] for r in checkpoints),
                                   "historically_changed": sum(r["historical_changed"] for r in checkpoints),
                                   "matched_changed": sum(r["matched"] and r["historical_changed"] for r in checkpoints)}
    summary["sdk_path"] = "Direct httpx DeepSeek Chat Completions, custom repair loop; not SDK Runner."
    summary["model_causal_effect_measured"] = False
    for name, data in (("summary.json", summary), ("checkpoints.json", checkpoints), ("input_manifest.json", manifest)):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_review(args.out), ensure_ascii=False, indent=2))
