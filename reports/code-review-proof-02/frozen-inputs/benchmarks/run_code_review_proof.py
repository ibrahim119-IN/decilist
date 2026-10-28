"""Fresh historical comparison of ordinary, proof-first and proof+d1 review.

No human productivity measurement. Freeze before any corpus model inference.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import statistics
import time

import httpx

from decilist.review_repair import ReviewRepairEngine, RepairPolicy, ExecutionResult, DefectHypothesis, ReproductionSpec
from decilist.review_repair_local import LocalReviewProvider, NodeReproductionHost

HERE=Path(__file__).resolve().parent
REPO=HERE.parent
ARMS=('ordinary','proof','proof_d1')
LIMIT=180


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


def sources(folder):
    # Pinned snapshots contain only these original text files, never node_modules.
    return {p.relative_to(folder).as_posix():p.read_bytes().decode('utf-8') for p in sorted(folder.rglob('*')) if p.is_file()}


async def protected_probe(case_id,workspace,mode):
    started=time.perf_counter()
    process=await asyncio.create_subprocess_exec('node','--require',str(HERE/'code_review_no_external.cjs'),str(HERE/'code_review_proof_probe.mjs'),case_id,str(workspace),mode,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,cwd=workspace)
    try:
        async with asyncio.timeout(15): stdout,stderr=await process.communicate()
    except (TimeoutError,asyncio.CancelledError):
        if process.returncode is None: process.kill()
        await process.communicate()
        if asyncio.current_task().cancelling(): raise
        return {'passed':False,'timeout':True,'seconds':time.perf_counter()-started}
    output=(stdout+stderr).decode('utf-8',errors='replace')
    try: observed=json.loads(output.strip().splitlines()[-1])
    except (ValueError,IndexError): observed={}
    return {'passed':process.returncode==0 and observed.get('status')=='passed','returncode':process.returncode,'observed':observed,'output':output[-5000:],'seconds':time.perf_counter()-started}


class StudyHost(NodeReproductionHost):
    def __init__(self,case_id,entry_paths,evidence_dir):
        super().__init__(entry_paths=entry_paths,timeout_s=5)
        self.case_id=case_id;self.evidence_dir=evidence_dir;self.executions=[]

    async def regress(self,workspace):
        outcome=await protected_probe(self.case_id,workspace,'regression')
        self.executions.append({'stage':'regression','workspace':str(workspace),**outcome})
        dump(self.evidence_dir/'executions.json',self.executions)
        observed=outcome.get('observed',{})
        status='pass' if outcome['passed'] else 'timeout' if outcome.get('timeout') else 'assertion_failure' if observed.get('name')=='AssertionError' else 'error'
        return ExecutionResult(outcome=status,assertions=observed.get('checks',0)+(status=='assertion_failure'),evidence=outcome.get('output','probe timeout')[-4000:])

    async def reproduce(self,workspace,spec):
        started=time.perf_counter()
        result=await super().reproduce(workspace,spec)
        self.executions.append({'stage':'reproduction','workspace':str(workspace),'seconds':time.perf_counter()-started,'result':result.model_dump(mode='json')})
        dump(self.evidence_dir/'executions.json',self.executions)
        return result


class OrdinaryReviewEngine(ReviewRepairEngine):
    """Same isolated edits/checks, without requiring reproduction failure first.

    This is an ablation with a test-capable local reviewer, not a human baseline.
    The participant receives honest not-run evidence, never fabricated proof.
    """
    async def _attempt(self,snapshot,hypothesis,attempt,scratch_root,allowed_paths):
        baseline=self._workspace(snapshot,hypothesis,scratch_root,'baseline')
        checks=await self._execute('baseline_regression',baseline,snapshot.sources,hypothesis,attempt)
        if checks.outcome!='pass' or checks.assertions<1:
            attempt.status,attempt.reason='infrastructure_failure','baseline_checks_not_passing';return None
        not_run=ExecutionResult(outcome='error',assertions=0,evidence='Ordinary-review arm: the proposed reproduction has not been executed before editing. Do not treat it as proven.')
        began=time.perf_counter()
        edits=await self._provider_call(self.provider.repair(snapshot,hypothesis,not_run))
        attempt.timings_ms['repair']=(time.perf_counter()-began)*1000
        expected,edits=self._apply(snapshot,hypothesis,edits,allowed_paths);attempt.edits=edits
        workspace=self._workspace(snapshot,hypothesis,scratch_root,'candidate')
        for path in {edit.path for edit in edits}: (workspace/path).write_bytes(expected[path].encode('utf-8'))
        proof=await self._execute('candidate_reproduction',workspace,expected,hypothesis,attempt)
        if proof.outcome!='pass' or proof.assertions<1:
            attempt.status,attempt.reason='unproven','candidate_reproduction_failed';return None
        checks=await self._execute('candidate_regression',workspace,expected,hypothesis,attempt)
        if checks.outcome!='pass' or checks.assertions<1:
            attempt.status,attempt.reason='regression','candidate_regression_failed';return None
        attempt.status,attempt.reason='accepted_candidate','proposed_reproduction_and_existing_checks_passed'
        return workspace


def frozen_files(root):
    paths=[HERE/'run_code_review_proof.py',HERE/'prepare_code_review_proof.py',HERE/'code_review_proof_catalog.json',HERE/'code_review_proof_probe.mjs',HERE/'code_review_no_external.cjs',HERE/'code-review-lint/proof.mjs',HERE/'code-review-lint/package-lock.json',REPO/'docs/code-review-proof-protocol.md']
    paths+=list((REPO/'packages/python/src/decilist').rglob('*.py'))
    paths+=[REPO/'packages/python/src/decilist/data/review_proof_runner.mjs']
    result={p.relative_to(REPO).as_posix():sha(p) for p in sorted(paths)}
    result.update({'artifact/'+p:sha(root/p) for p in ('corpus/sources.json','corpus/preflight.json','primary-fixture-verification.json','lint.json','runtime-hashes.json','neutral-preflight.json')})
    return result


async def neutral_preflight(root):
    from decilist.review_repair import _snapshot
    folder=root/'neutral';folder.mkdir(parents=True,exist_ok=True)
    provider=LocalReviewProvider(source_paths=['index.js'],public_docs='add(a,b) returns the sum of two finite numbers.',evidence_dir=folder/'model')
    neutral={'index.js':'export function add(a, b) { return a + b; }\n','package.json':'{"type":"module"}\n'}
    proposals=await provider.hypothesize(_snapshot(neutral),'Review numeric addition for concrete correctness errors only.')
    for path,code in neutral.items(): (folder/path).write_text(code,encoding='utf-8')
    host=NodeReproductionHost(entry_paths=['index.js'],timeout_s=5)
    observation=await host.reproduce(folder,ReproductionSpec(code="const {add} = await load('index.js'); assert.equal(add(2, 3), 5);"))
    if observation.outcome!='pass' or observation.assertions<1: raise RuntimeError('neutral host test failed')
    async with httpx.AsyncClient(trust_env=False,follow_redirects=False) as client:
        runtime={endpoint:(await client.get('http://127.0.0.1:8792/'+endpoint)).json() for endpoint in ('health','v1/models','props')}
        response=await client.post('http://127.0.0.1:8766/v1/code-review',json={'code':'export const flag = true;','language':'JavaScript','task':'Export a boolean flag.'},timeout=60)
        response.raise_for_status();specialist=response.json()
        if specialist['status']=='unavailable': raise RuntimeError('d1 unavailable')
    dump(root/'neutral-preflight.json',{'utc':datetime.now(timezone.utc).isoformat(),'hypotheses':[h.model_dump(mode='json') for h in proposals],'host':observation.model_dump(mode='json'),'runtime':runtime,'specialist':specialist})


def freeze(root,catalog):
    if (root/'freeze.json').exists() or (root/'trials').exists(): raise RuntimeError('freeze is write once')
    preflight=read(root/'corpus/preflight.json')
    if not preflight['all_expected'] or len(preflight['results'])!=24: raise RuntimeError('incomplete source preflight')
    for case in read(root/'corpus/sources.json')['cases']:
        base=root/'corpus'/case['id']
        if sha(base/'package-lock.json')!=case['dependency_lock_sha256']:raise RuntimeError('dependency lock changed')
        for path,digest in case['installed_dependency_files'].items():
            if sha(base/path)!=digest:raise RuntimeError('installed dependency changed')
    for case in catalog:
        for variant in ('broken','fixed'):
            actual={path:hashlib.sha256(code.encode('utf-8')).hexdigest() for path,code in sources(root/'corpus'/case['id']/variant).items()}
            if actual!=case['variants'][variant]['files']: raise RuntimeError('source bytes differ')
    hashes=read(root/'runtime-hashes.json')
    if hashes!={'runtime':'049243ee4986362abecbe70b1a2ac1457d01bc4dc6178f3a3a46d9d199f57022','model':'f6050b4da22869aa3fb9d94bb3314e9fc5a027d4741c1f97761c387d6b2b2335'}:raise RuntimeError('runtime mismatch')
    schedule=[]
    for index,case in enumerate(catalog):
        order=list(ARMS[index%3:]+ARMS[:index%3])
        for variant in ('broken','fixed'):
            for arm in (order if variant=='broken' else list(reversed(order))): schedule.append({'id':case['id'],'variant':variant,'arm':arm})
    dump(root/'freeze.json',{'utc':datetime.now(timezone.utc).isoformat(),'hashes':frozen_files(root),'schedule':schedule,'task_cap_seconds':LIMIT,'max_hypotheses':2,'max_edits':2,'max_changed_lines':30,'human_time_measured':False})
    print('Frozen 36 assigned trials.',flush=True)


async def trial(root,case,assignment,lint):
    name='-'.join(assignment[k] for k in ('id','variant','arm')); evidence=root/'trials'/name
    if evidence.exists():
        if (evidence/'result.json').exists():return read(evidence/'result.json')
        raise RuntimeError('interrupted trial retained; no silent rerun')
    evidence.mkdir(parents=True)
    folder=root/'corpus'/case['id']/assignment['variant'];original=sources(folder)
    allowed={x['path'] for x in case['source_files']}
    row={**assignment,'utc':datetime.now(timezone.utc).isoformat(),'specialist':None,'verified_target_repair':False}
    started=time.perf_counter()
    try:
        async with asyncio.timeout(LIMIT):
            hints={'lint':lint}
            if assignment['arm']=='proof_d1':
                hint_start=time.perf_counter();hint_rows=[]
                async with httpx.AsyncClient(trust_env=False,follow_redirects=False) as client:
                    for path in sorted(allowed):
                        response=await client.post('http://127.0.0.1:8766/v1/code-review',json={'code':original[path],'language':'JavaScript','task':case['purpose']},timeout=35)
                        response.raise_for_status();hint_rows.append({'path':path,'review':response.json()})
                row['specialist']={'wall_seconds':time.perf_counter()-hint_start,'rows':hint_rows};hints['advisory_classifier']=hint_rows
            provider=LocalReviewProvider(source_paths=sorted(allowed),public_docs=case['public_docs'],lint_hints=json.dumps(hints),evidence_dir=evidence/'model',timeout_s=90)
            host=StudyHost(case['id'],case['entry_points'],evidence)
            policy=RepairPolicy(max_hypotheses=2,timeout_ms=180000,provider_timeout_ms=90000,host_timeout_ms=15000)
            engine_type=OrdinaryReviewEngine if assignment['arm']=='ordinary' else ReviewRepairEngine
            engine=engine_type(provider,host,policy)
            result=await engine.run(original,task=case['purpose'],allowed_paths=allowed,scratch_root=root/'corpus'/case['id']/'trial-work'/name)
            row['result']=result.model_dump(mode='json')
            if result.candidate_workspace:
                candidate=Path(result.candidate_workspace)
                oracle=await protected_probe(case['id'],candidate,'target')
                row['hidden_target_verification']=oracle
                row['verified_target_repair']=assignment['variant']=='broken' and oracle['passed']
                row['diff']=''.join(''.join(difflib.unified_diff(original[path].splitlines(True),(candidate/path).read_text(encoding='utf-8').splitlines(True),fromfile=path,tofile=path)) for path in sorted(allowed))
                row['candidate_source']={path:(candidate/path).read_text(encoding='utf-8') for path in sorted(allowed)}
    except (TimeoutError,httpx.HTTPError) as error:
        row['error']=type(error).__name__
    row['elapsed_seconds']=time.perf_counter()-started
    if row['elapsed_seconds']>LIMIT:row['verified_target_repair']=False
    row['capped_task_seconds']=row['elapsed_seconds'] if row['verified_target_repair'] else LIMIT
    if sources(folder)!=original:raise RuntimeError('original sources changed')
    dump(evidence/'result.json',row)
    print(json.dumps({**assignment,'status':row.get('result',{}).get('status',row.get('error')),'target_repaired':row['verified_target_repair'],'seconds':row['elapsed_seconds']}),flush=True)
    return row


async def run(root,catalog):
    frozen=read(root/'freeze.json')
    if frozen['hashes']!=frozen_files(root):raise RuntimeError('frozen inputs changed; operational replay required')
    lint={(r['id'],r['variant']):r['diagnostics'] for r in read(root/'lint.json')['rows']}
    cases={c['id']:c for c in catalog};rows=[]
    for assignment in frozen['schedule']:rows.append(await trial(root,cases[assignment['id']],assignment,lint[assignment['id'],assignment['variant']]))
    summary={}
    for arm in ARMS:
        chosen=[r for r in rows if r['arm']==arm and r['variant']=='broken']
        summary[arm]={'verified_repairs':sum(r['verified_target_repair'] for r in chosen),'cases':len(chosen),'capped_median_seconds':statistics.median(r['capped_task_seconds'] for r in chosen),'actual_total_seconds':sum(r['elapsed_seconds'] for r in chosen)}
    dump(root/'results.json',{'freeze_sha256':sha(root/'freeze.json'),'summary':summary,'rows':rows,'human_time_measured':False})
    print(json.dumps(summary),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    action=parser.add_mutually_exclusive_group(required=True)
    for mode in ('preflight','freeze','run'):action.add_argument('--'+mode,action='store_true')
    args=parser.parse_args();catalog=read(HERE/'code_review_proof_catalog.json')['cases']
    if args.preflight:asyncio.run(neutral_preflight(args.root))
    if args.freeze:freeze(args.root,catalog)
    if args.run:asyncio.run(run(args.root,catalog))


if __name__=='__main__':main()
