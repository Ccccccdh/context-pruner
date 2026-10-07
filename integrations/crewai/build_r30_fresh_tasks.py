"""Build two fresh 3/5-source tasks for r30 confirmation once."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tasks/stage5_autogen/natural_tasks_r30_fresh.json"


def source(name: str, call: dict, result: dict, keys: tuple[str, ...]) -> dict:
    serialized = json.dumps(result, ensure_ascii=False)
    parts = [json.dumps({key: result[key]}, ensure_ascii=False)[1:-1] for key in keys]
    assert all(part in serialized for part in parts)
    return {"name": name, "call": call, "result": result, "evidence_parts": parts}


TASKS = [
    {
        "task_id": "artifact_signing_publication",
        "title": "三源构件签名发布核验",
        "task": "构件集 bundle-r6 的发布批次 publish-34 需要核验。读取发布清单、签名报告和最终发布校验，三项来源都至少读取一次，顺序不限、可重复。只有所有签名有效且最终校验通过才可发布。",
        "history_constraint": "bundle-r6 与 publish-34 是当前批次；旧批次 publish-19 已撤销，不得当作当前对象。",
        "history_topic": "旧发布批次撤销记录、签名争议与过期窗口",
        "tools": [
            source("read_artifact_manifest", {"bundle": "bundle-r6"},
                   {"bundle": "bundle-r6", "batch": "publish-34", "artifact_count": 9, "manifest_sha": "art-82f1"},
                   ("bundle", "batch", "artifact_count")),
            source("read_signature_report", {"batch": "publish-34"},
                   {"batch": "publish-34", "invalid_signatures": 0, "checked_signatures": 9, "previous_batch": "publish-19"},
                   ("invalid_signatures", "checked_signatures")),
            source("run_publication_check", {"bundle": "bundle-r6"},
                   {"bundle": "bundle-r6", "publication_ready": True, "blocking_checks": 0, "reviewer_count": 2},
                   ("publication_ready", "blocking_checks")),
        ],
        "decision": "AUTHORIZE", "handle_facts": ["bundle-r6", "publish-34", "publication_ready true"],
        "answer_facts": ["artifact_count 9", "invalid_signatures 0", "publication_ready true", "blocking_checks 0"],
        "forbidden_facts": ["publish-19"], "region_fact": "", "version_fact": "publish-34",
        "expected_terms": ["bundle-r6", "publish-34", "publication_ready true", "AUTHORIZE"],
    },
    {
        "task_id": "replica_rebuild_pause",
        "title": "五源副本重建审核",
        "task": "存储池 pool-v3 的重建申请 rebuild-81 需要审核。读取重建计划、副本健康、容量快照、变更记录和最终就绪校验，五项来源都至少读取一次，顺序不限、可重复。容量充足仍不能覆盖副本缺失和最终校验失败，应暂缓。",
        "history_constraint": "pool-v3 与 rebuild-81 是本次申请；旧申请 rebuild-63 已取消，不得作为当前重建。",
        "history_topic": "旧副本重建申请取消记录、容量误判与过期维护窗口",
        "tools": [
            source("read_rebuild_plan", {"pool": "pool-v3"},
                   {"pool": "pool-v3", "request": "rebuild-81", "target_replicas": 4, "plan_sha": "reb-6d17"},
                   ("pool", "request")),
            source("read_replica_status", {"pool": "pool-v3"},
                   {"pool": "pool-v3", "missing_replicas": 1, "checksum_state": "degraded"},
                   ("missing_replicas", "checksum_state")),
            source("read_capacity_snapshot", {"pool": "pool-v3"},
                   {"pool": "pool-v3", "free_capacity_gb": 200, "capacity_state": "sufficient"},
                   ("free_capacity_gb", "capacity_state")),
            source("read_rebuild_change_log", {"request": "rebuild-81"},
                   {"request": "rebuild-81", "record_state": "open", "previous_request": "rebuild-63", "change_step": 4},
                   ("record_state", "previous_request")),
            source("run_rebuild_check", {"pool": "pool-v3"},
                   {"pool": "pool-v3", "rebuild_ready": False, "blocking_checks": 1, "reviewer_count": 3},
                   ("rebuild_ready", "blocking_checks")),
        ],
        "decision": "PAUSE", "handle_facts": ["pool-v3", "rebuild-81", "rebuild_ready false"],
        "answer_facts": ["missing_replicas 1", "free_capacity_gb 200", "rebuild_ready false", "blocking_checks 1"],
        "forbidden_facts": ["rebuild-63"], "region_fact": "", "version_fact": "rebuild-81",
        "expected_terms": ["pool-v3", "rebuild-81", "rebuild_ready false", "PAUSE"],
    },
]


def main() -> None:
    if OUT.exists():
        raise SystemExit("r30 task set is write-once")
    OUT.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
