"""Report mechanism evidence separately from the small live development trial."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'runs/stage5-openhands/dotenv-three-arm-v10-pilot'
audit = json.loads((OUT / 'audit.json').read_text(encoding='utf-8'))
analysis = json.loads((OUT / 'revision-analysis.json').read_text(encoding='utf-8'))
replay = json.loads((ROOT / 'runs/stage5-openhands/semantic-version-replay-v5/replay.json').read_text(encoding='utf-8'))
assert audit['complete'] and len(audit['samples']) == 9
metrics = analysis['paired']['pruner_v5']['metrics']
lines = ['# v5 文件版本与重复记忆修复', '',
         '本轮修复了 v4 的具体残留问题：旧记忆作为受保护状态被再次并入新记忆，使代码证据重复，且成功编辑后旧版本仍占据上下文。v4 原报告及源文件保持不变。', '',
         '## 实现', '',
         '- 从已观察到的原始 SDK 事件重建候选记忆，不把旧派生摘要再次作为输入。',
         '- 按文件和成功编辑次数标记版本，合并同版本带行号的读取；成功编辑的工具快照替换旧源码证据。',
         '- 失败编辑单独标明，不能改变已确认文件版本；不读取磁盘、不检查隐藏测试来构造记忆。',
         '- 用户要求保留为原始事件；完整历史工具批次可以在这些要求之后压缩，最近批次保留。每次均通过 SDK 视图结构检查。',
         '- 遇到无法识别的已有摘要（例如实例重建后缺少私有事件缓存），保守返回原视图。当前仍未实现缓存恢复及自动归档检索闭环。', '',
         '## 固定轨迹回放：零模型请求', '',
         '使用同一批原始事件和工具动作，对两条旧轨迹中的四个检查点比较派生视图；全部使用同一重建 SDK 计数方式。这是局部机制证据，不是新的端到端任务结果。', '',
         '| 轨迹检查点 | v4 视图 token | v5 视图 token | 相对减少 | 完整函数体保留 |', '|---|---:|---:|---:|---|']
for row in replay['checkpoints']:
    checks = row.get('function_bodies_preserved', {})
    assert checks and all(checks.values())
    lines.append(f"| {row['sample']} / {row['old_condensation_id'][:8]} | {row['old_after']} | {row['v5_after']} | {1 - row['v5_after'] / row['old_after']:.2%} | decode_escapes、parse_value |")
lines += ['', '所有检查点均保留 _single_quote_escapes，旧记忆头未嵌套；重复压缩后 v5 记忆保持约 1.7 万字符，原 v4 有约 3.9 万字符的记忆。专项回归共 12 项通过，其中 4 项覆盖这次版本治理、重复读取、连续压缩及重建后的保守回退。', '',
          '## 真实三组试运行', '',
          '| 组别 | 正常完成 | 最终文件通过 | 总输入 token | 模型请求 |', '|---|---:|---:|---:|---:|']
for arm, row in audit['arms'].items():
    lines.append(f"| {arm} | {row['successes']}/3 | {row['artifact_successes']}/3 | {row['total_input_tokens']} | {row['model_calls']} |")
lines += ['', f"插件三项配对平均输入节省 **{metrics['all']['mean']:.2%}**，2/3 对省输入；本轮无 API 请求失败。", '',
          '| 任务 | 基线输入 | 插件输入 | 节省 | 基线文件通过 | 插件文件通过 |', '|---|---:|---:|---:|---|---|']
for row in audit['paired']['pruner_v5']['pairs']:
    lines.append(f"| {row['task']} | {row['baseline_input']} | {row['method_input']} | {row['input_savings']:.2%} | {row['baseline_success']} | {row['method_success']} |")
lines += ['', f"双方完成的两项任务平均节省 **{metrics['both_normal_success']['mean']:.2%}**（负数代表增加）。", '',
          '反斜杠和 CRLF 样本未触发插件压缩，因此其正负差异不能归因于裁剪。最大节省来自空值注释：基线 14 次调用且最终仍有一项测试失败，插件 9 次调用并通过。该差异同时包含调用轨迹和质量变化，不能把 52.41% 全部称为压缩机制收益。', '',
          '## 结论与下一步', '',
          '具体的重复记忆与旧文件版本残留问题已有代码修复、回归检查及固定动作流证据；真实插件试运行三项均通过。但试运行仅三项旧任务、每组一次，不能证明稳定省输入、质量等价或已达到 30% 目标。', '',
          '后续应把旧任务留作开发集，冻结不同项目的自然长任务，再做三组重复及文件版本治理消融。不能仅靠反复重跑旧任务获得正数来确认目标。', '',
          '证据：`runs/stage5-openhands/semantic-version-replay-v5/replay.json`、`runs/stage5-openhands/dotenv-three-arm-v10-pilot/`。实现：`context_pruner/adapters/openhands_v5.py`。']
body = '\n'.join(lines) + '\n'
Path(__file__).with_name('V5_REPAIR_REVIEW.md').write_text(body, encoding='utf-8', newline='\n')
for filename, marker in [('runs/HANDOFF_NEXT_CHAT.md', '\n## 10. v5 文件版本修复与试运行\n'),
                         ('README.md', '\n## 18. 最新 OpenHands v5 文件版本修复\n')]:
    path = ROOT / filename
    old = path.read_text(encoding='utf-8').split(marker)[0]
    text = body if filename.startswith('runs/') else (
        '最新适配器为 `context_pruner/adapters/openhands_v5.py`，详情见 `integrations/openhands/V5_REPAIR_REVIEW.md`。\n\n'
        '已修复重复记忆及旧源码残留，同轨迹四个检查点的派生视图较 v4 减少 20.51%–39.67%，完整关键函数体保留。真实三组试运行插件 3/3 完成，配对平均输入节省 16.84%；双方完成子集两对平均为 -0.94%。样本小，尚不能宣称稳定节省或达到 30%。\n')
    path.write_text(old.rstrip() + '\n' + marker + '\n' + text, encoding='utf-8', newline='\n')
print('Wrote V5_REPAIR_REVIEW.md, README and handoff update.')
