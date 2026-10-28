"""Publish reproducible historical-study evidence without rerunning models."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import statistics

from run_code_review_natural import read, dump, sha, files, frozen_files, HERE


def analyze(root, output):
    frozen=read(root/'freeze.json')
    if frozen['hashes'] != frozen_files(root):
        raise RuntimeError('frozen measurement code/evidence changed')
    data=read(root/'results.json'); rows=data['rows']
    if len(rows)!=32 or len({(x['id'],x['variant'],x['arm']) for x in rows})!=32:
        raise RuntimeError('incomplete assigned trials')
    for case in read(root/'sources.json')['cases']:
        for variant in ('broken','fixed'):
            if files(root/case['id']/variant)!=case['variants'][variant]['files']:
                raise RuntimeError('original source mutated')
        if sha(root/case['id']/'package-lock.json')!=case['dependency_lock_sha256']:
            raise RuntimeError('dependency lock mutated')
    adjudication=read(HERE/'code_review_natural_adjudication.json')
    summary=dict(data['summary'])
    by_key={(r['id'],r['variant'],r['arm']):r for r in rows}
    for arm in ('ordinary','assisted'):
        counts=Counter(x['category'] for x in adjudication['controls'] if x['arm']==arm)
        summary[arm]['control_categories']=dict(counts)
        successes={r['id'] for r in rows if r['arm']==arm and r['variant']=='broken' and r['verified_repair']}
        audits=[x for x in adjudication['protected_success_audit'] if x['arm']==arm]
        if {x['id'] for x in audits} != successes:
            raise RuntimeError('every protected-test success needs a separate behavioral audit')
        summary[arm]['accepted_repairs_after_additional_checks']=sum(x['accepted'] for x in audits)
        attempts=[a for r in rows if r['arm']==arm for a in r['attempts']]
        summary[arm]['model_requests']=len(attempts)
        summary[arm]['truncated_responses']=sum(a['finish_reason']=='length' for a in attempts)
        summary[arm]['invalid_proposals']=sum('invalid_proposal' in a for a in attempts)
    paired=[]
    for i in range(1,9):
        case=f'N{i:02}'
        ordinary=by_key[case,'broken','ordinary'];assisted=by_key[case,'broken','assisted']
        paired.append({'id':case,**{arm:{k:row[k] for k in ('verified_repair','elapsed_seconds','capped_task_seconds')} for arm,row in [('ordinary',ordinary),('assisted',assisted)]}})
    summary['assisted_faster_capped_pairs']=sum(p['assisted']['capped_task_seconds']<p['ordinary']['capped_task_seconds'] for p in paired)
    summary['capped_median_reduction_fraction']=1-summary['assisted']['capped_median_seconds']/summary['ordinary']['capped_median_seconds']
    conditions={
        'no lower protected repair success':summary['assisted']['verified_repairs']>=summary['ordinary']['verified_repairs'],
        'at least 20% capped-median reduction':summary['capped_median_reduction_fraction']>=.2,
        'at least half pairs faster':summary['assisted_faster_capped_pairs']>=len(paired)/2,
        'no increase in confirmed false runtime claims':summary['assisted']['control_categories'].get('confirmed_false_runtime',0)<=summary['ordinary']['control_categories'].get('confirmed_false_runtime',0),
        'no unresolved control claims':not any(x['category']=='unresolved_contract_claim' for x in adjudication['controls']),
    }
    summary['engineering_gate_passed']=all(conditions.values())
    summary['gate_failures']=[name for name,passed in conditions.items() if not passed]
    summary['timed_protocol_changed_after_exposure']=False
    summary['human_time_measured']=False
    hints=[h for row in rows if row['arm']=='assisted' for h in row['hints']]
    targets=[]
    catalog=read(HERE/'code_review_natural_catalog.json')['cases']
    for case in catalog:
        row=by_key[case['id'],'broken','assisted']
        # Broad changed-hunk overlap is only a localization proxy, not defect confirmation.
        regions={f['path']:[(int(a),int(a)+max(1,int(n or '1'))-1) for a,n in re.findall(r'^@@ -(\d+)(?:,(\d+))? \+',f['patch'],re.M)] for f in case['source_files']}
        overlap=any(any(not (f['end_line']<start or f['start_line']>end) for start,end in regions[h['path']]) for h in row['hints'] for f in h['result'].get('findings',[]))
        targets.append({'id':case['id'],'flag_in_changed_hunk':overlap,'any_file_flag':any(h['result'].get('findings') for h in row['hints'])})
    summary['specialist']={'file_calls':len(hints),'unavailable':sum(h['result']['status']=='unavailable' for h in hints),'broken_cases_with_any_flag':sum(t['any_file_flag'] for t in targets),'broken_cases_with_changed_hunk_flag':sum(t['flag_in_changed_hunk'] for t in targets),'corrected_cases_flagged':sum(any(h['result'].get('findings') for h in row['hints']) for row in rows if row['arm']=='assisted' and row['variant']=='fixed'),'median_task_hint_seconds':statistics.median(sum(h['wall_seconds'] for h in row['hints']) for row in rows if row['arm']=='assisted'),'window_false_positive_rate':None}
    lint=read(root/'lint.json')
    summary['lint']={'profile':lint['profile'],'target_defects_detected':0,'all_diagnostics':sum(len(x['diagnostics']) for x in lint['rows']),'median_all_variants_seconds':statistics.median(x['latency_ms']/1000 for x in lint['rows']),'first_call_seconds':lint['rows'][0]['latency_ms']/1000,'reason':'Only unchanged no-empty / no-unused-vars messages; none identifies the historical defects.'}
    checks=read(root/'control-adjudication-checks.json')
    for audit in adjudication['protected_success_audit']:
        evidence=next(x for x in checks if x['id']==audit['id'] and x['candidate']=='broken-repair-'+audit['arm'])
        if evidence['passed'] or audit['accepted']:
            raise RuntimeError('repair audit contradicts classification')
    output.mkdir(parents=True,exist_ok=True)
    for name in ('freeze.json','sources.json','lint.json','fixture-proof-initial.json','fixture-proof.json','preflight.json','runtime-hashes.json','results.json','control-adjudication-checks.json'):
        shutil.copyfile(root/name,output/name)
    shutil.copytree(root/'trials',output/'trials',dirs_exist_ok=True)
    for case in catalog:
        dest=output/'dependency-locks'/case['id']; dest.mkdir(parents=True,exist_ok=True)
        for name in ('package.json','package-lock.json'):
            shutil.copyfile(root/case['id']/name,dest/name)
    dump(output/'analysis.json',{'summary':summary,'pairs':paired,'localization':targets,'adjudication':adjudication})
    manifest={p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*')) if p.is_file() and p.name!='evidence-hashes.json'}
    dump(output/'evidence-hashes.json',manifest)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();analyze(args.root,args.output)
