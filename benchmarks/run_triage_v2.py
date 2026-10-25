"""Fresh-case triage comparison. Reserved repair trials never enter this evaluator."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from decilist import Client
from decilist import triage, triage_assistant
from .run_triage import baseline_route, canonical_hash
from . import triage_v2_lab as lab

ROOT=Path(__file__).resolve().parents[1]
CANDIDATE=Path(__file__).with_name('triage_v2_assistant_freeze.json')
SOURCE_FILES=[
    'packages/python/src/decilist/triage_assistant.py',
    'packages/python/src/decilist/triage_inspect.py',
    'packages/python/src/decilist/triage_context.py',
    'packages/python/src/decilist/triage_evidence.py',
    'packages/python/src/decilist/triage.py',
    'benchmarks/run_triage_v2.py',
]
VARIANTS=('patterns_v1','model_v1','rules_v2','model_v2','auto_v2')


def candidate_snapshot():
    return {'assistant':triage_assistant.specification(),
            'files_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES},
            'case_freeze_sha256':hashlib.sha256(lab.FREEZE.read_bytes()).hexdigest()}


def freeze_candidate():
    snapshot=candidate_snapshot()
    snapshot.update(frozen_at=datetime.now(timezone.utc).isoformat(),
        selection_scope='Previously observed v1 cases and new v2 development cases only. Evaluation cases and reserved repair trials not used for selecting the assistant.',
        unchanged_threshold=.7,variants=list(VARIANTS))
    with CANDIDATE.open('x',encoding='utf-8',newline='\n') as f:json.dump(snapshot,f,indent=2)
    return snapshot


def verify_candidate():
    saved=json.loads(CANDIDATE.read_text(encoding='utf-8'))
    current=candidate_snapshot()
    for key in current:
        if saved[key]!=current[key]:raise ValueError('Frozen assistant changed: '+key)
    return saved


def targets_present(report, case):
    refs=report.get('facts',{}).get('source_references',report.get('evidence',{}).get('references',[]))
    targets=case.get('inspection_targets',[])
    return [{'path':target['path'],'path_present':any(row['path']==target['path'] for row in refs),
             'symbol_present':any(row['path']==target['path'] and any(context.get('symbol') in target['symbols'] for context in row.get('implementation_context',[])) for row in refs)}
            for target in targets]


def summarize(rows):
    first=[row for row in rows if row['repetition']==1]
    regressions=[row for row in first if row['role']=='regression']
    accepted=[row for row in regressions if row['status']=='suggested' and row['route']!='manual_review']
    correct=[row for row in accepted if row['route'] in row['acceptable_routes']]
    durations=[row['elapsed_ms'] for row in rows if row['role']=='regression']
    return {'unique_regressions':len(regressions),'accepted':len(accepted),'correct_accepted':len(correct),
        'coverage':len(accepted)/len(regressions) if regressions else None,
        'accuracy_accepted':len(correct)/len(accepted) if accepted else None,
        'wrong_accepted':[{'case_id':row['case_id'],'route':row['route'],'acceptable_routes':row['acceptable_routes']} for row in accepted if row not in correct],
        'median_assistant_ms':median(durations) if durations else None,
        'cases_with_target_path':sum(any(t['path_present'] for t in row['inspection_targets']) for row in regressions),
        'cases_with_target_symbol':sum(any(t['symbol_present'] for t in row['inspection_targets']) for row in regressions),
        'controls':[{'case_id':row['case_id'],'status':row['status'],'route':row['route'],'specific_suggestion':row['status']=='suggested' and row['route']!='manual_review'} for row in first if row['role']!='regression'],
        'changed_outcome_cases':[case for case in sorted({row['case_id'] for row in rows}) if len({(row['status'],row['route']) for row in rows if row['case_id']==case})>1],
        'human_time_saved':None}


async def run(args):
    lab.verify_freeze()
    if args.split=='evaluation':verify_candidate()
    if not 1<=args.repetitions<=10:raise ValueError('Use 1–10 timing repetitions; they are not independent cases')
    output=Path(args.output)
    if output.exists():raise ValueError('Output exists; preserve prior results')
    proof_path=Path(args.proof)
    proof=json.loads(proof_path.read_text(encoding='utf-8'))
    catalog=lab.load_catalog()
    cases=[case for case in catalog['cases'] if case['split']==args.split and case['role']!='trial_regression']
    if not cases:raise ValueError('No cases for requested split')
    proof_rows={row['case_id']:row for row in proof['cases']}
    rows=[]
    async with Client(args.url) as client:
        for variant in args.variants:
            for repetition in range(1,args.repetitions+1):
                for case in cases:
                    fixture=proof_rows[case['id']]
                    if not fixture['verified']:raise ValueError('Unverified case')
                    observed=fixture['mutation']
                    if hashlib.sha256(observed['stdout'].encode()).hexdigest()!=observed['run_sha256']:raise ValueError('Observed log hash mismatch')
                    project=proof_path.parent/case['id']
                    evidence=lab.prepare_machine_evidence(project,observed)
                    tick=perf_counter()
                    if variant=='patterns_v1':
                        route=baseline_route(evidence)
                        report={'status':'needs_review' if route=='manual_review' else 'suggested','route':route,'source':'rules','evidence':evidence}
                    elif variant=='model_v1':
                        report=await triage.diagnose(evidence,client)
                    else:
                        report=await triage_assistant.diagnose(evidence,client,project=project,mode={'rules_v2':'rules','model_v2':'model','auto_v2':'auto'}[variant])
                    elapsed=(perf_counter()-tick)*1000
                    rows.append({'case_id':case['id'],'split':case['split'],'project':case['project'],
                        'role':case['role'],'provenance':case['provenance'],'variant':variant,'repetition':repetition,
                        'acceptable_routes':case['acceptable_routes'],'input_evidence_sha256':canonical_hash(evidence),
                        'model_state_sha256':hashlib.sha256(report.get('model_state',json.dumps(evidence,ensure_ascii=False)).encode()).hexdigest(),
                        'status':report['status'],'route':report['route'],'elapsed_ms':elapsed,
                        'inspection_targets':targets_present(report,case),'report':report})
            print(json.dumps({'variant':variant,'summary':summarize([row for row in rows if row['variant']==variant])}),flush=True)
    result={'suite':'decilist-triage-v2','split':args.split,'measured_at':datetime.now(timezone.utc).isoformat(),
        'candidate':candidate_snapshot(),'case_freeze':json.loads(lab.FREEZE.read_text()),
        'proof_sha256':hashlib.sha256(proof_path.read_bytes()).hexdigest(),'repetitions':args.repetitions,
        'summaries':{variant:summarize([row for row in rows if row['variant']==variant]) for variant in args.variants},'rows':rows,
        'limitations':['Authored/seeded fixtures in two real projects; no broad developer population claim.',
            'Source-target presence measures provided context, not correct root-cause explanation or repair.',
            'V2 model input includes statically referenced implementation; same prepared failure evidence is shared before enrichment.',
            'Assistant time includes enrichment/model/policy work, not human debugging. Repetitions are not new cases.',
            'Reserved repair-trial cases are excluded from all machine results. No human time savings measured.']}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,indent=2,ensure_ascii=False)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-candidate',action='store_true')
    parser.add_argument('--proof')
    parser.add_argument('--split',choices=('dev','evaluation'),default='dev')
    parser.add_argument('--variants',nargs='+',choices=VARIANTS,default=list(VARIANTS))
    parser.add_argument('--repetitions',type=int,default=1)
    parser.add_argument('--url',default='http://127.0.0.1:8765')
    parser.add_argument('--output',default='reports/local/triage-v2.json')
    args=parser.parse_args()
    if args.freeze_candidate:
        print(json.dumps(freeze_candidate(),indent=2));return
    if not args.proof:parser.error('--proof is required for measured evaluation')
    asyncio.run(run(args))


if __name__=='__main__':main()
