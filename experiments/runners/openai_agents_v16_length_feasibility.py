"""Zero-API feasibility replay for an all-arm shared output constraint/validator.

The question: on the recorded v12/v13/v14 answers, can "required facts + `cause=`/`fix=` fields
+ the full frozen regex + at most 160 characters" hold **at the same time**, and what does the
bounded repair path cost?

Method (recorded data only; nothing is written back to any batch, no request is made):

* every recorded sample is re-parsed into its issue/``cause=``/``fix=`` parts and each of the
  four conditions is checked mechanically, so the failure mode is visible per sample;
* for each frozen contract the **minimal deletion-only answer** is computed - the shortest
  string that still contains every required fact and every regex token in order, built solely
  from substrings of the recorded answer (no words are invented, none are reordered);
* that minimal answer is checked against all four conditions again, which answers "is length
  satisfiable at all" separately from "will a live model produce it";
* the repair path is priced: one extra provider call per over-length answer, with input tokens
  taken from that sample's own last-boundary input and output tokens from its own answer, and
  the whole thing is charged to the arm that produced the answer;
* the batch cost gate (paired complete-total provider tokens, **including repair calls**) is
  then recomputed with the observed repairs and with the worst case where every sample needs
  one.

This is a **new host configuration**; it is a projection on recorded answers and does not
re-judge, re-score or overwrite any frozen result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "runs/stage5-openai-agents-api"
OUT = REPO / "integrations/openai_agents/V16_LENGTH_FEASIBILITY_20261006.json"

MAX_CHARS = 160
V12 = "openai-repo-diagnostic-v12-exact-duplicate-dev-01"
V13 = "openai-repo-diagnostic-v13-requests1766-confirm-01"
V14 = "openai-repo-diagnostic-v14-multitask-confirm-01"

#: frozen contracts of the recorded batches (issue id, regex, required facts)
CONTRACTS = {
    V12: (
        "django-16263",
        r"RESULT issue=django-16263 cause=.*existing_annotations.*subquery.* fix=.*referenced.*",
        ("existing_annotations", "subquery", "referenced"),
    ),
    V13: (
        "requests-1766",
        r"RESULT issue=requests-1766 cause=.*qop.* fix=.*quot.*",
        ("qop", "build_quot_header", "quot"),
    ),
    V14: None,  # per task, read from the v14 registry
}


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def contract_for(batch: str, task: str) -> tuple[str, str, tuple[str, ...]]:
    if batch == V14:
        from experiments.runners import openai_agents_multitask_registry_v14 as registry

        entry = registry.task(task)
        match = re.search(r"issue=([^ ]+)", str(entry["contract"]))
        return (
            match.group(1) if match else "",
            entry["answer_pattern"],
            tuple(entry["required_terms"]),
        )
    issue, pattern, facts = CONTRACTS[batch]  # type: ignore[misc]
    if batch == V13:
        from experiments.runners import openai_agents_requests_task_registry_v13 as registry13

        return issue, pattern, tuple(registry13.REQUIRED_TERMS)
    return issue, pattern, facts


def conditions(answer: str, issue: str, pattern: str, facts: tuple[str, ...]) -> dict[str, bool]:
    cause = answer.split("cause=", 1)[1].split(" fix=", 1)[0] if "cause=" in answer else ""
    fix = answer.split(" fix=", 1)[1] if " fix=" in answer else ""
    return {
        "fields_present": bool(cause.strip()) and bool(fix.strip()),
        "facts_present": all(fact in answer for fact in facts),
        "full_regex": bool(re.fullmatch(pattern, answer, flags=re.IGNORECASE)),
        "within_160": len(answer) <= MAX_CHARS,
    }


def minimal_answer(answer: str, issue: str, pattern: str, facts: tuple[str, ...]) -> dict[str, Any]:
    """Shortest deletion-only answer that keeps the facts and the regex tokens in order."""
    tokens = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", pattern.split("cause=", 1)[1].split(" fix=", 1)[0]) if token not in {"cause", "rest", "s", "fix"}]
    kept_pieces: list[str] = []
    survivors: list[str] = []
    for fact in facts:
        if fact not in answer:
            return {"feasible": False, "reason": f"fact {fact!r} is absent from the recorded answer"}
        survivors.append(fact)
    # cause side: regex tokens that must appear before the fix boundary; fix side: the rest
    before_fix, _, after_fix = pattern.partition(" fix=")
    cause_tokens = [t for t in tokens if t in before_fix]
    fix_tokens = [t for t in tokens if t in after_fix]
    cause_bits = []
    for token in cause_tokens:
        if token not in cause_bits:
            cause_bits.append(token)
    fix_bits = []
    for token in fix_tokens:
        if token not in fix_bits:
            fix_bits.append(token)
    for fact in facts:
        if fact not in cause_bits and fact not in fix_bits:
            fix_bits.append(fact)
    cause_text = " ".join(cause_bits)
    fix_text = " ".join(fix_bits)
    minimal = f"RESULT issue={issue} cause={cause_text} fix={fix_text}"
    kept_pieces = cause_bits + fix_bits
    return {
        "feasible": True,
        "minimal_answer": minimal,
        "minimal_chars": len(minimal),
        "within_160": len(minimal) <= MAX_CHARS,
        "kept_tokens": kept_pieces,
        "dropped_chars": len(answer) - len(minimal),
        "kept_fraction": round(len(minimal) / len(answer), 4) if answer else 0.0,
        "conditions": conditions(minimal, issue, pattern, facts),
    }


def retry_cost(row: dict) -> dict[str, int]:
    """One extra call per over-length answer, using that sample's own recorded sizes."""
    by_call = list(row.get("actual_input_tokens_by_call") or [])
    outputs = list(row.get("actual_output_tokens_by_call") or [])
    resend_input = int(by_call[-1]) if by_call else int(row.get("actual_peak_input_tokens", 0) or 0)
    answer_output = int(outputs[-1]) if outputs else int(row.get("actual_output_tokens", 0) or 0)
    return {
        "extra_requests": 1,
        "extra_input_tokens": resend_input,
        "extra_output_tokens": answer_output,
        "extra_total_tokens": resend_input + answer_output,
    }


