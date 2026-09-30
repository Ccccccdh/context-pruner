"""Check live budget notices and reserved tools in recorded runs; no API calls."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    rows = []
    for path in sorted(out.glob('r*/report.json')):
        report = json.loads(path.read_text(encoding='utf-8'))
        calls = report['ledger']['calls']
        agent_calls = [c for c in calls if c['kind'] == 'agent']
        for index, call in enumerate(agent_calls):
            budget = call['budget']
            assert budget['remaining_requests'] == manifest['max_agent_calls_per_sample']-index, path.parent.name
            assert 'HOST RUNTIME BUDGET' in call['budget_notice'], path.parent.name
            phase = budget['phase']
            if phase == 'verify':
                assert call['allowed_tools'] == ['scoped_tests']
            elif phase == 'finish':
                assert call['allowed_tools'] == ['finish']
            else:
                assert index < manifest['max_agent_calls_per_sample']-manifest['closing_request_reserve']
                if 'finish' in call['allowed_tools']:
                    assert budget['public_verification'] == 'passed_current'
            if call['status'] == 'returned':
                assert call.get('budget_response_valid') is True, path.parent.name
        if report['success']:
            assert report['workflow_verification_ok'] and report['artifact_success'] and report['sdk_status'] == 'finished'
        rows.append({'sample':path.parent.name,'arm':report['arm'],
                     'agent_calls':len(agent_calls),
                     'verify_only_requests':sum(c['budget']['phase']=='verify' for c in agent_calls),
                     'finish_only_requests':sum(c['budget']['phase']=='finish' for c in agent_calls),
                     'closing_reasons':sorted({c['budget']['reason'] for c in agent_calls if c['budget']['phase']!='work'}),
                     'workflow_verification_ok':report['workflow_verification_ok'],
                     'normal_success':report['success'],'error_type':report.get('error_type')})
    result={'samples':len(rows),'expected_samples':manifest['repeats']*len(manifest['tasks'])*len(manifest['arms']),
            'notice_and_reserve_checks_passed':True,'rows':rows}
    (out/'budget-audit.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
