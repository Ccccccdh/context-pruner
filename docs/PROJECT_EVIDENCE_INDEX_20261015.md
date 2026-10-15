# 当前项目证据索引与引用口径

更新日期2026-10-15。用途：区分宿主、任务族、候选版本、开发与确认，以及证据不足的情形。根入口为HANDOFF_TWO_HOSTS_20261015.md，历史HANDOFF_TWO_HOSTS_20261013.md保持冻结。

| 证据ID | 宿主/任务 | 结果口径 | 入口 |
|---|---|---|---|
| LEGACY-REPAIR-K | Agents旧Django修复 | 旧K轴策略费用上升、通过数下降；历史交接记载，未在本次复跑 | ../HANDOFF_TWO_HOSTS_20261013.md |
| LEGACY-DIAG | Agents旧诊断 | 字符串端点，不能称修复质量 | ../HANDOFF_TWO_HOSTS_20261013.md §3 |
| LEGACY-OH | OpenHands旧代码任务 | v71作废；v74未触发，原事件流缺失限制保留 | ../HANDOFF_TWO_HOSTS_20261013.md |
| REG-V5 | Agents registry正式质量/成本 | 45样本；质量非劣及费用收益未确认，不能隐藏负结果 | AGENTS_FORMAL_QUALITY_COST_V5_RESULT_20261014.md |
| REG-V6 | Agents来源清单开发 | 首轮5完成、1基础设施失败/未知usage；不补跑或当全通过 | AGENTS_V6_DEVELOPMENT_RESULT_20261015.md |
| REG-V6-FOLLOWUP | Agents独立registry开发 | 6完成/通过；小样本描述，不是正式确认 | AGENTS_V6_FOLLOWUP_RESULT_20261015.md |
| REG-V7 | Agents统一归档读取开发 | 12完成、11质量通过；宿主复用可用，额外费用收益未确认 | AGENTS_V7_RESULT_20261015.md |
| REG-V8-DEV | Agents记录粒度开发 | 12完成/通过；开发门通过，不计入确认样本 | AGENTS_V8_RESULT_20261015.md |
| REG-V8-CONFIRM | Agents记录粒度确认 | 48新实例/144通过；联合标准通过，限合成任务族和协议假设 | AGENTS_V8_CONFIRMATION_RESULT_20261015.md |
| CODE-V9-DESIGN | Agents真实代码读取 | 仅新设计和路径盘点，无实现/测试/付费结果 | superpowers/specs/2026-10-15-agents-code-evidence-v9-design.md |

## 可引用的主要数值

REG-V8-CONFIRM：B1/B0累计总token−24.51%，累计usage估价−16.63%；实例级比值几何均值分别0.751715476/0.821995332，单侧95%上界分别0.765576514/0.873469332。累计量与几何均值不得混写。两套费用是同usage按成比例价格估算，非两份独立实验。

质量：B1/B2各48/48，预设10个百分点非劣界值，保守差值下界−7.40个百分点；不写真实失败率0、不写任意任务均不降质量。总请求1008、未知usage0，总估价USD0.256603218—0.513206436，非账单核销。

宿主复用：V8确认96次归档命中实际投递、来源构造192→96、完整性校验均720；这不是模型自主恢复、不是实测延迟收益，也不是归档导致额外LLM费用下降的独立确认。

## 不可合并或替换

不跨宿主合并百分比；不把registry确认说成Django修复确认；不把旧行范围工具称为全文；不把字节缩减当真实token；不将开发任务纳入新留出；不删基础设施/未知usage；不把旧作废批次当有效结果；不由“没有显著差异”推出质量非劣。

V8全部三组共享裁剪，不能单凭本轮证明裁剪相对无裁剪的效应。V9拟设C0不裁剪、C1裁剪/全文回传模式、C2同裁剪/代码单元回传模式，分别识别两种改变。

## 主要完整性锚点

| 根目录（runs/下） | 结项文件数 | completion_manifest.json SHA256 |
|---|---:|---|
| agents-receipt-v6-20261015 | 400 | 35612d60a4cbc6e97b2237c83bdebc10daed73ace63428f0c5f1d22b799ca9a4 |
| agents-receipt-v6-followup-20261015 | 411 | 7c39cf7dd71e765d2bd65b697bde08b6f86f6563e4576e7cdf491107ba870c51 |
| agents-archive-first-v7-20261015 | 801 | 4cea06d0ee3b2e0a0dcd0863390f7e14ad245b0b0d4eb4ff14548592ae3c2150 |
| agents-minimal-evidence-v8-20261015 | 814 | 33c510d420a83b6ade1c5dfcec9acc55f48db7e21773fdd66af85bc62c59beb8 |
| agents-minimal-evidence-confirmation-v8-20261015 | 8786 | dae8d3b3e0440931345b486cb7d2b20ff997049e97880f179f3e34eb095287f3 |

本次文档整理新增文件，不改这些清单、原交接、协议、代码或结果。历史旧结果文件当前缺失/位置未定位时，引用注明来自旧交接，不能声称本次已复现。