def batch_samples(batch: str) -> list[dict]:
    rows = read_jsonl(RUNS / batch / "samples.jsonl")
    out = []
    for row in rows:
        task = str(row.get("scenario"))
        issue, pattern, facts = contract_for(batch, task)
        answer = str(row.get("final_output", ""))
        out.append(
            {
                "batch": batch,
                "task": task,
                "repeat": int(row["repeat"]),
                "method": str(row["method"]),
                "answer_chars": len(answer),
                "answer": answer,
                "issue": issue,
                "pattern": pattern,
                "facts": list(facts),
                "conditions": conditions(answer, issue, pattern, facts),
                "complete_total_tokens": int(row.get("all_arm_total_tokens", 0) or 0),
                "row": row,
            }
        )
    return out


def paired_with_repairs(samples: list[dict]) -> dict[str, Any]:
    """Paired complete-total saving with the observed repairs and in the worst case."""
    keys = {(entry["task"], entry["repeat"]) for entry in samples}
    results: dict[str, Any] = {}
    for mode in ("observed", "worst_case"):
        pairs = []
        for task, repeat in sorted(keys):
            baseline = next(
                (e for e in samples if e["task"] == task and e["repeat"] == repeat and e["method"] == "none"),
                None,
            )
            plugin = next(
                (e for e in samples if e["task"] == task and e["repeat"] == repeat and e["method"] == "pruner_v1"),
                None,
            )
            if baseline is None or plugin is None:
                continue
            base_total = baseline["complete_total_tokens"]
            plugin_total = plugin["complete_total_tokens"]
            if mode == "observed":
                for entry in (baseline, plugin):
                    if not entry["conditions"]["within_160"]:
                        plugin_total += 0 if entry is baseline else retry_cost(entry["row"])["extra_total_tokens"]
                        base_total += retry_cost(entry["row"])["extra_total_tokens"] if entry is baseline else 0
            else:
                base_total += retry_cost(baseline["row"])["extra_total_tokens"]
                plugin_total += retry_cost(plugin["row"])["extra_total_tokens"]
            pairs.append(
                {
                    "task": task,
                    "repeat": repeat,
                    "baseline_total": base_total,
                    "plugin_total": plugin_total,
                    "saving": (base_total - plugin_total) / base_total if base_total else 0.0,
                }
            )
        mean = sum(pair["saving"] for pair in pairs) / len(pairs) if pairs else 0.0
        results[mode] = {
            "pairs": pairs,
            "paired_n": len(pairs),
            "mean_saving": mean,
            "positive_pairs": sum(1 for pair in pairs if pair["saving"] > 0),
        }
    return results


