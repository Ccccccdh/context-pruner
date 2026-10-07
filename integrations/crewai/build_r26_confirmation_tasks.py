"""Build the prospective r26 task set once; no provider output is consulted."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tasks/stage5_autogen/natural_tasks_r26_confirmation.json"


def source(name: str, call: dict, result: dict, keys: tuple[str, ...]) -> dict:
    serialized = json.dumps(result, ensure_ascii=False)
    parts = [json.dumps({key: result[key]}, ensure_ascii=False)[1:-1] for key in keys]
    assert all(part in serialized for part in parts)
    return {"name": name, "call": call, "result": result, "evidence_parts": parts}


TASKS = [
    {
        "task_id": "certificate_rotation_release",
        "title": "证书轮换发布核验",
        "task": "服务 gateway-c4 的证书轮换 rot-2026-14 申请发布。读取四份当前证据（轮换配置、链路探测、变更记录、发布前核验），判断发布或暂缓，保留当前标识和检查结果。四个来源各至少读取一次，顺序不限，可重复调用。",
        "history_constraint": "gateway-c4 与 rot-2026-14 是本次唯一有效轮换；旧轮换 rot-2026-09 已撤销，不得当作当前目标。",
        "history_topic": "旧证书轮换撤销记录、链路探测误报与过期发布窗口",
        "tools": [
            source("read_rotation_config", {"service": "gateway-c4"}, {"service": "gateway-c4", "rotation": "rot-2026-14", "key_size": 3072, "config_sha": "cert-83ac"}, ("service", "rotation")),
            source("read_chain_probe", {"service": "gateway-c4"}, {"service": "gateway-c4", "chain_valid": True, "failed_regions": 0, "probe_count": 6}, ("chain_valid", "failed_regions")),
            source("read_rotation_change_log", {"rotation": "rot-2026-14"}, {"rotation": "rot-2026-14", "record_state": "approved", "previous_rotation": "rot-2026-09", "change_step": 4}, ("record_state", "previous_rotation")),
            source("run_release_check", {"service": "gateway-c4"}, {"service": "gateway-c4", "release_ready": True, "blocking_checks": 0, "reviewer_count": 2}, ("release_ready", "blocking_checks")),
        ],
        "decision": "DEPLOY", "handle_facts": ["gateway-c4", "rot-2026-14", "release_ready true"],
        "answer_facts": ["release_ready true", "blocking_checks 0", "reviewer_count 2"],
        "forbidden_facts": ["rot-2026-09"], "region_fact": "", "version_fact": "rot-2026-14",
        "expected_terms": ["gateway-c4", "rot-2026-14", "release_ready true", "DEPLOY"],
    },
    {
        "task_id": "inventory_quarantine_hold",
        "title": "库存隔离解除审核",
        "task": "仓库 store-n8 的隔离批次 quarantine-37 申请解除。读取四份当前证据（批次定义、抽检结果、变更记录、解除前核验），判断解除或继续隔离，保留当前标识和检查结果。四个来源各至少读取一次，顺序不限，可重复调用。",
        "history_constraint": "store-n8 与 quarantine-37 是本次审核对象；历史批次 quarantine-21 已结案，不得当作当前对象。",
        "history_topic": "历史隔离批次结案记录、抽检差异争议与旧解除窗口",
        "tools": [
            source("read_quarantine_config", {"warehouse": "store-n8"}, {"warehouse": "store-n8", "batch": "quarantine-37", "item_count": 184, "policy_sha": "inv-522e"}, ("warehouse", "batch")),
            source("read_sample_results", {"batch": "quarantine-37"}, {"batch": "quarantine-37", "failed_samples": 2, "sample_count": 24, "lab_state": "final"}, ("failed_samples", "sample_count")),
            source("read_quarantine_change_log", {"batch": "quarantine-37"}, {"batch": "quarantine-37", "record_state": "open", "previous_batch": "quarantine-21", "change_step": 5}, ("record_state", "previous_batch")),
            source("run_release_safety_check", {"warehouse": "store-n8"}, {"warehouse": "store-n8", "release_ready": False, "blocking_alerts": 2, "reviewer_count": 3}, ("release_ready", "blocking_alerts")),
        ],
        "decision": "HOLD", "handle_facts": ["store-n8", "quarantine-37", "release_ready false"],
        "answer_facts": ["release_ready false", "blocking_alerts 2", "failed_samples 2"],
        "forbidden_facts": ["quarantine-21"], "region_fact": "", "version_fact": "quarantine-37",
        "expected_terms": ["store-n8", "quarantine-37", "release_ready false", "HOLD"],
    },
    {
        "task_id": "backup_restore_closeout",
        "title": "备份恢复结项审核",
        "task": "数据库 db-west-12 的恢复演练 restore-58 申请结项。读取四份当前证据（演练配置、恢复校验、变更记录、结项前核验），判断结项或继续修复，保留当前标识和检查结果。四个来源各至少读取一次，顺序不限，可重复调用。",
        "history_constraint": "db-west-12 与 restore-58 是本次演练对象；旧演练 restore-43 已作废，不得当作当前对象。",
        "history_topic": "旧恢复演练作废记录、校验争议与过期结项窗口",
        "tools": [
            source("read_restore_config", {"database": "db-west-12"}, {"database": "db-west-12", "run": "restore-58", "snapshot_sha": "bak-7e11", "replica_count": 3}, ("database", "run")),
            source("read_restore_validation", {"run": "restore-58"}, {"run": "restore-58", "checks_passed": 11, "checks_failed": 0, "checksum_state": "matched"}, ("checks_failed", "checksum_state")),
            source("read_restore_change_log", {"run": "restore-58"}, {"run": "restore-58", "record_state": "open", "previous_run": "restore-43", "change_step": 2}, ("record_state", "previous_run")),
            source("run_closeout_check", {"database": "db-west-12"}, {"database": "db-west-12", "closeout_ready": True, "open_findings": 0, "approver_count": 4}, ("closeout_ready", "open_findings")),
        ],
        "decision": "CLOSE", "handle_facts": ["db-west-12", "restore-58", "closeout_ready true"],
        "answer_facts": ["closeout_ready true", "open_findings 0", "checks_failed 0"],
        "forbidden_facts": ["restore-43"], "region_fact": "", "version_fact": "restore-58",
        "expected_terms": ["db-west-12", "restore-58", "closeout_ready true", "CLOSE"],
    },
]


def main() -> None:
    if OUT.exists():
        raise SystemExit("r26 task set is write-once")
    OUT.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
