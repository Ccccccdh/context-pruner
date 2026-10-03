# 付费后审计字段修正

第一次审计报 `provider budget mismatch`，其余 27 个样本、144 个请求、成功率和配对值均吻合。根因是冻结审计脚本读取 `manifest.budget_calibration.provider`；实际 runner 从付费前便写入 `provider_tokens`。冻结脚本原字节存于同目录 `audit_pre_run_snapshot.py`，哈希与 `NATURAL_V1_R3_DEV_FREEZE.json` 一致。

独立修正版 `experiments/audits/audit_openai_agents_natural_r3.py` 仅将读取字段改为 `provider_tokens`，并用原字节快照验证冻结审计器哈希；`audit.json` 现为 `complete: true`、`issues: []`。修正发生在付费运行结束之后，因此原脚本与修正版的身份和结果均单独说明，不能把修正版称为付费前已冻结的审计器。
