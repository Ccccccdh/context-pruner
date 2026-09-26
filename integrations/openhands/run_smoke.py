"""Bounded OpenHands installation smoke, separate from pruning experiments."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--check", action="store_true", help="Local tools only, no model call")
    parser.add_argument("--out", default="runs/stage5-openhands/installation-smoke-v1")
    args = parser.parse_args()
    plan = {
        "experiment": "openhands-installation-smoke-v1",
        "model": "openai/deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "maximum_model_calls": 8,
        "max_output_tokens_per_call": 1024,
        "maximum_total_input_tokens": 100000,
        "maximum_single_input_tokens": 20000,
        "api_retries": 0,
        "thinking": "disabled",
        "data": "synthetic Python fixture only; no user documents",
        "plugin_enabled": False,
        "purpose": "host installation and autonomous file editing smoke",
    }
    if args.plan or not (args.run or args.check):
        print(json.dumps(plan, indent=2))
        return 0
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit("Output already exists; use a new --out directory.")
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key and not args.check:
        raise SystemExit("DEEPSEEK_API_KEY is unavailable (value is never printed).")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(Path(__file__).resolve().parents[2] / ".tooling" / "tiktoken")
    from pydantic import PrivateAttr, SecretStr
    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.tool import Tool, register_tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorAction, FileEditorObservation
    from openhands.tools.file_editor.impl import FileEditorExecutor
    from litellm import token_counter

    class BoundedLLM(LLM):
        _calls: int = PrivateAttr(default=0)
        _input_tokens: int = PrivateAttr(default=0)

        def completion(self, *call_args, **kwargs):
            messages = kwargs.get("messages", call_args[0] if call_args else [])
            serialized = [m.model_dump(mode="json") for m in messages]
            tool_data = [t.model_dump(mode="json") for t in kwargs.get("tools", []) or []]
            estimated = token_counter(model="gpt-4o", text=json.dumps(
                {"messages": serialized, "tools": tool_data})) + 256
            if (self._calls >= 8 or estimated > 20000
                    or self._input_tokens + estimated > 100000):
                raise RuntimeError("Smoke model call/token limit reached")
            self._calls += 1
            self._input_tokens += estimated
            print(f"model_call={self._calls}; estimated_input_tokens={estimated}", flush=True)
            return super().completion(*call_args, **kwargs)

    workspace = out / "workspace"
    workspace.mkdir(parents=True)
    original = "def parse_enabled(value):\n    return bool(value)\n"
    (workspace / "settings.py").write_text(original, encoding="utf-8")
    spec = (
        "parse_enabled accepts bool unchanged; strings true/1/yes/on and "
        "false/0/no/off (case insensitive, whitespace ignored). "
        "Other values raise ValueError. Only change settings.py.\n"
    )
    (workspace / "SPEC.md").write_text(spec, encoding="utf-8")

    class ScopedEditorExecutor(FileEditorExecutor):
        def __call__(self, action, conversation=None):
            path = Path(action.path).resolve()
            if not path.is_relative_to(workspace):
                return FileEditorObservation.from_text(
                    text="Access outside the smoke workspace is blocked.",
                    command=action.command, is_error=True)
            return super().__call__(action, conversation)

    class ScopedEditorTool(FileEditorTool):
        @classmethod
        def create(cls, conv_state):
            tools = super().create(conv_state)
            executor = ScopedEditorExecutor(workspace_root=str(workspace),
                                          allowed_edits_files=[str(workspace / "settings.py")])
            return [tool.set_executor(executor) for tool in tools]

    register_tool(ScopedEditorTool.name, ScopedEditorTool)
    if args.check:
        from openhands.sdk.llm.utils.telemetry import normalize_usage
        from litellm.types.utils import Usage
        usage = normalize_usage(Usage(prompt_tokens=10, completion_tokens=2,
                                      prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=10))
        assert usage.prompt_tokens == 10 and usage.completion_tokens == 2
        executor = ScopedEditorExecutor(workspace_root=str(workspace),
                                      allowed_edits_files=[str(workspace / "settings.py")])
        viewed = executor(FileEditorAction(command="view", path=str(workspace / "SPEC.md")))
        assert not viewed.is_error
        outside = executor(FileEditorAction(command="view", path=str(workspace.parent)))
        assert outside.is_error
        blocked = executor(FileEditorAction(command="str_replace", path=str(workspace / "SPEC.md"),
                                           old_str=spec, new_str="changed"))
        assert blocked.is_error
        edited = executor(FileEditorAction(command="str_replace", path=str(workspace / "settings.py"),
                                          old_str="return bool(value)", new_str="return False"))
        assert not edited.is_error
        assert "return False" in (workspace / "settings.py").read_text(encoding="utf-8")
        print(json.dumps({"local_tools_ok": True, "outside_read_blocked": True,
                          "other_file_edit_blocked": True, "usage_normalization_ok": True,
                          "model_calls": 0}))
        return 0
    plan["versions"] = {name: importlib.metadata.version(name)
                        for name in ("openhands-sdk", "openhands-tools", "litellm")}
    plan["runner_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    llm = BoundedLLM(model=plan["model"], api_key=SecretStr(key),
                     base_url=plan["base_url"], num_retries=0, timeout=60,
                     max_output_tokens=1024, temperature=0,
                     litellm_extra_body={"thinking": {"type": "disabled"}},
                     usage_id="installation-smoke")
    agent = Agent(llm=llm, tools=[Tool(name=ScopedEditorTool.name)])
    events: list[dict] = []

    def record(event):
        events.append({"type": type(event).__name__, "id": str(event.id)})

    started = time.perf_counter()
    conversation = None
    report = {"success": False, "plugin_enabled": False}
    try:
        conversation = Conversation(agent=agent, workspace=str(workspace),
                                    callbacks=[record], persistence_dir=str(out / "conversation"),
                                    max_iteration_per_run=8, visualizer=None, delete_on_close=False)
        conversation.send_message(
            f"Work only within {workspace}. Read SPEC.md and settings.py using the file editor. "
            "Fix parse_enabled to satisfy the spec. Preserve the function name. "
            "Only edit settings.py. Do not access files outside this workspace. "
            "Finish when the edit is complete; no terminal tool is available."
        )
        conversation.run()
        # The generated fixture is executed only by this local evaluator.
        repaired = (workspace / "settings.py").read_text(encoding="utf-8")
        assert repaired != original
        assert (workspace / "SPEC.md").read_text(encoding="utf-8") == spec
        assert {p.name for p in workspace.iterdir()} == {"SPEC.md", "settings.py"}
        tree = ast.parse(repaired)
        assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef)
        assert tree.body[0].name == "parse_enabled"
        assert not tree.body[0].decorator_list
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal))
            if isinstance(node, ast.Attribute):
                assert node.attr in ("strip", "lower", "casefold")
            if isinstance(node, ast.Call):
                assert ((isinstance(node.func, ast.Name) and node.func.id in
                         ("isinstance", "ValueError", "str", "bool"))
                        or isinstance(node.func, ast.Attribute))
        namespace = {}
        exec(compile(tree, str(workspace / "settings.py"), "exec"),
             {"__builtins__": {"isinstance": isinstance, "ValueError": ValueError,
                               "str": str, "bool": bool}}, namespace)
        parse = namespace["parse_enabled"]
        for value, expected in [(True, True), (False, False), (" TRUE ", True),
                                ("1", True), ("yes", True), ("on", True),
                                ("false", False), ("0", False), ("No", False), ("off", False)]:
            assert parse(value) is expected, repr(value)
        for value in ("invalid", None, 2):
            try:
                parse(value)
            except ValueError:
                continue
            raise AssertionError(f"Expected ValueError: {value!r}")
        report["success"] = True
        report["assertions_passed"] = 13
        report["only_allowed_file_changed"] = True
        report["repaired_sha256"] = hashlib.sha256(repaired.encode()).hexdigest()
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        # Provider errors may embed request details: never print/store the exception text.
    finally:
        report.update(model_calls=llm._calls, estimated_input_tokens=llm._input_tokens,
                      elapsed_seconds=time.perf_counter() - started, events=events)
        report["metrics"] = llm.metrics.model_dump(mode="json")
        if conversation is not None:
            conversation.close()
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("events", "metrics")}, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
