"""受控真实文件工作区，用于阶段四 A 的文件、检索与代码工具实验。"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .env import Task, ToolExecution
from .llm import LLMClient


_IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}


class WorkspaceEnvironment:
    """只允许访问复制后的任务工作区，并提供固定参数的代码验证工具。"""

    def __init__(self, root: str | Path, *, test_timeout: float = 30.0) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"工作区不存在：{self.root}")
        self.test_timeout = max(1.0, float(test_timeout))
        self.tool_counts: Counter[str] = Counter()
        self.bytes_read = 0
        self.bytes_written = 0
        self.last_test: dict[str, Any] | None = None
        self._mutation_epoch = 0
        self._file_versions: Counter[str] = Counter()
        self._confirmed_edits: dict[str, str] = {}
        self._mutated_paths: set[str] = set()
        self._post_mutation_read_count = 0
        self._test_run_count = 0
        self._initial_snapshot = self.snapshot()

    @classmethod
    def materialize(
        cls,
        task: Task,
        destination: str | Path,
        *,
        replace: bool = False,
    ) -> "WorkspaceEnvironment":
        source_dir = Path(str(task.metadata.get("_task_source_dir", ".")))
        template = (source_dir / task.workspace).resolve()
        destination = Path(destination).resolve()
        if not task.workspace or not template.is_dir():
            raise ValueError(f"任务 {task.task_id} 的工作区模板不存在：{template}")
        if destination.exists():
            if not replace:
                raise FileExistsError(f"工作区已存在：{destination}")
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return cls(destination)

    @property
    def docs(self) -> dict[str, str]:
        """兼容现有 Agent 的资源全集；内容按需从真实文件读取。"""
        return {path: "" for path in self.resource_ids()}

    def tool_schema(self) -> str:
        return (
            "list_files(pattern='**/*'): 列出工作区文件；"
            "search(keyword, path='.'): 在真实文件中检索文本；"
            "read(doc_id, start_line=1, end_line=200): 按行读取文件；"
            "replace_text(path, old, new, count=1): 精确替换文件文本，成功结果会返回当前版本的新文本，"
            "无需仅为确认写入而重新读取；"
            "run_tests(): 在隔离工作区运行固定 unittest 测试命令。"
            "多缺陷任务应先完成已知修改，再集中测试；测试失败后才根据失败证据继续修改并复测。"
            "禁止访问工作区之外的路径，禁止执行任意 shell 命令"
        )

    def tool_names(self) -> set[str]:
        return {"list_files", "search", "read", "replace_text", "run_tests"}

    def tool_ledger(self, actions: list[dict[str, Any]], candidates: list[str]) -> str:
        """生成不被历史压缩的最小工作区进度，防止重复读写和漏掉验证。"""
        if not actions:
            return ""
        reads = [
            str((item.get("args") or {}).get("doc_id") or (item.get("args") or {}).get("path") or "")
            for item in actions
            if item.get("name") == "read"
        ]
        edits = [
            str((item.get("args") or {}).get("path") or "")
            for item in actions
            if item.get("name") == "replace_text"
        ]
        test_state = self.test_state()
        changed = self.changed_files()
        unread = [item for item in candidates if item not in set(reads)]
        versions = {path: self._file_versions[path] for path in changed}
        confirmed = {
            path: self._file_versions[path]
            for path in list(self._confirmed_edits)[-10:]
        }
        return (
            "工作区工具状态："
            f"已读={reads[-20:]}; 候选未读={unread[:20]}; 已修改={changed or edits[-20:]}; "
            f"文件版本={versions}; 已确认修改版本={confirmed}; "
            f"工作区版本={self._mutation_epoch}; 最近测试={test_state}。"
            "已修改文件的当前状态优先于修改前 read 证据，"
            "replace_text 成功返回的新文本就是当前版本的可信证据，不要仅为确认写入而回读；"
            "禁止根据旧证据重复修改；禁止重复读取已覆盖区间。"
            "多文件修复应先完成所有已有证据支持的修改，再集中运行测试；"
            "仅在测试失败且需要新证据时复测。"
            "最近测试=PASS 时必须立即给出简洁 final_answer，不得再次读取、修改或测试。"
        )

    def redundant_action_reason(
        self,
        action: dict[str, Any],
        executed_actions: list[dict[str, Any]],
        candidates: list[str],
    ) -> str | None:
        """允许读取未覆盖区间与测试复跑，阻断被既有区间包含的重复读取。"""
        name = str(action.get("name", ""))
        if name == "run_tests":
            return "tests_already_current" if self.test_state() in {"PASS", "FAIL"} else None
        if name == "read":
            args = dict(action.get("args") or {})
            path = str(args.get("doc_id") or args.get("path") or "")
            start = max(1, int(args.get("start_line", 1)))
            end = max(start, int(args.get("end_line", 200)))
            latest_edit = max(
                (
                    index
                    for index, item in enumerate(executed_actions)
                    if item.get("name") == "replace_text"
                    and str((item.get("args") or {}).get("path") or "") == path
                ),
                default=-1,
            )
            for index, prior in enumerate(executed_actions):
                if str(prior.get("name", "")) != "read":
                    continue
                if index < latest_edit:
                    continue
                prior_args = dict(prior.get("args") or {})
                prior_path = str(
                    prior_args.get("doc_id") or prior_args.get("path") or ""
                )
                prior_start = max(1, int(prior_args.get("start_line", 1)))
                prior_end = max(prior_start, int(prior_args.get("end_line", 200)))
                if path == prior_path and prior_start <= start and prior_end >= end:
                    return "already_read_range"
            # A read after a mutation is intentionally allowed even when its raw
            # action JSON equals an older read.  Do not let the generic exact-
            # duplicate fallback below undo the file-version check above.
            return None
        if name == "replace_text":
            args = dict(action.get("args") or {})
            normalized = (
                str(args.get("path", "")),
                str(args.get("old", "")).strip(),
                str(args.get("new", "")).strip(),
            )
            for prior in executed_actions:
                if prior.get("name") != "replace_text":
                    continue
                prior_args = dict(prior.get("args") or {})
                prior_normalized = (
                    str(prior_args.get("path", "")),
                    str(prior_args.get("old", "")).strip(),
                    str(prior_args.get("new", "")).strip(),
                )
                if normalized == prior_normalized:
                    return "duplicate_workspace_edit"
        signature = json.dumps(action, ensure_ascii=False, sort_keys=True, default=str)
        if any(
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) == signature
            for item in executed_actions
        ):
            return "duplicate_workspace_action"
        return None

    def repeat_guard_observation(
        self,
        action: dict[str, Any],
        executed_actions: list[dict[str, Any]],
        reason: str,
        candidates: list[str],
    ) -> str:
        if reason == "workspace_complete":
            return (
                "保护：当前工作区版本已经通过测试，本次额外工具调用已跳过。"
                "现在必须立即输出简洁 final_answer，不得再次调用任何工具。"
            )
        return (
            "保护：该调用或其完整读取区间已经执行，本次已跳过。"
            "请服从工作区工具状态：读取尚未覆盖的区间；修改后运行一次测试；"
            "若最近测试为 PASS，立即 final_answer。"
        )

    def resource_ids(self) -> list[str]:
        return [
            path.relative_to(self.root).as_posix()
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and not any(part in _IGNORED_PARTS for part in path.parts)
        ]

    def search(self, keyword: str = "", path: str = ".") -> list[str]:
        query = str(keyword).strip().casefold()
        if not query:
            return []
        base = self._safe_path(path, require_file=False)
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        hits: list[str] = []
        for file in files:
            if not file.is_file() or any(part in _IGNORED_PARTS for part in file.parts):
                continue
            relative = file.relative_to(self.root).as_posix()
            try:
                text = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if query in relative.casefold() or query in text.casefold():
                hits.append(relative)
        return hits

    def read(self, doc_id: str, start_line: int = 1, end_line: int = 200) -> str:
        file = self._safe_path(doc_id, require_file=True)
        text = file.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), max(start, int(end_line)), start + 239)
        selected = lines[start - 1 : end]
        rendered = "\n".join(
            f"{line_number:04d}: {line}"
            for line_number, line in enumerate(selected, start=start)
        )
        if end < len(lines):
            rendered += f"\n...（剩余 {len(lines) - end} 行，可继续按行读取）"
        rendered = rendered[:12_000]
        self.bytes_read += len(rendered.encode("utf-8"))
        return rendered

    def execute(self, name: str, args: dict[str, Any]) -> ToolExecution:
        self.tool_counts[name] += 1
        try:
            if name == "list_files":
                pattern = str(args.get("pattern", "**/*"))
                files = [item for item in self.resource_ids() if fnmatch.fnmatch(item, pattern)]
                return ToolExecution(f"list_files 结果（{len(files)} 个）：{files}", candidates=files)
            if name == "search":
                keyword = str(args.get("keyword", ""))
                files = self.search(keyword, str(args.get("path", ".")))
                snippets = self._search_snippets(keyword, files)
                suffix = "" if len(files) <= 30 else f"；另有 {len(files) - 30} 个文件"
                return ToolExecution(
                    f"search 证据：命中 {len(files)} 个文件\n" + "\n".join(snippets) + suffix,
                    candidates=files,
                )
            if name == "read":
                path = str(args.get("doc_id") or args.get("path") or "")
                if path in self._mutated_paths:
                    self._post_mutation_read_count += 1
                content = self.read(
                    path,
                    int(args.get("start_line", 1)),
                    int(args.get("end_line", 200)),
                )
                # 生命周期内核用稳定前缀识别可信工具证据；来源路径保留在正文中。
                return ToolExecution(f"read 结果：文件={path}\n{content}")
            if name == "replace_text":
                return self._replace_text(args)
            if name == "run_tests":
                return self._run_tests()
            return ToolExecution(f"错误：未知工具 {name}", is_error=True)
        except (OSError, UnicodeError, ValueError) as error:
            return ToolExecution(f"错误：{error}", is_error=True)

    def validate(self, task: Task, final_answer: str) -> dict[str, Any]:
        expectations = dict(task.workspace_expectations or {})
        checks: list[dict[str, Any]] = []
        for relative, rules in dict(expectations.get("files") or {}).items():
            try:
                text = self._safe_path(relative, require_file=True).read_text(encoding="utf-8")
                error = ""
            except (OSError, UnicodeError, ValueError) as exc:
                text = ""
                error = str(exc)
            for needle in rules.get("contains", []):
                checks.append(
                    {
                        "check": "file_contains",
                        "path": relative,
                        "value": str(needle),
                        "passed": not error and str(needle) in text,
                    }
                )
            for needle in rules.get("absent", []):
                checks.append(
                    {
                        "check": "file_absent",
                        "path": relative,
                        "value": str(needle),
                        "passed": not error and str(needle) not in text,
                    }
                )
        if expectations.get("tests_pass"):
            checks.append(
                {
                    "check": "tests_pass",
                    "passed": self.test_state() == "PASS",
                    "returncode": None if self.last_test is None else self.last_test.get("returncode"),
                }
            )
        required_tools = [str(item) for item in expectations.get("required_tools", [])]
        for tool in required_tools:
            checks.append(
                {
                    "check": "tool_used",
                    "tool": tool,
                    "passed": self.tool_counts[tool] > 0,
                }
            )
        return {
            "success": all(item["passed"] for item in checks) if checks else True,
            "checks": checks,
            "changed_files": self.changed_files(),
        }

    def metrics(self) -> dict[str, Any]:
        return {
            "tool_counts": dict(self.tool_counts),
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
            "changed_file_count": len(self.changed_files()),
            "changed_files": self.changed_files(),
            "tests_passed": self.test_state() == "PASS",
            "test_returncode": None if self.last_test is None else self.last_test.get("returncode"),
            "workspace_mutation_epoch": self._mutation_epoch,
            "file_versions": dict(self._file_versions),
            "test_state": self.test_state(),
            "test_workspace_epoch": None
            if self.last_test is None
            else self.last_test.get("workspace_epoch"),
            "post_mutation_read_count": self._post_mutation_read_count,
            "test_run_count": self._test_run_count,
            "intermediate_test_count": max(0, self._test_run_count - 1),
        }

    def test_state(self) -> str:
        if self.last_test is None:
            return "NOT_RUN"
        if int(self.last_test.get("workspace_epoch", -1)) != self._mutation_epoch:
            return "STALE"
        return "PASS" if self.last_test.get("passed") else "FAIL"

    def is_complete(self) -> bool:
        """Return whether a current workspace version has passed validation tests."""
        return self._mutation_epoch > 0 and self.test_state() == "PASS"

    def snapshot(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for relative in self.resource_ids():
            data = (self.root / relative).read_bytes()
            result[relative] = hashlib.sha256(data).hexdigest()
        return result

    def changed_files(self) -> list[str]:
        current = self.snapshot()
        names = set(self._initial_snapshot) | set(current)
        return sorted(
            name for name in names if self._initial_snapshot.get(name) != current.get(name)
        )

    def audit(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "initial": self._initial_snapshot,
            "final": self.snapshot(),
            "metrics": self.metrics(),
            "last_test": self.last_test,
        }

    def _safe_path(self, value: str, *, require_file: bool) -> Path:
        raw = str(value).strip().replace("\\", "/")
        if not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
            raise ValueError(f"非法工作区路径：{value!r}")
        candidate = (self.root / raw).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"路径越界：{value!r}")
        if not candidate.exists():
            raise ValueError(f"路径不存在：{raw}")
        if require_file and not candidate.is_file():
            raise ValueError(f"不是文件：{raw}")
        return candidate

    def _search_snippets(self, keyword: str, files: list[str]) -> list[str]:
        query = keyword.casefold()
        snippets: list[str] = []
        for relative in files[:30]:
            try:
                lines = (self.root / relative).read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            match = next(
                ((index, line) for index, line in enumerate(lines, start=1) if query in line.casefold()),
                None,
            )
            if match:
                snippets.append(f"{relative}:{match[0]}: {match[1][:240]}")
            else:
                snippets.append(relative)
        payload = "\n".join(snippets)
        self.bytes_read += len(payload.encode("utf-8"))
        return snippets

    def _replace_text(self, args: dict[str, Any]) -> ToolExecution:
        relative = str(args.get("path", ""))
        old = str(args.get("old", ""))
        new = str(args.get("new", ""))
        count = max(1, min(20, int(args.get("count", 1))))
        if not old:
            return ToolExecution("错误：replace_text.old 不能为空", is_error=True)
        file = self._safe_path(relative, require_file=True)
        text = file.read_text(encoding="utf-8")
        occurrences = text.count(old)
        if occurrences == 0:
            return ToolExecution(f"错误：{relative} 中未找到待替换文本", is_error=True)
        updated = text.replace(old, new, count)
        file.write_text(updated, encoding="utf-8")
        written = len(updated.encode("utf-8"))
        self.bytes_written += written
        self._mutation_epoch += 1
        self._file_versions[relative] += 1
        self._mutated_paths.add(relative)
        self._confirmed_edits[relative] = new
        new_evidence = json.dumps(new[:400], ensure_ascii=False)
        return ToolExecution(
            "mutation 结果："
            f"文件={relative}; file_version={self._file_versions[relative]}; "
            f"workspace_epoch={self._mutation_epoch}; 替换={min(count, occurrences)}; "
            f"写入字节={written}\n"
            f"当前版本补丁证据：new={new_evidence}。"
            "该 new 文本已写入且可直接作为当前状态依据，无需为确认本次替换而重新读取文件。"
        )

    def _run_tests(self) -> ToolExecution:
        self._test_run_count += 1
        command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.test_timeout,
                check=False,
            )
            output = (completed.stdout + completed.stderr).strip()[-8_000:]
            passed = completed.returncode == 0
            self.last_test = {
                "passed": passed,
                "returncode": completed.returncode,
                "command": command[1:],
                "output": output,
                "workspace_epoch": self._mutation_epoch,
                "file_versions": dict(self._file_versions),
            }
            return ToolExecution(
                "verification 结果："
                f"{'PASS' if passed else 'FAIL'}; workspace_epoch={self._mutation_epoch}; "
                f"exit={completed.returncode}\n{output}",
                is_error=not passed,
            )
        except subprocess.TimeoutExpired as error:
            output = str(error)
            self.last_test = {
                "passed": False,
                "returncode": None,
                "command": command[1:],
                "output": output,
                "workspace_epoch": self._mutation_epoch,
                "file_versions": dict(self._file_versions),
            }
            return ToolExecution(
                "verification 结果："
                f"FAIL; workspace_epoch={self._mutation_epoch}; error=timeout\n{output}",
                is_error=True,
            )


class ScriptedWorkspaceLLM(LLMClient):
    """阶段四离线管线测试客户端；动作脚本来自任务，不用于效果结论。"""

    def __init__(self, task: Task) -> None:
        self.task = task
        self.calls = 0

    def reset(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        plan = list(self.task.metadata.get("mock_plan") or [])
        if self.calls < len(plan):
            action = dict(plan[self.calls])
            self.calls += 1
            return json.dumps(
                {"thought": f"执行受控工作区步骤 {self.calls}。", "action": action},
                ensure_ascii=False,
            )
        self.calls += 1
        answer = str(self.task.metadata.get("mock_final_answer") or self.task.golden_answer)
        return json.dumps(
            {"thought": "工作区检查和验证完成。", "final_answer": answer},
            ensure_ascii=False,
        )
