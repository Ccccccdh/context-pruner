"""Offline diagnostics for natural development trajectories; no model requests."""
from collections import Counter
import argparse
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    target_tokens = manifest.get('pruner_target_tokens', 10000)
    records = []
    for rp in sorted(out.glob('r*/report.json')):
        report = json.loads(rp.read_text(encoding='utf-8'))
        events = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(rp.parent.glob('conversation/**/events/*.json'))]
        actions = [e for e in events if e['kind'] == 'ActionEvent']
        obs = {e['action_id']: e['observation'] for e in events if e['kind'] == 'ObservationEvent'}
        counts, repeated, mutations = Counter(), 0, []
        for event in actions:
            action = event.get('action', {})
            cmd = action.get('command', event.get('tool_name'))
            if cmd == 'view':
                signature = (action.get('path'), tuple(action.get('view_range') or []))
                repeated += counts[signature] > 0
                counts[signature] += 1
            if cmd in ('str_replace', 'create', 'insert', 'undo_edit'):
                mutations.append({'id': event['id'], 'path': action.get('path'),
                                  'confirmed': event['id'] in obs and not obs[event['id']].get('is_error', False)})
        condensed = [e for e in events if e['kind'] == 'Condensation']
        keywords = (['atomic', 'colliding', 'duplicate', 'hidden', 'replacement'] if report['task'] == 'click_aliases'
                    else ['InvalidVersion', 'prereleases', 'select_requirements', 'identity', 'extras'])
        state_path = rp.parent / 'pruner-state.json'
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        accepted = [a for a in state.get('audit', []) if 'forgotten_ids' in a]
        records.append({'sample': rp.parent.name, 'arm': report['arm'], 'task': report['task'],
                        'view_actions': sum(counts.values()), 'repeated_identical_views': repeated,
                        'mutation_actions': len(mutations), 'confirmed_mutations': sum(m['confirmed'] for m in mutations),
                        'finish_actions': sum(e.get('tool_name') == 'finish' for e in actions),
                        'user_messages': sum(e['kind'] == 'MessageEvent' and e.get('source') == 'user' for e in events),
                        'conversation_errors': [{'code': e.get('code'), 'detail': e.get('detail')} for e in events if e['kind'] == 'ConversationErrorEvent'],
                        'budget_failure': 'Frozen experiment call/token limit' in report.get('diagnostic', ''),
                        'pruner_accepted_compressions': len(accepted),
                        'pruner_target_misses': sum(a['after'] > target_tokens for a in accepted),
                        'pruner_hard_misses': sum(a.get('hard_budget_exceeded', False) for a in accepted),
                        'pruner_after_range': [min((a['after'] for a in accepted), default=0), max((a['after'] for a in accepted), default=0)],
                        'summary_checks': [{'event_id': e['id'], 'chars': len(e.get('summary', '')),
                                            'keyword_presence_in_summary_only': {k: k.lower() in e.get('summary', '').lower() for k in keywords}}
                                           for e in condensed],
                        'mutations': mutations})
    result = {'samples': records, 'note': 'Repeated views are exact path/range repeats; modified versions may justify rereads. Summary keyword checks alone do not prove complete outgoing-request information loss or causality.'}
    (out / 'trace-diagnostics.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    lines = ['# 独立任务轨迹诊断', '', '| 样本 | 读取 | 相同读取重复 | 确认修改 | Finish | 压缩 | 超过硬界标记 |', '|---|---:|---:|---:|---:|---:|---:|']
    for r in records:
        lines.append(f"| {r['sample']} | {r['view_actions']} | {r['repeated_identical_views']} | {r['confirmed_mutations']} | {r['finish_actions']} | {r['pruner_accepted_compressions']} | {r['pruner_hard_misses']} |")
    lines += ['', '相同路径/范围的重复读取未扣除修改后的合理重读。摘要关键词检查仅针对派生摘要，不能单独证明完整请求丢失约束或模型循环的因果关系。',
              '压缩预算保证应按本轮适配器实现解释：v5 的硬界仅用于审计；v6 对接受的压缩结果执行精确硬界检查。跳过压缩后的原始上下文可能仍超过该值。']
    (out / 'TRACE_DIAGNOSTICS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('Analyzed', len(records), 'samples')

if __name__ == '__main__':
    main()
