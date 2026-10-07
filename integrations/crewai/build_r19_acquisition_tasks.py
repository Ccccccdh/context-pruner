"""Write three complete new CrewAI acquisition tasks once; never overwrite them."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tasks/stage5_autogen/natural_tasks_r19_acquisition.json"


def tool(name: str, call: dict, result: dict) -> dict:
    return {"name": name, "call": call, "result": result,
            "evidence_parts": [f'"{k}": {json.dumps(v, ensure_ascii=False)}'
                               for k, v in list(result.items())[:2]]}


TASKS = [
    {
        "task_id": "quorum_snapshot_r19", "title": "快照法定人数准入",
        "task": "集群 cluster-q19 拟按 snapshot-q19 发布新快照。依次读取清单、投票、归档和最终检验，判断是否可发布。",
        "history_constraint": "cluster-q19 和 snapshot-q19 为当前标识；旧 snapshot-q11 已撤销，不得作为当前目标。",
        "history_topic": "旧快照撤销、归档延迟和过期投票讨论",
        "tools": [
            tool("read_quorum_manifest_r19", {"snapshot": "snapshot-q19"}, {"cluster": "cluster-q19", "snapshot": "snapshot-q19", "manifest_sha": "qm-19a", "replicas": 9}),
            tool("read_vote_counters_r19", {"snapshot": "snapshot-q19"}, {"snapshot": "snapshot-q19", "votes_valid": 7, "votes_required": 8, "votes_invalid": 0}),
            tool("read_snapshot_journal_r19", {"snapshot": "snapshot-q19"}, {"snapshot": "snapshot-q19", "journal_state": "open", "previous_snapshot": "snapshot-q11", "write_index": 4}),
            tool("run_quorum_probe_r19", {"snapshot": "snapshot-q19"}, {"cluster": "cluster-q19", "snapshot": "snapshot-q19", "quorum_ready": False, "votes_valid": 7, "votes_required": 8, "failed_checks": 1}),
        ],
        "decision": "PAUSE", "handle_facts": ["cluster-q19", "snapshot-q19", "quorum_ready false"],
        "answer_facts": ["quorum_ready false", "votes_valid 7", "votes_required 8"],
        "forbidden_facts": ["snapshot-q11"], "region_fact": "", "version_fact": "cluster-q19",
        "expected_terms": ["cluster-q19", "snapshot-q19", "quorum_ready false", "PAUSE"],
    },
    {
        "task_id": "cache_purge_r19", "title": "缓存清退准入",
        "task": "缓存 cache-f19 的工单 ticket-p19 拟执行清退。依次读取策略、陈旧项指标、清退记录和最终检验。",
        "history_constraint": "cache-f19 和 ticket-p19 为当前标识；旧 ticket-p03 已关闭，不得作为本次工单。",
        "history_topic": "旧缓存清退和陈旧项误报讨论",
        "tools": [
            tool("read_cache_policy_r19", {"cache": "cache-f19"}, {"cache": "cache-f19", "ticket": "ticket-p19", "policy_sha": "cp-19d", "ttl_hours": 6}),
            tool("read_eviction_metrics_r19", {"cache": "cache-f19"}, {"cache": "cache-f19", "stale_entries": 0, "active_entries": 430, "eviction_errors": 0}),
            tool("read_purge_journal_r19", {"ticket": "ticket-p19"}, {"ticket": "ticket-p19", "journal_state": "open", "previous_ticket": "ticket-p03", "purge_step": 5}),
            tool("run_cache_probe_r19", {"ticket": "ticket-p19"}, {"cache": "cache-f19", "ticket": "ticket-p19", "purge_ready": True, "stale_entries": 0, "eviction_errors": 0, "failed_checks": 0}),
        ],
        "decision": "SWEEP", "handle_facts": ["cache-f19", "ticket-p19", "purge_ready true"],
        "answer_facts": ["purge_ready true", "stale_entries 0", "eviction_errors 0"],
        "forbidden_facts": ["ticket-p03"], "region_fact": "", "version_fact": "cache-f19",
        "expected_terms": ["cache-f19", "ticket-p19", "purge_ready true", "SWEEP"],
    },
    {
        "task_id": "certificate_rollover_r19", "title": "证书轮换准入",
        "task": "证书槽 slot-c19 拟使用 chain-c19 完成轮换。依次读取清单、过期窗口、轮换记录和最终检验。",
        "history_constraint": "slot-c19 和 chain-c19 为当前标识；旧 chain-c04 已作废，不得用作本次目标。",
        "history_topic": "旧证书链作废、过期窗口和失败握手讨论",
        "tools": [
            tool("read_certificate_manifest_r19", {"slot": "slot-c19"}, {"slot": "slot-c19", "chain": "chain-c19", "manifest_sha": "cm-19f", "key_bits": 3072}),
            tool("read_expiry_window_r19", {"chain": "chain-c19"}, {"chain": "chain-c19", "days_to_expiry": 12, "expired_chains": 0, "window_state": "open"}),
            tool("read_rollover_journal_r19", {"slot": "slot-c19"}, {"slot": "slot-c19", "journal_state": "open", "previous_chain": "chain-c04", "rollover_step": 3}),
            tool("run_certificate_probe_r19", {"slot": "slot-c19"}, {"slot": "slot-c19", "chain": "chain-c19", "rollover_ready": True, "expired_chains": 0, "handshake_failures": 0, "failed_checks": 0}),
        ],
        "decision": "ROLL", "handle_facts": ["slot-c19", "chain-c19", "rollover_ready true"],
        "answer_facts": ["rollover_ready true", "expired_chains 0", "handshake_failures 0"],
        "forbidden_facts": ["chain-c04"], "region_fact": "", "version_fact": "slot-c19",
        "expected_terms": ["slot-c19", "chain-c19", "rollover_ready true", "ROLL"],
    },
]


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"write-once task file already exists: {OUT}")
    OUT.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
