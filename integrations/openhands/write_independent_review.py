"""Publish audited validation results after both rounds finish."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def main():
    rounds = {}
    for version in ('v11', 'v12'):
        out = ROOT / 'runs/stage5-openhands' / f'independent-two-projects-3x3-{version}'
        audit = json.loads((out / 'audit.json').read_text(encoding='utf-8'))
        diag = json.loads((out / 'trace-diagnostics.json').read_text(encoding='utf-8'))
        assert audit['complete'] and len(audit['samples']) == 18
        rounds[version] = audit, diag
    lines = ['# 独立项目长任务验证结果', '',
             '两轮均完成 18 个真实样本，共 36 个；两个新公开项目、三组、每任务三次。插件保持冻结 v5。全部失败保留；供应商输入包括原生摘要开销。', '',
             '## 设计与解释', '',
             '- Click 8.2.1：命令别名注册、调用、装饰器、补全与原子错误处理。',
             '- packaging 25.0：依赖名称/版本匹配、marker/extras 和迭代器筛选。',
             '- 功能合同为评估者设计；不是上游已确认漏洞，也不是随机抽取的真实工业需求。',
             '- 原版分别通过 110 和 8,337 项选定回归；新功能分别有 15 和 21 项验收失败。主机隔离参考实现全部通过 125 和 8,358 项。参考代码不进入 Agent 工作目录。',
             '- v11 使用只读调查→实现→反馈，发现持续阅读直到预算耗尽。v12 合并读取与实现，仅在失败后修正；统一增大三组预算。两轮不能混算，也不能把 v12 称作未接触过任务的首次盲测。',
             '- 两轮部分并行执行；每轮内部串行轮换三组。不据此声称运行时间优劣。', '',
             '两轮另有每阶段 SDK 36 步上限（运行器源码已冻结），并不等同于 36 次模型请求；返回压缩结果的步骤也可能消耗 SDK 步数。因此这是给定双重预算条件下的结果，不是无限预算能力比较。', '',
             '## 审计结果', '',
             '| 轮次 | 方法 | 正常完成 | 最终代码通过 | 实际总输入 | 压缩事件 | 预算中断 | API 失败请求 |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for version, (audit, diag) in rounds.items():
        for arm, row in audit['arms'].items():
            lines.append(f"| {version} | {arm} | {row['successes']}/6 | {row['artifact_successes']}/6 | {row['total_input_tokens']:,} | {row['condensation_events']} | {row['call_or_token_limit_exhaustions']} | {row['failed_calls']} |")
    fmt = lambda x: '不可用' if x is None else f'{x:.2%}'
    lines += ['', '| 轮次 | 方法 | 全配对输入减少率 | API 正常配对减少率 | 双方通过且 API 正常 |', '|---|---|---:|---:|---:|']
    for version, (audit, diag) in rounds.items():
        for method, data in audit['paired'].items():
            metrics = data['metrics']
            cells = [f"{fmt(metrics[k]['mean'])}，n={metrics[k]['n']}" for k in ('all', 'api_ok', 'both_success_api_ok')]
            lines.append(f'| {version} | {method} | ' + ' | '.join(cells) + ' |')
    audit, diag = rounds['v12']
    lines += ['', '## v12 轨迹计数', '', '| 方法 | 文件读取 | 相同路径/范围的重复读取 | 确认修改 |', '|---|---:|---:|---:|']
    for arm in ('none', 'native_summary', 'pruner_v5'):
        traces = [r for r in diag['samples'] if r['arm'] == arm]
        values = [sum(r[k] for r in traces) for k in ('view_actions', 'repeated_identical_views', 'confirmed_mutations')]
        lines.append('| ' + arm + ' | ' + ' | '.join(str(v) for v in values) + ' |')
    traces = [r for r in diag['samples'] if r['arm'] == 'pruner_v5']
    lines += ['', f"插件压缩 {sum(r['pruner_accepted_compressions'] for r in traces)} 次，压缩后超过硬界标记 {sum(r['pruner_hard_misses'] for r in traces)} 次。重复读取计数未扣除修改后的合理重读；它是轨迹诊断，不单独证明失败因果。"]
    base, plugin = audit['arms']['none'], audit['arms']['pruner_v5']
    mean = audit['paired']['pruner_v5']['metrics']['both_success_api_ok']['mean']
    insufficient = plugin['artifact_successes'] < base['artifact_successes'] or mean is None or mean < .3
    conclusion = ('本次验证没有确认“质量保持且稳定减少 30% 输入”。' if insufficient else
                  '本次两个任务的配对结果达到描述性 30% 目标；样本范围和数量仍不能证明普遍质量等价或稳定外推。')
    lines += ['', '## 结论', '', conclusion,
              '代码验收与正常结束已分开：SDK 到达迭代上限可能直接返回，原始 runner 的 success 只反映最终代码测试与边界；此报告额外根据 ConversationErrorEvent 校正正常完成率，原始数据未重写。', '',
              '## 实现限制、待验证故障原因与修复顺序', '',
              '1. **预算只是标记。** v5 的 hard_tokens 超限会写审计记录，却不保证派生上下文在硬界内；完整函数证据保护和编辑返回的整文件快照可使记忆持续扩大。应做按任务符号选择的代码证据与可恢复归档，不能只截断函数字符串。',
              '2. **任务合同来源覆盖不足。** 当前 task_state 从用户消息构造；本轮功能合同由 TASK.md 的工具读取提供，工具证据中的长说明可能被摘要。需将明确任务文件的合同作为受保护任务状态，并检查完整出站请求的约束覆盖。摘要关键词缺失本身不等于因果证明。',
              '3. **长任务循环。** 轨迹有大量相同范围读取和预算耗尽；没有终端测试工具，Agent 在 host 验收前可能继续检查。应提供范围受控的测试工具并定义明确完成条件，然后用新的冻结协议复验。',
              '4. **完成状态审计。** 后续 runner 要把 SDK 正常结束、API 中断、预算中断与代码通过分别记录，不能只读取 pytest 结果。',
              '以上是下一版本的开发方向，本轮未改动冻结插件或追改样本。不要把本轮负面结果删除或拿早停的低输入宣传收益。', '',
              '## 证据与复现', '',
              '- 两轮目录：runs/stage5-openhands/independent-two-projects-3x3-v11 与 v12；manifest.json、原始事件、请求 ledger、report、最终工作目录、audit.json 和逐样本测试输出。',
              '- 协议：INDEPENDENT_PROTOCOL.md 与 INDEPENDENT_PROTOCOL_V12.md。',
              '- 复验：用独立 .venv-openhands 调用 audit_independent.py --out 对应目录；轨迹诊断调用 analyze_independent.py。已有任务报告不覆写。',
              '- 所有运行源码哈希、编辑边界、测试不修改代码、出站工具结构和归档引用均通过检查；实际密钥扫描零匹配。',
              '- 当前环境的 SDK 价格映射缺失，美元金额未知；失败 API 未返回 usage 的消耗未知。当前仅选定测试模块，不声称完整上游通过。',
              '- 项目源：[Click 8.2.1](https://github.com/pallets/click/tree/8.2.1)、[packaging 25.0](https://github.com/pypa/packaging/tree/25.0)。提交号与许可证在任务定义及源目录保留。']
    target = ROOT / 'integrations/openhands/INDEPENDENT_VALIDATION_REVIEW.md'
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    snippets = {
        ROOT / 'README.md': '\n## 19. 独立项目长任务验证（v11/v12）\n\n' + conclusion + '\n\n两轮共 36 个真实样本，插件冻结 v5，全部失败保留。质量、预算中断与正常完成分别审计。详见 `integrations/openhands/INDEPENDENT_VALIDATION_REVIEW.md`。\n',
        ROOT / 'runs/HANDOFF_NEXT_CHAT.md': '\n## 11. 独立项目验证完成\n\n' + conclusion + '\n\nClick/packaging 两任务，两轮各 18 样本。v11 原始只读阶段，v12 修正为读取实现同阶段，仍冻结插件 v5；两轮分开统计。最终证据在 `runs/stage5-openhands/independent-two-projects-3x3-v11` 和 `v12` 对应目录，结论见 `integrations/openhands/INDEPENDENT_VALIDATION_REVIEW.md`。下一步修复任务文件合同保护、按符号选择可恢复代码证据、测试工具与完成状态。禁止覆写失败样本，禁止把第二轮称作首次盲测。没有请求仍在运行。\n',
    }
    for path, snippet in snippets.items():
        existing = path.read_text(encoding='utf-8')
        heading = snippet.strip().splitlines()[0]
        assert heading not in existing, f'Review already published: {path}'
        path.write_text(existing.rstrip() + '\n' + snippet, encoding='utf-8', newline='\n')
    print(str(target))

if __name__ == '__main__':
    main()
