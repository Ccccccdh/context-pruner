"""Publish v6 repair evidence and audited funded rerun, preserving older reports."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def main():
    out = ROOT / 'runs/stage5-openhands/independent-two-projects-3x3-v14'
    audit = read(out / 'audit.json')
    traces = read(out / 'trace-diagnostics.json')['samples']
    interrupted = read(ROOT / 'runs/stage5-openhands/independent-two-projects-3x3-v13/audit.json')
    assert audit['complete'] and len(audit['samples']) == 18
    reports = [read(p) for p in (ROOT / 'runs/stage5-openhands/independent-two-projects-3x3-v13').glob('r*/report.json')]
    billing = sum('insufficient balance' in r.get('diagnostic', '').lower() for r in reports)
    baseline, plugin = audit['arms']['none'], audit['arms']['pruner_v6']
    metrics = audit['paired']['pruner_v6']['metrics']
    quality_ok = plugin['artifact_successes'] >= baseline['artifact_successes'] and plugin['successes'] >= baseline['successes']
    quality_mean = metrics['both_success_api_ok']['mean']
    if quality_ok and quality_mean is not None and quality_mean >= .3:
        verdict = '修复后的本轮开发复验达到描述性的质量与平均减少 30% 输入目标；尚未构成新的独立盲测或广泛质量等价证明。'
    elif quality_ok and quality_mean is not None and quality_mean > 0:
        verdict = '本轮开发复验显示质量保持且有描述性输入减少，但没有达到平均减少 30% 的目标，也不能据此证明广泛稳定性。'
    else:
        verdict = '本轮开发复验仍未确认质量保持且平均减少 30% 输入；需要继续根据失败原因修复，不能宣传达标。'
    fmt = lambda value: '不可用' if value is None else f'{value:.2%}'
    lines = ['# v6 修复与三组开发复验', '', verdict, '',
             '## 实际改动', '',
             '- 保留观察到的 TASK.md 完整合同，同时三组初始用户消息均提供完整合同。',
             '- 按任务相关函数和模块片段选取已观察源码；成功修改替换旧文件版本，重复读取合并。未选代码保留路径、版本、范围及原 SDK 来源，可通过原编辑器重新读取。',
             '- v6 采用适配器内的有界源码证据选择，替代 v5 中间件摘要路径；不新增摘要模型调用。它尚未实现跨进程归档恢复或所有生命周期接口。',
             '- 使用 SDK 计数校核压缩结果。必保上下文过大时保留原视图并记录不可安全压缩，不能强行截断任务或最新工具批次。',
             '- 三组均接入固定公开回归测试工具：最多三次代码改变后的测试；同代码缓存结果；测试进程不继承模型凭据，没有任意命令入口。测试结果携带代码版本哈希，后续编辑会使先前通过结果失效。',
             '- SDK 每阶段 200 步和 Agent 总计 36 次请求分开；摘要最多 16 次，输出 3,072、单次输入估计 80,000、总计 2,000,000。所有组统一。正常结束与代码通过分别记录。',
             '- v5、v11/v12 原数据及结果完整保留。本轮与旧轮的工具、初始提示和预算不同，跨轮改善不能全部归因于 v6。', '',
             '## 本地检查与余额故障', '',
             'SDK 相关 15 项测试通过，包括完整合同、精确预算、不可删内容超限处理。六条 v12 固定轨迹回放接受 61 个压缩检查点，均未超硬界且完整保留已观察合同；零 API 请求，不代表真实任务质量或总输入收益。固定公开测试工具通过 110 项回归，未变化代码缓存检查通过。',
             f'v13 共保留 18 个尝试，其中 {billing} 个受 Insufficient Balance 中断。不能拿其不完整用量估算方法收益。用户充值后在新目录完整重跑 v14；仅增加余额不足立即停止后续请求的处理，v6、任务与测试参数未变。', '',
             '## v14 真实结果', '',
             '| 方法 | 正常完成 | 最终代码通过 | 实际输入（含摘要） | Agent/摘要请求 | 压缩事件 | API 失败请求 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for arm, row in audit['arms'].items():
        lines.append(f"| {arm} | {row['successes']}/6 | {row['artifact_successes']}/6 | {row['total_input_tokens']:,} | {row['model_calls'] - row['summary_calls']}/{row['summary_calls']} | {row['condensation_events']} | {row['failed_calls']} |")
    lines += ['', '| 方法 | 全配对均值 | API 正常均值 | 双方代码通过且 API 正常均值 |', '|---|---:|---:|---:|']
    for method, values in audit['paired'].items():
        m = values['metrics']
        lines.append('| ' + method + ' | ' + ' | '.join(f"{fmt(m[k]['mean'])} (n={m[k]['n']}, 输入减少配对={m[k]['wins']})" for k in ('all','api_ok','both_success_api_ok')) + ' |')
    selected = [t for t in traces if t['arm'] == 'pruner_v6']
    states = [read(p) for p in out.glob('r*-pruner_v6/pruner-state.json')]
    skips = [a for s in states for a in s['audit'] if 'skipped' in a]
    lines += ['', '## 轨迹与预算', '',
              f"插件已接受压缩 {sum(t['pruner_accepted_compressions'] for t in selected)} 次，接受后的硬界超限 {sum(t['pruner_hard_misses'] for t in selected)} 次；无法安全压缩的跳过 {len(skips)} 次（具体理由见各样本 state）。",
              '相同路径/范围重复读取未扣除修改后的合理重读；不能单独当作失败因果。', '',
              '| 方法 | 读取 | 相同范围重复 | 确认修改 | Finish |', '|---|---:|---:|---:|---:|']
    for arm in audit['arms']:
        rows = [t for t in traces if t['arm'] == arm]
        lines.append('| ' + arm + ' | ' + ' | '.join(str(sum(r[k] for r in rows)) for k in ('view_actions','repeated_identical_views','confirmed_mutations','finish_actions')) + ' |')
    lines += ['', '## 结论范围与下一步', '', verdict,
              '这是两个已接触过的自编功能任务、每任务三次的开发复验。不能把参数调整后的结果称作第一次独立盲测，不能证明普遍质量等价。应根据本轮结果冻结下一版，再用未接触过的新任务验证；不以反复重跑既有任务中的正数取代独立证据。',
              '保留 API 失败、预算中断和最终代码产物；失败请求未返回 usage 的消耗未知，金额映射缺失，美元成本未知。只有选定的上游测试模块，不声称完整上游测试通过。', '',
              '## 证据', '',
              '- v6 实现：context_pruner/adapters/openhands_v6.py。',
              '- 协议：INDEPENDENT_PROTOCOL_V13.md / INDEPENDENT_PROTOCOL_V14.md；固定测试工具 verification_tool.py。',
              '- 机械回放：runs/stage5-openhands/independent-replay-v6/replay.json。',
              '- 余额故障：runs/stage5-openhands/independent-two-projects-3x3-v13；有效重跑：对应 v14 目录。',
              '- 请求 ledger、源哈希 manifest、SDK 原事件、最终工作目录、独立 audit.json、逐样本外部测试及轨迹诊断完整保留。',
              '- 所有源哈希、编辑边界、工具请求结构及归档引用核查通过；凭据扫描零匹配。']
    target = ROOT / 'integrations/openhands/V6_REPAIR_REVIEW.md'
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    snippets = {
        ROOT / 'README.md': '\n## 20. v6 修复与充值后的三组复验\n\n' + verdict + '\n\n详见 `integrations/openhands/V6_REPAIR_REVIEW.md`。v13 余额故障样本保留，不计作有效确认；v14 为新目录完整重跑。\n',
        ROOT / 'runs/HANDOFF_NEXT_CHAT.md': '\n## 12. v6 后续修复与复验\n\n' + verdict + '\n\nv6 新增完整任务合同保护、有界函数证据选择与显式重新读取路径；三组均接入固定公开回归工具，SDK 步骤与请求上限分开。15 项本地 SDK 测试、6 条旧轨迹 61 检查点回放通过。v13 因余额不足受干扰，用户充值后 v14 完整新目录重跑并审计；不得覆写旧故障。源码与结论见 `integrations/openhands/V6_REPAIR_REVIEW.md`，数据在对应 v13/v14 目录。下一步依结果冻结版本并采用未接触的新任务做独立验证，不把既有两任务开发复验冒充盲测。v14 模型请求已停止；后续修复试验须使用独立新目录。\n',
    }
    for path, text in snippets.items():
        old = path.read_text(encoding='utf-8')
        assert text.strip().splitlines()[0] not in old
        path.write_text(old.rstrip() + '\n' + text, encoding='utf-8', newline='\n')
    print(str(target))

if __name__ == '__main__':
    main()
