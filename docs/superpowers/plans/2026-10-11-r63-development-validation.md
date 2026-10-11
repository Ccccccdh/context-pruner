# R63 Development Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the R63 task-anchor change on the already-used Sphinx 10673 task with at most 120 paid DeepSeek requests and independently audited quality.

**Architecture:** Reuse the existing R62 10673 zero-API gate and generic code pilot, but give R63 a distinct write-once freeze, batch name, durable ledger, and independent audit. Do not reuse or mutate R62 outputs. This is a development validation, not a holdout or saving claim.

**Tech Stack:** Python, unittest/pytest, CrewAI, DeepSeek OpenAI-compatible API, SWE-bench Verified host harness.

**Spec:** `integrations/crewai/R64_RELIABILITY_DESIGN_PROPOSAL_20261011.md`, stage 1 only; user approved at most 120 DeepSeek requests for this development batch.

## Global Constraints

- Task: `sphinx-doc__sphinx-10673`, already used; baseline source must be clean at `f35d2a6cc726f97d0e859ca7a0e1729f7da8a6c8`.
- Arms: `none_r0`, `none_r1`, `none_r2`; model `deepseek-v4-flash`; at most 40 requests per arm and 120 total.
- Freeze current R63 source hashes before provider calls; write-once paths; reject source drift and existing output.
- Use existing positive/negative zero-API gate; do not read reference patch or send host tests/credentials to model.
- Independent audit must compare ledger usage and fresh hidden-host evaluations, inspect credential leakage, and require at least 2/3 hidden-host passes plus a successful input peak at least 9,000 tokens.
- Output must state `citable_as_saving: false`. No plugin arm in this development batch.
- Do not commit or mutate user-owned unrelated files in the dirty primary checkout; isolate run workspaces under `.tooling/tmp`.

## Review Focus

- Existing R62 output: R63 must refuse to write into it; test unique batch paths.
- Missing API key: freeze or run must fail before creating paid output; test missing key branch.
- Source drift after freeze: run must refuse before a provider request; test drift branch.
- One arm exhausting 40 requests: ledger must prevent a 41st; use existing durable-budget tests and audit per-arm counts.
- Incomplete provider batch: audit must not mark admission or savings; test partial rows.

---

### Task 1: Write-once R63 freeze and capped runner

**Files:**
- Create: `experiments/commands/prepare_crewai_r63_development.py`
- Create: `experiments/commands/run_crewai_r63_development.py`
- Create: `tests/test_crewai_r63_development_contract.py`

**Interfaces:**
- Produces `prepare_crewai_r63_development.main() -> None`, a write-once development freeze.
- Produces `run_crewai_r63_development.load_freeze() -> dict` and `main() -> None`, a write-once development batch with three result rows and durable ledger.
- Task 2 consumes these exact paths and row/ledger contracts.

- [ ] Write tests for unique batch path, 40/120 caps, three baseline arms, development-only attribution, freeze source drift rejection, and existing output rejection. Tests should fail because R63 modules do not exist.
- [ ] Run `& .venv-crewai/Scripts/python.exe -m unittest -q tests.test_crewai_r63_development_contract`; expected import failure for missing R63 modules.
- [ ] Implement minimal prepare/run wrappers around R62 patterns and generic `run_arm`; retain explicit `--confirm-send-public-source` flag and zero-retry provider client.
- [ ] Rerun the focused unittest; expected all pass.
- [ ] Run relevant R62 contract/unit tests and syntax compile; expected pass. No commit because primary checkout has user-owned changes.

### Task 2: Independent R63 audit and result gate

**Files:**
- Create: `experiments/audits/audit_crewai_r63_development.py`
- Extend: `tests/test_crewai_r63_development_contract.py`

**Interfaces:**
- Consumes R63 freeze and batch, produces write-once `audit.json` with `errors`, `request_slots`, `fresh_host_passes`, `max_input_tokens_by_repeat`, `admission_met`, `citable_as_saving: false`.

- [ ] Add tests for 2/3 + 9,000 admission, 1/3 rejection, and partial/incomplete rows rejection. Tests should fail because audit module is missing.
- [ ] Run focused unittest; expected missing audit module or symbol.
- [ ] Implement independent ledger/host audit using R62 audit as a template but R63 paths and explicit development-only interpretation.
- [ ] Rerun focused unittest and R62 audit tests; expected pass.
- [ ] Freeze, run preflight, execute at most 120 paid requests, and run the independent audit. Read all outputs and report whether R63 development gate passed. Never change R62 frozen files.

## Self-review

The stage-1 spec is covered by Tasks 1–2. Task 2 consumes only Task 1's named freeze, batch and result contracts. No new plugin behavior or holdout selection is in scope. The primary checkout already contains user-owned changes, so the prior in-place isolation ruling takes precedence over worktree creation and commit steps.

## Execution ruling and result

Ruling: `api-01` failed before any provider request because the Windows sandbox denied Git for Windows/MSYS named-object creation. Preserve its write-once output and freeze; use `api-02` with a new freeze and run under the permissions that passed a separate local clone probe. Cost if wrong: the second batch could inherit a hidden environmental difference, so its provenance and audit must remain separate. Both tests and independent audit completed; see `integrations/crewai/R63_DEVELOPMENT_RESULT_20261011.md`. No commit was made in the user-owned dirty primary checkout.
