"""Fixed-input, multi-step operational tasks for the OpenAI Agents pilot.

The records below are curated simulated fixtures. They are sent to the model
only through tools; expected answers remain local to the runner/auditor.
"""

from __future__ import annotations

from agents import function_tool

from experiments.runners import run_openai_agents_api_experiment as base


TASKS = ("incident_triage", "invoice_reconcile", "release_gate")


@function_tool
def read_service_log() -> str:
    """Read the latest checkout service error log."""
    return "INC-204 checkout: 502 after release 2026.10.1; payment authorization failed: PAYMENT_TOKEN missing. Earlier requests succeeded."


@function_tool
def read_deploy_record() -> str:
    """Read the checkout deployment record."""
    return "INC-204 deployment 2026.10.1 at 09:12 removed PAYMENT_TOKEN from the service environment; previous release 2026.09.4 had it."


@function_tool
def read_incident_runbook() -> str:
    """Read the incident response runbook."""
    return "For a newly missing required secret after deployment, roll back the release and then restore the secret through the approved configuration path. Do not restart repeatedly."


@function_tool
def read_invoice() -> str:
    """Read the vendor invoice."""
    return "INV-17: services 1200 USD, tax 120 USD, total due 1320 USD."


@function_tool
def read_payment_ledger() -> str:
    """Read settled payments for the vendor invoice."""
    return "INV-17: one settled payment of 1200 USD; no other payment or credit note."


@function_tool
def read_billing_policy() -> str:
    """Read the billing reconciliation policy."""
    return "A balance remains open when invoice total exceeds settled payments. Request the outstanding balance; do not mark paid until the full total is settled."


@function_tool
def read_ci_matrix() -> str:
    """Read continuous integration results for the proposed release."""
    return "REL-9: Linux 421/421 passed, macOS 421/421 passed, Windows 419/421 passed; two Windows path tests failed."


@function_tool
def read_release_policy() -> str:
    """Read the release approval policy."""
    return "A release must be held until every required platform is green. Windows is required. A failure may not be waived by a green Linux or macOS result."


@function_tool
def read_fix_status() -> str:
    """Read the current fix status."""
    return "REL-9: a candidate Windows path fix is under review; no rerun has passed yet."


_CASE = {
    "incident_triage": {
        "id": "INC-204",
        "request": "Triage INC-204. Read the service log, deployment record, and runbook in that order. State the root cause and immediate action using the evidence, without inventing a fix.",
        "tools": [read_service_log, read_deploy_record, read_incident_runbook],
        "names": ("read_service_log", "read_deploy_record", "read_incident_runbook"),
        "terms": ("INC-204", "PAYMENT_TOKEN"),
        "contract": "RESULT incident=INC-204 cause=<root_cause> action=<immediate_action>",
        "pattern": r"RESULT incident=INC-204 cause=.*PAYMENT_TOKEN.* action=.*roll.?back.*",
    },
    "invoice_reconcile": {
        "id": "INV-17",
        "request": "Reconcile INV-17. Read the invoice, settled payment ledger, and billing policy in that order. Calculate the outstanding USD balance and state the next action.",
        "tools": [read_invoice, read_payment_ledger, read_billing_policy],
        "names": ("read_invoice", "read_payment_ledger", "read_billing_policy"),
        "terms": ("INV-17",),
        "contract": "RESULT invoice=INV-17 outstanding_usd=<balance> action=<next_action>",
        "pattern": r"RESULT invoice=INV-17 outstanding_usd=120(?:\.00)? action=.*(?:request|collect|follow.?up).*",
    },
    "release_gate": {
        "id": "REL-9",
        "request": "Decide whether REL-9 may ship. Read the CI matrix, release policy, and fix status in that order. State the decision and reason.",
        "tools": [read_ci_matrix, read_release_policy, read_fix_status],
        "names": ("read_ci_matrix", "read_release_policy", "read_fix_status"),
        "terms": ("REL-9", "hold", "Windows"),
        "contract": "RESULT release=REL-9 decision=<ship_or_hold> reason=<brief_reason>",
        "pattern": r"RESULT release=REL-9 decision=hold reason=.*Windows.*",
    },
}


def build_case(scenario: str, repeat: int) -> base.ApiCase:
    """The repeat label never changes the task input, tools, or evidence."""
    task = _CASE[scenario]
    history = [
        {"role": "system", "content": f"Operational case {task['id']}. Treat old notes as background; use current tool evidence for the decision."},
        {"role": "user", "content": f"Start a review log for {task['id']}; prior status is provisional."},
    ]
    note = (
        f"Historical coordination for {task['id']}: the team recorded an open item, "
        "but did not verify it against current systems. The incident owner asked for "
        "the next reviewer to consult the authoritative records before deciding. "
        "Prior chat observations may be stale; the current tool records govern. "
    )
    for index in range(9):
        history.extend((
            {"role": "assistant", "content": f"Review note {index}: " + note * 2},
            {"role": "user", "content": f"Status reminder {index}: " + note},
        ))
    history.append({"role": "user", "content": task["request"]})
    return base.ApiCase(
        scenario=scenario,
        repeat=repeat,
        codename=task["id"],
        history=history,
        tools=task["tools"],
        expected_terms=task["terms"],
        expected_tool_names=task["names"],
        expected_model_calls=4,
        final_contract=task["contract"],
        answer_pattern=task["pattern"],
        allow_repeat_tools=True,
        disable_thinking=True,
    )


def main(argv=None) -> int:
    base.SCENARIOS = TASKS
    base.build_case = build_case
    base.SYNTHETIC_DISCLOSURE = (
        "Curated simulated operational tasks, fixed histories, tool arguments, and "
        "tool results are sent to the configured API endpoint. No workspace files "
        "or expected answers are sent. The repeat index changes only arm order."
    )
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
