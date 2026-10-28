"""Explicit local investigation of a small existing JS package; original is never edited."""
from __future__ import annotations
import argparse
import asyncio
import difflib
import json
import os
from pathlib import Path
import tempfile

from .review_repair import ReviewRepairEngine, RepairPolicy
from .review_repair_local import LocalReviewProvider, NodeReproductionHost


def collect_project(project: Path) -> dict[str,str]:
    """Copy bounded text sources/tests without secrets, dependencies or prior runs."""
    project = project.resolve()
    ignored={'.git','.decilist-review','node_modules','.venv','venv','dist','build','coverage','__pycache__'}
    accepted={'.js','.mjs','.cjs','.json','.md','.txt'}
    result={};total=0
    for folder,dirs,names in os.walk(project,followlinks=False):
        dirs[:]=[name for name in dirs if name not in ignored and not name.startswith('.')
                 and not (Path(folder)/name).is_symlink()
                 and (Path(folder)/name).resolve().is_relative_to(project)]
        for name in sorted(names):
            path=Path(folder)/name
            if (name.startswith('.') or path.is_symlink() or not path.resolve().is_relative_to(project)
                    or path.suffix not in accepted or name in {'package-lock.json','npm-shrinkwrap.json'}):continue
            data=path.read_bytes();total+=len(data)
            if len(data)>512000 or total>2000000 or len(result)>=256:raise ValueError('package exceeds bounded review size')
            result[path.relative_to(project).as_posix()]=data.decode('utf-8')
    return result


def _relative_file(project: Path,path: str) -> str:
    target=(project/path).resolve()
    if not target.is_file() or not target.is_relative_to(project) or (project/path).is_symlink():raise ValueError('path must be an existing local project file')
    return target.relative_to(project).as_posix()


def main(argv=None):
    parser=argparse.ArgumentParser(prog='decilist review investigate',description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--source',nargs='+',required=True,help='Existing JS source files to investigate')
    parser.add_argument('--entry',nargs='+',help='Importable public entrypoints; defaults to source files')
    parser.add_argument('--regression',nargs='+',required=True,help='Existing Node test files; must pass before investigation')
    parser.add_argument('--task',required=True,help='Intended public behavior')
    parser.add_argument('--contract',help='Existing public API documentation file within the project')
    parser.add_argument('--model-url',default='http://127.0.0.1:8792/v1/chat/completions')
    parser.add_argument('--model',default='qwen3-coder-30b-a3b-q4km')
    parser.add_argument('--no-thinking',action='store_true',help='Use llama.cpp structured-output compatibility for thinking models')
    parser.add_argument('--allow-execution',action='store_true',help='Execute the trusted project and generated probes in disposable local copies (not an OS sandbox)')
    args=parser.parse_args(argv)
    if not args.allow_execution:parser.error('investigation executes code; pass --allow-execution for a trusted project')
    project=args.project.resolve()
    if not project.is_dir():parser.error('project must be a directory')
    source_paths=[_relative_file(project,p) for p in args.source]
    entries=[_relative_file(project,p) for p in args.entry or args.source]
    tests=[_relative_file(project,p) for p in args.regression]
    if any(Path(p).suffix not in {'.js','.mjs','.cjs'} for p in source_paths+entries+tests):parser.error('executable investigation currently supports native Node JavaScript files')
    original=collect_project(project)
    if any(p not in original for p in source_paths+entries+tests):parser.error('selected files must be included in the bounded text snapshot')
    contract_path=_relative_file(project,args.contract) if args.contract else None
    if contract_path and contract_path not in original:parser.error('contract must be a collected text file')
    docs=original[contract_path] if contract_path else args.task
    if len(docs)>16000:parser.error('select a smaller API contract document (maximum 16000 characters)')
    scratch=project/'.decilist-review'
    if scratch.is_symlink() or not scratch.resolve().is_relative_to(project):parser.error('review output must stay within the project')
    scratch.mkdir(exist_ok=True)
    run=Path(tempfile.mkdtemp(prefix='investigate-',dir=scratch))
    provider=LocalReviewProvider(source_paths=source_paths,public_docs=docs,endpoint=args.model_url,model=args.model,evidence_dir=run/'model',disable_thinking=args.no_thinking)
    host=NodeReproductionHost(entry_paths=entries,regression_argv=['node','--test','--test-reporter=tap',*tests],timeout_s=15)
    result=asyncio.run(ReviewRepairEngine(provider,host,RepairPolicy(max_hypotheses=2,timeout_ms=180000)).run(original,task=args.task,allowed_paths=set(source_paths),scratch_root=run/'workspaces'))
    report=result.model_dump(mode='json')
    if result.candidate_workspace:
        candidate=Path(result.candidate_workspace)
        patch=''.join(''.join(difflib.unified_diff(original[p].splitlines(True),(candidate/p).read_text(encoding='utf-8').splitlines(True),fromfile=p,tofile=p)) for p in source_paths)
        (run/'candidate.patch').write_text(patch,encoding='utf-8',newline='\n')
        report['patch']=str(run/'candidate.patch')
    report['report']=str(run/'report.json')
    (run/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(report,indent=2))
    return 2 if result.status in {'invalid','infrastructure_failure'} else 0