def build() -> dict[str, Any]:
    all_samples: list[dict] = []
    for batch in (V12, V13, V14):
        all_samples.extend(batch_samples(batch))

    over_length = [entry for entry in all_samples if not entry["conditions"]["within_160"]]
    only_length = [
        entry
        for entry in over_length
        if entry["conditions"]["facts_present"]
        and entry["conditions"]["full_regex"]
        and entry["conditions"]["fields_present"]
    ]

    feasibility = []
    for entry in all_samples:
        analysis = minimal_answer(entry["answer"], entry["issue"], entry["pattern"], tuple(entry["facts"]))
        feasibility.append(
            {
                "batch": entry["batch"],
                "task": entry["task"],
                "repeat": entry["repeat"],
                "method": entry["method"],
                "answer_chars": entry["answer_chars"],
                "conditions": entry["conditions"],
                "over_length": not entry["conditions"]["within_160"],
                "minimal": analysis,
            }
        )
    feasibility_by_key = {(entry["batch"], entry["task"], entry["repeat"], entry["method"]): entry for entry in feasibility}

    retry = []
    for entry in over_length:
        cost = retry_cost(entry["row"])
        retry.append(
            {
                "batch": entry["batch"],
                "task": entry["task"],
                "repeat": entry["repeat"],
                "method": entry["method"],
                "answer_chars": entry["answer_chars"],
                **cost,
            }
        )

    per_batch_cost = {}
    for batch in (V12, V13, V14):
        samples = [entry for entry in all_samples if entry["batch"] == batch]
        observed = [entry for entry in samples if not entry["conditions"]["within_160"]]
        worst_tokens = sum(retry_cost(entry["row"])["extra_total_tokens"] for entry in samples)
        per_batch_cost[batch] = {
            "samples": len(samples),
            "over_length_samples": len(observed),
            "observed_extra_requests": len(observed),
            "observed_extra_tokens": sum(retry_cost(entry["row"])["extra_total_tokens"] for entry in observed),
            "worst_case_extra_requests": len(samples),
            "worst_case_extra_tokens": worst_tokens,
            "worst_case_extra_tokens_share_of_batch": round(
                worst_tokens
                / sum(entry["complete_total_tokens"] for entry in samples)
                if sum(entry["complete_total_tokens"] for entry in samples)
                else 0.0,
                4,
            ),
            "paired_with_repairs": paired_with_repairs(samples),
        }

    v15_anchor = None
    admission = REPO / "integrations/openai_agents/V15_ACQUISITION_02_ADMISSION_20261006.json"
    if admission.is_file():
        record = json.loads(admission.read_text(encoding="utf-8"))
        for row in record.get("rows", []):
            for repair in row.get("repair_ledger", []):
                if repair.get("attempted"):
                    v15_anchor = {
                        "task": row["task"],
                        "requests": 1,
                        "input_tokens": int(repair["repair_usage"]["input_tokens"]),
                        "output_tokens": int(repair["repair_usage"]["output_tokens"]),
                        "same_answer_returned": repair["trigger_answer_sha256"]
                        == repair["repair_answer_sha256"],
                        "accepted_format": repair["accepted_format"],
                    }

    all_four_achievable = all(
        entry["minimal"].get("feasible") and entry["minimal"].get("conditions", {}).get("full_regex")
        and entry["minimal"].get("conditions", {}).get("facts_present")
        and entry["minimal"].get("conditions", {}).get("fields_present")
        and entry["minimal"].get("within_160")
        for entry in feasibility
    )
    return {
        "schema": "openai_agents_v16_length_feasibility",
        "date": "20261006",
        "paid_requests": 0,
        "host_configuration": (
            "new host configuration (all-arm shared output constraint/validator with a bounded "
            "repair path); nothing here is written back to v12-v14 and no recorded result is "
            "re-scored or re-judged"
        ),
        "recorded_samples": len(all_samples),
        "over_length_samples": len(over_length),
        "over_length_and_only_length": len(only_length),
        "feasibility": feasibility,
        "retry_estimates": retry,
        "per_batch_cost": per_batch_cost,
        "v15_recorded_repair_anchor": v15_anchor,
        "conclusion": {
            "all_four_conditions_achievable_by_deletion_only": all_four_achievable,
            "minimal_answer_chars": sorted(
                {entry["minimal"].get("minimal_chars") for entry in feasibility if entry["minimal"].get("feasible")}
            ),
            "model_can_produce_it": "unknown",
            "model_can_produce_it_note": (
                "a zero-API replay cannot show that a live model will answer inside 160 "
                "characters; it only shows that such an answer exists and what a repair costs"
            ),
            "host_rewrite_degrades_content": (
                "the deletion-only minimal answer keeps the required tokens and drops everything "
                "else; it is mechanically compliant and humanly degenerate, so a host rewrite is "
                "not recommended as an all-arm validator"
            ),
        },
        "limits": [
            "descriptive projection on recorded answers; no request, no re-scoring, no write-back",
            "repair input tokens assume a resend of that sample's own last-boundary payload",
            "the worst case assumes every sample in every arm needs one repair",
        ],
    }


def main() -> int:
    report = build()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(
        f"recorded samples {report['recorded_samples']} | over-length {report['over_length_samples']} "
        f"| over-length and only length {report['over_length_and_only_length']}"
    )
    print(
        f"all four conditions achievable by deletion-only: "
        f"{report['conclusion']['all_four_conditions_achievable_by_deletion_only']} "
        f"(minimal lengths {report['conclusion']['minimal_answer_chars']})"
    )
    for batch, entry in report["per_batch_cost"].items():
        observed = entry["paired_with_repairs"]["observed"]["mean_saving"]
        worst = entry["paired_with_repairs"]["worst_case"]["mean_saving"]
        print(
            f"  {batch}: samples {entry['samples']} over-length {entry['over_length_samples']} | "
            f"extra tokens observed {entry['observed_extra_tokens']} worst {entry['worst_case_extra_tokens']} "
            f"({entry['worst_case_extra_tokens_share_of_batch']:.1%} of batch) | paired saving observed "
            f"{observed:.4f} worst {worst:.4f}"
        )
    if report["v15_recorded_repair_anchor"]:
        anchor = report["v15_recorded_repair_anchor"]
        print(
            f"  v15 recorded repair anchor: 1 request, input {anchor['input_tokens']}, "
            f"output {anchor['output_tokens']}, same answer returned {anchor['same_answer_returned']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
