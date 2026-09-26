"""Offline trace analysis; never modifies frozen runner or original reports."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def text(content):
    return ''.join(c.get('text', '') for c in content or [] if c.get('type') == 'text')


def sample_trace(sample):
    report = load(sample / 'report.json')
    events = [load(p) for p in sorted(sample.glob('conversation/**/events/event-*.json'))]
    response_events = defaultdict(list)
    phase = 0
    response_phase = {}
    observations = {}
    condensations = []
    for e in events:
        if e['kind'] == 'MessageEvent' and e['source'] == 'user':
            phase += 1
        if e['kind'] == 'ObservationEvent':
            observations[e['action_id']] = e['observation']
        response = e.get('llm_response_id')
        if response and e['kind'] in ('ActionEvent', 'MessageEvent'):
            response_events[response].append(e)
            response_phase[response] = phase
        if e['kind'] == 'Condensation':
            summary = e.get('summary') or ''
            condensations.append({'id': e['id'], 'phase': phase, 'timestamp': e['timestamp'],
                                 'forgotten_count': len(e['forgotten_event_ids']),
                                 'summary_chars': len(summary),
                                 'archived_memory_headers': summary.count('Archived history (derived memory; original events retained):'),
                                 'contains_decode_escapes': 'decode_escapes' in summary,
                                 'contains_single_quote_escapes': '_single_quote_escapes' in summary,
                                 'contains_parse_value': 'parse_value' in summary})
    actions = []
    calls = []
    seen_views = Counter()
    coverage = defaultdict(list)
    versions = Counter()
    for number, usage in enumerate(report['agent_metrics']['token_usages'], 1):
        response = usage['response_id']
        call_actions = []
        for event in response_events[response]:
            if event['kind'] != 'ActionEvent':
                continue
            a = event.get('action') or {}
            observation = observations.get(event['id'], {})
            path = a.get('path', '').replace('\\', '/')
            marker = '/workspace/'
            path = path.split(marker, 1)[-1] if marker in path else ('.' if path.endswith('/workspace') else path)
            command = a.get('command', event['tool_name'])
            signature = (path, tuple(a.get('view_range') or []))
            repeated = False
            covered_read = False
            overlap_read = False
            if command == 'view':
                repeated = seen_views[signature] > 0
                seen_views[signature] += 1
                requested = a.get('view_range') or [1, -1]
                start, end = requested
                if end == -1:
                    end = 10**9
                prior = coverage[path]
                overlap_read = any(max(start, lo) <= min(end, hi) for lo, hi in prior)
                merged = []
                for lo, hi in sorted(prior):
                    if merged and lo <= merged[-1][1] + 1:
                        merged[-1][1] = max(merged[-1][1], hi)
                    else:
                        merged.append([lo, hi])
                covered_read = any(lo <= start and end <= hi for lo, hi in merged)
                coverage[path].append([start, end])
            elif command in ('str_replace', 'insert', 'create', 'undo_edit') and not observation.get('is_error', False):
                versions[path] += 1
                coverage[path] = []
            record = {'event_id': event['id'], 'call': number, 'phase': response_phase[response],
                      'command': command, 'path': path, 'view_range': a.get('view_range'),
                      'repeated_view': repeated, 'error': observation.get('is_error', False),
                      'file_version': versions[path], 'already_covered_read': covered_read,
                      'overlap_read': overlap_read,
                      'error_excerpt': text(observation.get('content'))[:500] if observation.get('is_error') else None,
                      'old_str': a.get('old_str'), 'new_str': a.get('new_str'),
                      'finish_message': a.get('message'), 'action_keys': sorted(a),
                      'thought_excerpt': text(event.get('thought'))[:500]}
            actions.append(record)
            call_actions.append(record)
        calls.append({'call': number, 'response_id': response, 'phase': response_phase.get(response),
                      'input': usage['prompt_tokens'], 'output': usage['completion_tokens'],
                      'cache_read': usage['cache_read_tokens'], 'actions': call_actions})
    phase_metrics = {}
    for phase in (1, 2, 3):
        selected = [c for c in calls if c['phase'] == phase]
        phase_metrics[str(phase)] = {'calls': len(selected), 'input': sum(c['input'] for c in selected),
                                    'output': sum(c['output'] for c in selected)}
    state = load(sample / 'pruner-state.json') if report['arm'] == 'pruner_v1' else None
    return {'sample': sample.name, 'task': report['task'], 'arm': report['arm'], 'repeat': report['repeat'],
            'normal_success': report['success'], 'phases': phase_metrics, 'calls': calls,
            'agent_input': sum(c['input'] for c in calls),
            'summary_input': report['summary_metrics']['accumulated_token_usage']['prompt_tokens'],
            'actions': actions, 'condensations': condensations,
            'pruner_audit': state['audit'] if state else [],
            'plugin_task_state': state['middleware']['plugin']['task_state'] if state and state['middleware'] else None,
            'repeated_views': sum(a['repeated_view'] for a in actions),
            'covered_reads': sum(a['already_covered_read'] for a in actions),
            'overlap_reads': sum(a['overlap_read'] for a in actions),
            'failed_edits': sum(a['error'] and a['command'] in ('str_replace', 'insert', 'create', 'undo_edit') for a in actions),
            'view_count': sum(a['command'] == 'view' for a in actions),
            'edit_count': sum(a['command'] in ('str_replace', 'insert', 'create', 'undo_edit') for a in actions),
            'first_evaluation': report.get('first_evaluation'), 'final_evaluation': report.get('final_evaluation')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    manifest = load(out / 'manifest.json')
    root = Path(__file__).resolve().parents[2]
    assert all(hashlib.sha256((root / path).read_bytes()).hexdigest() == value for path, value in manifest['source_hashes'].items())
    audit = load(out / 'audit.json')
    traces = [sample_trace(p.parent) for p in sorted(out.glob('r*/report.json'))]
    assert len(traces) == 27
    lookup = {(t['task'], t['repeat'], t['arm']): t for t in traces}
    comparisons = []
    for pair in audit['paired']['pruner_v1']['pairs']:
        baseline = lookup[pair['task'], pair['repeat'], 'none']
        treatment = lookup[pair['task'], pair['repeat'], 'pruner_v1']
        base_calls, method_calls = len(baseline['calls']), len(treatment['calls'])
        base_average = baseline['agent_input'] / base_calls
        method_average = treatment['agent_input'] / method_calls
        # Exact symmetric decomposition: total delta = call-count term +
        # mean-input-per-call term. Descriptive accounting, not causality.
        count_term = (method_calls - base_calls) * (method_average + base_average) / 2
        length_term = (method_average - base_average) * (method_calls + base_calls) / 2
        assert abs(count_term + length_term - (treatment['agent_input'] - baseline['agent_input'])) < 1e-7
        comparisons.append(dict(pair, baseline_calls=base_calls, pruner_calls=method_calls,
                                baseline_average_input=base_average, pruner_average_input=method_average,
                                call_count_term=count_term, per_call_input_term=length_term,
                                baseline_phases=baseline['phases'], pruner_phases=treatment['phases'],
                                pruner_compressions=len(treatment['condensations']),
                                baseline_repeat_views=baseline['repeated_views'], pruner_repeat_views=treatment['repeated_views'],
                                baseline_covered_reads=baseline['covered_reads'], pruner_covered_reads=treatment['covered_reads'],
                                baseline_failed_edits=baseline['failed_edits'], pruner_failed_edits=treatment['failed_edits']))
    result = {'experiment': manifest['version'], 'analysis_version': 1, 'samples': traces, 'paired': comparisons,
              'decomposition_note': 'Exact symmetric accounting decomposition; cannot identify causal effect from independent live trajectories.'}
    successful_compressions = [a for t in traces for a in t['pruner_audit'] if 'forgotten_ids' in a]
    result['compression'] = {
        'active_samples': sum(bool(t['condensations']) for t in traces if t['arm'] == 'pruner_v1'),
        'accepted': len(successful_compressions),
        'non_reducing_rejections': sum(a.get('skipped') == 'no_token_reduction' for t in traces for a in t['pruner_audit']),
        'empty_task_states': sum(t['plugin_task_state'] == '' for t in traces if t['arm'] == 'pruner_v1'),
        'sum_local_sdk_before': sum(a['before'] for a in successful_compressions),
        'sum_local_sdk_after': sum(a['after'] for a in successful_compressions),
        'mean_local_view_reduction': sum(1 - a['after'] / a['before'] for a in successful_compressions) / len(successful_compressions),
    }
    (out / 'trace-analysis.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    lines = ['# OpenHands 九对轨迹分析', '',
             '本分析只读取冻结 v6 的报告、SDK 事件和插件状态；没有模型调用，不改原报告和运行器。', '',
             '## 九对逐项结果', '',
             '| 任务 | 重复 | 基线调用 | 插件调用 | 插件压缩次数 | 输入节省 |', '|---|---:|---:|---:|---:|---:|']
    labels = {'quoted_roundtrip': '反斜杠', 'empty_inline_comment': '空值注释', 'crlf_error_recovery': 'CRLF'}
    for pair in comparisons:
        lines.append(f'| {labels[pair["task"]]} | {pair["repeat"]} | {pair["baseline_calls"]} | {pair["pruner_calls"]} | {pair["pruner_compressions"]} | {pair["input_savings"]:.2%} |')
    compression = result['compression']
    lines += ['', '## 1. 平均收益为何很小', '',
              '九对中五对省输入、四对增加输入。反斜杠第 3 次是最大负收益：59486 → 96537 token（增加 62.29%），调用数 6 → 10。修复阶段增加一次查看，最终检查由两次调用变为五次；额外检查在测试通过后读取解析器三个片段。', '',
              '该样本单次视图压缩 12368 → 6836（SDK 计数，减少 44.73%），但全任务输入仍增加。精确对称账面分解中，调用数变化项为 +39136 token，每调用平均输入变化项为 -2085 token，合计 +37051。分解是描述性核算，不能当成额外调用由压缩导致的因果证明。', '',
              '反斜杠第 1、2 次没有触发插件压缩，输入节省却分别为 +14.80%、-21.24%；CRLF 第 1、2 次也未触发。四个无压缩样本是组间行为波动的直接证据，不能把这些收益归于裁剪算法。', '',
              '空值注释三次分别省 15.88%、17.58%、32.98%。前两次基线/插件调用数相同，更小的平均每次输入对应账面的节省；第三次还伴随基线 14 次、插件 10 次调用及基线三次失败编辑。因此改善集中在本次较复杂、上下文确实增长的任务，但三个重复仍不是三个独立项目。', '',
              f'插件只有 {compression["active_samples"]}/9 样本触发压缩，共 {compression["accepted"]} 次。接受的单次视图平均减少 {compression["mean_local_view_reduction"]:.2%}（SDK 估算），并不等于全任务配对平均 0.87%。另有 {compression["non_reducing_rejections"]} 次变换因不能减少输入被回退，未发送膨胀后的候选视图。', '',
              '## 2. 可以由源码与状态确认的适配问题', '',
              '### 任务目标没有进入压缩评分', '',
              f'全部 {compression["empty_task_states"]} 个创建了中间件的插件样本，其导出 plugin.task_state 都为空字符串。适配器创建 ContextPrunerMiddleware 和调用 before_model 时没有传任务状态。当前用户目标虽保留在 SDK 模型视图中，但内核相关性评分不能使用同一目标。', '',
              '### 将整段 SDK JSON 当成语义内容', '',
              'prepared.content 直接 json.dumps(message.model_dump())；记忆含重复路径、tool_call ID、null 字段和嵌套转义。自然代码原来的换行被编码成 JSON 内的转义文本，现有文本分块与摘要策略无法直接得到原来的源码结构。原始 SDK 日志已经保存这些结构，派生记忆无需重复所有传输字段。', '',
              '### 压缩后的记忆再次被 JSON 包装', '',
              '第一次记忆作为 CondensationSummaryEvent 进入下一轮，又作为普通消息 JSON 参与压缩。空值注释第 1 次的记忆字符数 5296 → 18742，第 3 次为 5239 → 16705；第二份内部含上一份记忆头及多层反斜杠。字符增长不是净 token 增长的等价证明，但结合七次 no_token_reduction 回退，说明这层包装需要修正。', '',
              '### 代码证据丢失与重新读取', '',
              '反斜杠第 3 次的压缩记忆不含 decode_escapes、_single_quote_escapes、parse_value 三个符号，之后模型分别读取解析器定义、parse_value 与 decode_escapes 所在片段。原文件未改，历史上已读取完整解析器。证据支持“关键代码未进入派生记忆，随后重新取证”的解释；不能仅据顺序断言删除某符号一定造成全部额外调用。', '',
              '## 3. 失败样本的具体原因', '',
              '- 插件空值注释第 1 次：初期方案无条件清除 #，之后多次改为显式记录等号后的空白；修复后首次评分 59 项全过。第 14 次调用用于最终再查看，后续调用被上限阻止，所以文件任务通过但正常完成失败。',
              '- 基线空值注释第 1 次：两次字符串替换失败，随后还修复一次换行拼接问题；最终文件通过，检查阶段用尽调用数。第 3 次有三次失败替换且到达上限，最终四项行为仍失败。',
              '- 原生摘要空值注释第 1 次：把等号正则改为不吞空白，但 parse_value 随即又吞了空白，注释识别仍失败。模型将失败归因于导入了错误安装包；摘要保留了这一猜测。实际评分子进程明确用 workspace/src 作为 PYTHONPATH，原版负对照也已复现，因此没有证据支持该导入猜测。',
              '- 原生摘要空值注释第 3 次：最后只改等号正则，不再吞掉等号后的空白，导致普通未加引号的值保留了前导空格；上游用例 ` a = b ` 期望值 b，实际为 ` b`，最终一项回归失败。', '',
              '这些都是可追溯的代码或预算问题。原生摘要中的错误猜测来自模型轨迹，不能把它当成测试环境的真实故障，也不能据此断言所有原生摘要都不可靠。', '',
              '## 4. 实验设计中需要控制的因素', '',
              '三组分别重新调用模型，温度 0 仍不能保证产生相同动作。工具路径也暴露了组名、任务名和重复编号，长度不同；未做组名盲化。独立轨迹受提示、取证和调用数共同影响。当前配对比较是端到端结果，不能识别裁剪机制的纯因果效果。', '',
              '固定第三阶段要求再次检查，使部分已通过测试的任务继续取证。这一阶段各组均有，但与压缩后的证据完整性相互作用。要调整完成策略，应在所有三组统一调整，不只提前结束插件组。', '',
              '## 5. 建议实施顺序', '',
              '1. 先修 OpenHands 适配器的内容投影：提取工具正文和必要定位信息，将传输 ID 留在原日志；已有记忆直接作为语义记忆输入，避免再嵌套整个 JSON。',
              '2. 传入初始任务与当前用户要求，保留目标、约束、已改文件、文件版本及宿主测试状态；把已验证事实与待验证猜测分别标注。',
              '3. 为当前修复依赖的函数/代码片段增加来源与保留策略。不能只留函数名；应保留能核对行为的代码和版本，旧版本要显式标记。',
              '4. 所有组统一完成策略：测试通过后做一次有界最终确认；只有明确新风险或失败才能增加检查。记录达到上限时已有正确代码的情况。',
              '5. 先做零 API 验证：使用冻结事件回放，检查 JSON 嵌套、任务状态、关键代码、工具批次和当前测试结果；同一检查点测变换前后视图并做分组/无变换负对照。',
              '6. 零 API 门控通过后才冻结新协议：使用不暴露组名的工作区 ID，选择不同项目和更长的自然任务，小规模试运行后做三组重复。保持本轮 v6 原始证据与源文件不变，新适配器用新模块/版本保存。', '',
              '不建议为了数字直接降低触发阈值，也不建议重跑旧失败替换结果。现阶段优先修正语义投影与任务状态，然后控制完成策略，再观察端到端收益。', '',
              '## 证据', '',
              '`trace-analysis.json` 含逐调用提供商输入/输出、三阶段分解、工具动作、同版本已覆盖读取、失败编辑、摘要摘要信息与九对精确核算。原始 `report.json`、SDK 事件和 `pruner-state.json` 未修改。文件内容覆盖度只按路径、行范围和成功编辑版本判断，不能断言所有重复读取都不必要。']
    (out / 'TRACE_ANALYSIS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'paired': comparisons,
                      'sample_summary': [{k: t[k] for k in ('sample', 'repeated_views', 'failed_edits', 'edit_count', 'view_count', 'phases')}
                                         for t in traces]}, indent=2))


if __name__ == '__main__':
    main()
