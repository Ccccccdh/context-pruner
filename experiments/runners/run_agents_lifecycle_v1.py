"""Real Agents SDK Runner + deterministic local Model; never contacts a provider.

Controlled mechanism/dependency fixture, not natural problem-solving evidence.
Each arm executes identical tools and schedule; only model-visible history varies.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path

from agents import Agent, Model, ModelResponse, function_tool
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from context_pruner.archive import InMemoryArchiveStore, SQLiteArchiveStore
from context_pruner.adapters.openai_agents_lifecycle_v1 import OpenAIAgentsEvidenceLifecycle, atomic_spans, encoded
from context_pruner.types import estimate_tokens


def route_from_model_input(items):
    """Only reads actual model input, no archive/store/file/answer access."""
    for item in reversed(items):
        if item.get("type") != "function_call_output":
            continue
        try:
            payload = json.loads(item["output"])
            if payload.get("kind") == "evidence" and payload.get("entity") == "route":
                return json.loads(payload["text"])["route"]
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    return ""


class LocalDependencyModel(Model):
    def __init__(self):
        self.inputs = []

    async def get_response(self, system_instructions, input, model_settings, tools,
                           output_schema, handoffs, tracing, **kwargs):
        if not isinstance(input, list):
            raise ValueError("test requires full client-managed input")
        self.inputs.append(deepcopy(input))
        step = len(self.inputs) - 1
        schedule = ["read_source", "close_stage", "unrelated_step", "check_dependency", "submit_receipt", "unrelated_step"]
        if step < len(schedule):
            name = schedule[step]
            args = {"route": route_from_model_input(input)} if name in {"check_dependency", "submit_receipt"} else {}
            output = [ResponseFunctionToolCall(id=f"fc-{step}", type="function_call", call_id=f"call-{step}",
                                               name=name, arguments=json.dumps(args), status="completed")]
        else:
            output = [ResponseOutputMessage(id="final", type="message", role="assistant", status="completed",
                content=[ResponseOutputText(type="output_text", text="Local lifecycle probe complete.", annotations=[], logprobs=[])])]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError("streaming explicitly unsupported")
        yield


def run_local_trial(arm, backend, out_dir):
    if arm not in {"A0", "A1", "A2"} or backend not in {"memory", "sqlite"}:
        raise ValueError("unregistered trial arm/backend")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    workspace = out / "workspace"
    (workspace / "docs").mkdir(parents=True)
    source = workspace / "docs/route.txt"
    # Identical workload across arms; this marker tests raw source removal, not answer success.
    source.write_text(json.dumps({"route": "NORTH7", "raw_marker": "ARCHIVE_ONLY_MARKER",
                                 "background": "field observation " * 160}), encoding="utf-8")
    version = hashlib.sha256(source.read_bytes()).hexdigest()
    namespace = f"agents-local-v1-{backend}-{arm}"
    store = SQLiteArchiveStore(out / "archive.sqlite", namespace=namespace) if backend == "sqlite" else InMemoryArchiveStore()
    adapter = OpenAIAgentsEvidenceLifecycle(namespace=namespace,
        trusted_tools={"read_source", "close_stage", "check_dependency"},
        current_versions={"docs/route.txt": version}, archive_store=store,
        lifecycle_enabled=arm != "A0", recovery_enabled=arm == "A2", ttl_model_calls=2)
    tool_log = []
    host = {"read": False}

    def record(name, payload):
        result = json.dumps(payload)
        tool_log.append({"name": name, "result": result})
        return result

    @function_tool
    def read_source() -> str:
        """Read the route source in the current workspace."""
        host["read"] = True
        return record("read_source", {"kind": "evidence", "source_path": "docs/route.txt",
            "source_version": version, "entity": "route", "stage": "research", "text": source.read_text(encoding="utf-8")})

    @function_tool
    def close_stage() -> str:
        """Finish the verified research stage after the source read."""
        if not host["read"]:
            return record("close_stage", {"kind": "error", "error": "research not completed"})
        return record("close_stage", {"kind": "stage_complete", "stage": "research"})

    @function_tool
    def unrelated_step() -> str:
        """Record an unrelated completed operation."""
        return record("unrelated_step", {"kind": "note", "text": "unrelated operation complete"})

    @function_tool
    def check_dependency(route: str) -> str:
        """Check the current proposed route; report missing evidence on an empty proposal."""
        if not route:
            return record("check_dependency", {"kind": "missing_evidence", "entity": "route"})
        correct = route == json.loads(source.read_text(encoding="utf-8"))["route"]
        return record("check_dependency", {"kind": "verified" if correct else "error", "accepted": correct})

    @function_tool
    def submit_receipt(route: str) -> str:
        """Write the proposed route to an artifact for independent evaluation."""
        (workspace / "receipt.json").write_text(json.dumps({"route": route}), encoding="utf-8")
        return record("submit_receipt", {"kind": "submitted"})

    model = LocalDependencyModel()
    agent = Agent(name="Agents lifecycle controlled dependency", model=model,
                  instructions="Read, finish research, perform the later dependency, then submit its receipt.",
                  tools=[read_source, close_stage, unrelated_step, check_dependency, submit_receipt])
    try:
        result = adapter.run_sync(agent, input="Follow the route from the source and submit a receipt.", max_turns=8)
        expected = json.loads(source.read_text(encoding="utf-8"))["route"]
        candidate = json.loads((workspace / "receipt.json").read_text(encoding="utf-8"))["route"]
        has_raw = [any("ARCHIVE_ONLY_MARKER" in encoded(i) for i in items) for items in model.inputs]
        paired = all(s["complete"] for items in model.inputs for s in atomic_spans(items) if s["calls"])
        report = {"experiment_id": "agents-lifecycle-v1-controlled-20261014", "arm": arm, "backend": backend,
            "sdk_version": importlib.metadata.version("openai-agents"), "sdk_runner_used": True,
            "model_kind": "deterministic_local_dependency_probe", "provider_requests": 0,
            "sdk_model_calls": len(model.inputs), "sdk_tool_calls": len(tool_log),
            "independent_dependency_passed": candidate == expected,
            "recovery_original_delivered": arm == "A2" and not has_raw[3] and has_raw[4],
            "ttl_original_removed": arm == "A2" and has_raw[5] and not has_raw[6],
            "raw_source_present_per_call": has_raw, "all_tool_groups_paired": paired,
            "metrics": adapter.metrics, "local_input_tokens": sum(estimate_tokens(encoded(i)) for i in model.inputs),
            "final_output": result.final_output,
            "limits": ["controlled schedule and trusted missing signal; not autonomous missing detection",
                       "not natural model quality/cost evidence", "no provider token usage or money claim"]}
        for name, payload in (("report.json", report), ("model_inputs.json", model.inputs),
                              ("tool_events.json", tool_log), ("lifecycle_events.json", adapter.events),
                              ("checkpoint.json", adapter.export_state())):
            (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception as exc:
        failure = {"arm": arm, "backend": backend, "provider_requests": 0,
                   "error_type": type(exc).__name__, "error": str(exc),
                   "sdk_model_calls_observed": len(model.inputs), "metrics": adapter.metrics}
        for name, payload in (("failure.json", failure), ("model_inputs.json", model.inputs),
                              ("tool_events.json", tool_log), ("lifecycle_events.json", adapter.events)):
            (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        if backend == "sqlite":
            store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    rows = [run_local_trial(arm, backend, args.out/f"{backend}-{arm}")
            for backend in ("memory", "sqlite") for arm in ("A0", "A1", "A2")]
    (args.out / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{k: r[k] for k in ("arm", "backend", "independent_dependency_passed", "recovery_original_delivered", "ttl_original_removed", "sdk_model_calls", "provider_requests")} for r in rows], indent=2))
