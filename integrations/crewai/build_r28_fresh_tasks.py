"""Build two fresh variable-source confirmation tasks once."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tasks/stage5_autogen/natural_tasks_r28_fresh.json"


def source(name: str, call: dict, result: dict, keys: tuple[str, ...]) -> dict:
    serialized = json.dumps(result, ensure_ascii=False)
    parts = [json.dumps({key: result[key]}, ensure_ascii=False)[1:-1] for key in keys]
    assert all(part in serialized for part in parts)
    return {"name": name, "call": call, "result": result, "evidence_parts": parts}


TASKS = [
    {
        "task_id": "invoice_settlement_finalize",
        "title": "三源发票结算核验",
        "task": "结算集 invoices-q12 的批次 settlement-76 申请结算。核对批次清单、支付对账和最终结算校验，三项来源都至少读取一次，顺序不限、可重复。只有无未支付项目且结算校验通过才可完成。",
        "history_constraint": "invoices-q12 与 settlement-76 是本次对象；旧批次 settlement-52 已撤销，不得作为当前结算。",
        "history_topic": "旧结算批次撤销记录、支付对账争议和过期审核窗口",
        "tools": [
            source("read_invoice_batch", {"set": "invoices-q12"},
                   {"set": "invoices-q12", "batch": "settlement-76", "invoice_count": 17, "batch_sha": "bill-8c24"},
                   ("set", "batch", "invoice_count")),
            source("read_payment_reconciliation", {"batch": "settlement-76"},
                   {"batch": "settlement-76", "unpaid_items": 0, "matched_amount": 8300, "previous_batch": "settlement-52"},
                   ("unpaid_items", "matched_amount")),
            source("run_settlement_check", {"set": "invoices-q12"},
                   {"set": "invoices-q12", "settlement_ready": True, "blocking_errors": 0, "reviewer_count": 2},
                   ("settlement_ready", "blocking_errors")),
        ],
        "decision": "FINALIZE", "handle_facts": ["invoices-q12", "settlement-76", "settlement_ready true"],
        "answer_facts": ["invoice_count 17", "unpaid_items 0", "settlement_ready true", "blocking_errors 0"],
        "forbidden_facts": ["settlement-52"], "region_fact": "", "version_fact": "settlement-76",
        "expected_terms": ["invoices-q12", "settlement-76", "settlement_ready true", "FINALIZE"],
    },
    {
        "task_id": "dns_cutover_wait",
        "title": "五源 DNS 切流审核",
        "task": "域 zone-k9 的切流申请 cutover-62 需要审核。依次核对拓扑、探测结果、TTL 观测、变更记录与最终切流校验；五项来源都至少读取一次，顺序不限、可重复。即使 TTL 达标，探测失败或最终校验未通过也必须暂缓。",
        "history_constraint": "zone-k9 与 cutover-62 是本次申请；旧申请 cutover-41 已取消，不得当作当前切流。",
        "history_topic": "旧切流申请取消记录、探测争议与过期变更窗口",
        "tools": [
            source("read_zone_topology", {"zone": "zone-k9"},
                   {"zone": "zone-k9", "request": "cutover-62", "target_nodes": 5, "topology_sha": "dns-2a4e"},
                   ("zone", "request")),
            source("read_endpoint_probe", {"zone": "zone-k9"},
                   {"zone": "zone-k9", "failed_probes": 1, "probe_count": 12, "probe_state": "degraded"},
                   ("failed_probes", "probe_state")),
            source("read_ttl_observation", {"zone": "zone-k9"},
                   {"zone": "zone-k9", "ttl_seconds": 60, "ttl_state": "within_limit"},
                   ("ttl_seconds", "ttl_state")),
            source("read_cutover_change_log", {"request": "cutover-62"},
                   {"request": "cutover-62", "record_state": "open", "previous_request": "cutover-41", "change_step": 4},
                   ("record_state", "previous_request")),
            source("run_cutover_check", {"zone": "zone-k9"},
                   {"zone": "zone-k9", "cutover_ready": False, "blocking_checks": 1, "reviewer_count": 3},
                   ("cutover_ready", "blocking_checks")),
        ],
        "decision": "WAIT", "handle_facts": ["zone-k9", "cutover-62", "cutover_ready false"],
        "answer_facts": ["failed_probes 1", "ttl_seconds 60", "cutover_ready false", "blocking_checks 1"],
        "forbidden_facts": ["cutover-41"], "region_fact": "", "version_fact": "cutover-62",
        "expected_terms": ["zone-k9", "cutover-62", "cutover_ready false", "WAIT"],
    },
]


def main() -> None:
    if OUT.exists():
        raise SystemExit("r28 task set is write-once")
    OUT.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
