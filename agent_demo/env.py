"""模拟工具环境：无状态、可复现，用于实验评测。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolExecution:
    """统一的工具执行结果，供知识库与真实工作区共同接入 Agent。"""

    output: str
    candidates: list[str] | None = None
    is_error: bool = False


@dataclass
class Task:
    """一个实验任务实例。docs 为模拟知识库文档；golden_facts 用于自动判分。"""

    task_id: str
    scenario: str
    question: str
    docs: dict[str, str] = field(default_factory=dict)  # doc_id -> content
    golden_facts: list[str] = field(default_factory=list)
    golden_answer: str = ""
    expected_tools: list[str] = field(default_factory=list)
    answer_constraints: dict[str, list[str]] = field(default_factory=dict)
    recovery_targets: list[str] = field(default_factory=list)
    fault_injection: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    workspace: str = ""
    workspace_expectations: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeBase:
    """模拟检索环境：只有 search 和 read 两个工具。"""

    docs: dict[str, str]

    def search(self, keyword: str = "") -> list[str]:
        kw = keyword.strip().lower()
        hits = [
            did
            for did, content in self.docs.items()
            if kw in did.lower() or kw in content.lower()
        ]
        # 小知识库兜底：没有精确命中时返回全部文档，避免 Agent 反复换关键词空转。
        if hits:
            return hits
        return list(self.docs)

    def read(self, doc_id: str) -> str:
        return self.docs.get(doc_id, f"错误：文档 {doc_id} 不存在")

    def tool_schema(self) -> str:
        return "search(keyword): 按关键词检索文档，返回文档 id 列表；read(doc_id): 读取文档内容"

    def tool_names(self) -> set[str]:
        return {"search", "read"}

    def resource_ids(self) -> list[str]:
        return list(self.docs)

    def execute(self, name: str, args: dict[str, Any]) -> ToolExecution:
        if name == "search":
            candidates = [str(item) for item in self.search(str(args.get("keyword", "")))]
            return ToolExecution(f"search 结果：{candidates}", candidates=candidates)
        if name == "read":
            result = self.read(str(args.get("doc_id", "")))
            return ToolExecution(f"read 结果：{result}", is_error=result.startswith("错误"))
        return ToolExecution(f"错误：未知工具 {name}", is_error=True)

    def metrics(self) -> dict[str, Any]:
        return {}


def load_tasks(path: str | Path) -> list[Task]:
    """从 tasks/<场景>/<任务>.json 加载任务。"""
    root = Path(path)
    if root.is_file():
        files = [root]
    else:
        files = sorted(root.glob("*.json"))
    tasks: list[Task] = []
    for f in files:
        payload = json.loads(f.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else [payload]
        for data in records:
            metadata = dict(data.get("metadata", {}))
            metadata.setdefault("_task_source_dir", str(f.parent.resolve()))
            tasks.append(
                Task(
                    task_id=data["task_id"],
                    scenario=data.get("scenario", f.parent.name),
                    question=data["question"],
                    docs=data.get("docs", {}),
                    golden_facts=data.get("golden_facts", []),
                    golden_answer=data.get("golden_answer", ""),
                    expected_tools=data.get("expected_tools", []),
                    answer_constraints=data.get("answer_constraints", {}),
                    recovery_targets=data.get("recovery_targets", []),
                    fault_injection=data.get("fault_injection", {}),
                    metadata=metadata,
                    workspace=data.get("workspace", ""),
                    workspace_expectations=data.get("workspace_expectations", {}),
                )
            )
    return tasks
