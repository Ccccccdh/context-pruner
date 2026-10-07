"""Build the prospective variable-source CrewAI task set once."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tasks/stage5_autogen/natural_tasks_r27_variable.json"


def source(name: str, call: dict, result: dict, keys: tuple[str, ...]) -> dict:
    serialized = json.dumps(result, ensure_ascii=False)
    parts = [json.dumps({key: result[key]}, ensure_ascii=False)[1:-1] for key in keys]
    assert all(part in serialized for part in parts)
    return {"name": name, "call": call, "result": result, "evidence_parts": parts}


TASKS = [
    {
        "task_id": "shipment_customs_dispatch",
        "title": "三源出关发运核验",
        "task": "货运单 shipment-641 的报关批次 customs-18 申请发运。先读货单与合规标记，再查放行校验；把三项当前来源都至少读取一次，顺序不限、可重复。依据货物数量、限制标记和校验状态综合决定发运或扣留。",
        "history_constraint": "shipment-641 与 customs-18 才是本次对象；旧批次 customs-11 已撤销，不得当作当前报关批次。",
        "history_topic": "旧报关批次撤销记录、限制标记争议和过期发运窗口",
        "tools": [
            source("read_shipment_manifest", {"shipment": "shipment-641"},
                   {"shipment": "shipment-641", "batch": "customs-18", "package_count": 16, "manifest_sha": "ship-6ab1"},
                   ("shipment", "batch", "package_count")),
            source("read_compliance_flags", {"batch": "customs-18"},
                   {"batch": "customs-18", "restricted_flags": 0, "inspection_state": "passed", "previous_batch": "customs-11"},
                   ("restricted_flags", "inspection_state")),
            source("run_dispatch_check", {"shipment": "shipment-641"},
                   {"shipment": "shipment-641", "dispatch_ready": True, "blocking_holds": 0, "checker_count": 2},
                   ("dispatch_ready", "blocking_holds")),
        ],
        "decision": "SHIP", "handle_facts": ["shipment-641", "customs-18", "dispatch_ready true"],
        "answer_facts": ["package_count 16", "restricted_flags 0", "dispatch_ready true", "blocking_holds 0"],
        "forbidden_facts": ["customs-11"], "region_fact": "", "version_fact": "customs-18",
        "expected_terms": ["shipment-641", "customs-18", "dispatch_ready true", "SHIP"],
    },
    {
        "task_id": "cluster_failover_hold",
        "title": "五源集群故障转移审核",
        "task": "集群 cluster-e6 的转移申请 failover-29 需要审核。分别核对拓扑、副本健康、流量容量、变更记录和最终激活校验，五项来源都至少读取一次，顺序不限、可重复。容量即使足够，只要副本落后且激活校验未通过就必须暂缓。",
        "history_constraint": "cluster-e6 与 failover-29 是本次申请；旧申请 failover-17 已取消，不得作为当前申请。",
        "history_topic": "旧故障转移申请取消记录、副本追赶争议与过期切流窗口",
        "tools": [
            source("read_failover_topology", {"cluster": "cluster-e6"},
                   {"cluster": "cluster-e6", "request": "failover-29", "replica_count": 4, "topology_sha": "topo-8d3c"},
                   ("cluster", "request")),
            source("read_replica_health", {"cluster": "cluster-e6"},
                   {"cluster": "cluster-e6", "lagging_replicas": 1, "max_lag_seconds": 48, "health_state": "degraded"},
                   ("lagging_replicas", "health_state")),
            source("read_traffic_capacity", {"cluster": "cluster-e6"},
                   {"cluster": "cluster-e6", "capacity_headroom_pct": 35, "route_state": "available"},
                   ("capacity_headroom_pct", "route_state")),
            source("read_failover_change_log", {"request": "failover-29"},
                   {"request": "failover-29", "record_state": "open", "previous_request": "failover-17", "change_step": 3},
                   ("record_state", "previous_request")),
            source("run_activation_check", {"cluster": "cluster-e6"},
                   {"cluster": "cluster-e6", "activation_ready": False, "blocking_checks": 1, "reviewer_count": 3},
                   ("activation_ready", "blocking_checks")),
        ],
        "decision": "WAIT", "handle_facts": ["cluster-e6", "failover-29", "activation_ready false"],
        "answer_facts": ["lagging_replicas 1", "capacity_headroom_pct 35", "activation_ready false", "blocking_checks 1"],
        "forbidden_facts": ["failover-17"], "region_fact": "", "version_fact": "failover-29",
        "expected_terms": ["cluster-e6", "failover-29", "activation_ready false", "WAIT"],
    },
]


def main() -> None:
    if OUT.exists():
        raise SystemExit("r27 task set is write-once")
    OUT.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
