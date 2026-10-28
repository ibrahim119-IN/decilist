"""Prepare byte-pinned historical sources; never run upstream install scripts."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def snapshot(repo, revision, target, archive_root):
    archive = archive_root / (revision + '.zip')
    if not archive.exists():
        url = f'https://codeload.github.com/{repo}/zip/{revision}'
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read(50_000_001)
        if len(data) > 50_000_000:
            raise RuntimeError('archive exceeds 50 MB preparation limit')
        archive.write_bytes(data)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            parts = Path(item.filename).parts[1:]
            if not parts or item.is_dir():
                continue
            relative = Path(*parts)
            if relative.is_absolute() or '..' in parts or ':' in str(relative):
                raise ValueError('unsafe archive path')
            first = parts[0].lower()
            keep = first in {'lib','src'} or (len(parts)==1 and (relative.suffix in {'.js','.json'} or first.startswith('license')))
            if not keep or first in {'test.js','test','tests'}:
                continue
            if item.file_size > 2_000_000 or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('oversized source or archive link')
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = bundle.read(item)
            if path.exists() and path.read_bytes() != content:
                raise ValueError('refuse to replace modified historical source')
            path.write_bytes(content)
    if not any(p.is_file() and p.name.lower().startswith('license') for p in target.iterdir()):
        raise ValueError('upstream license missing')
    return {'revision': revision, 'archive_sha256': digest(archive),
            'files': {p.relative_to(target).as_posix():digest(p) for p in sorted(target.rglob('*')) if p.is_file()}}

def prepare(case, root):
    folder = root / case['id']
    archives = root / 'archives'
    archives.mkdir(parents=True, exist_ok=True)
    variants = {name:snapshot(case['repository'],case[name],folder/name,archives) for name in ('broken','fixed')}
    package = json.loads((folder/'fixed/package.json').read_text())
    deps = {'name':'decilist-natural-'+case['id'].lower(),'version':'0.0.0','private':True,'dependencies':package.get('dependencies',{})}
    (folder/'package.json').write_text(json.dumps(deps,indent=2)+'\n')
    return {'id':case['id'],'variants':variants,'dependencies':deps['dependencies']}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--install',action='store_true')
    args=parser.parse_args()
    args.root.mkdir(parents=True,exist_ok=True)
    catalog=json.loads(Path(__file__).with_name('code_review_natural_catalog.json').read_text())['cases']
    with ThreadPoolExecutor(max_workers=4) as pool:
        prepared=list(pool.map(lambda c:prepare(c,args.root),catalog))
    for item in prepared:
        if args.install:
            result=subprocess.run(['npm.cmd' if os.name=='nt' else 'npm','install','--ignore-scripts','--omit=dev','--no-audit','--no-fund'],cwd=args.root/item['id'],capture_output=True,text=True,timeout=240)
            (args.root/item['id']/'install.log').write_text(result.stdout+result.stderr)
            if result.returncode:
                raise RuntimeError(f"dependency installation failed for {item['id']}; retained install.log")
            item['dependency_lock_sha256']=digest(args.root/item['id']/'package-lock.json')
        print(json.dumps({'prepared':item['id'],'source_files':sum(len(v['files']) for v in item['variants'].values())}),flush=True)
    (args.root/'sources.json').write_text(json.dumps({'cases':prepared},indent=2)+'\n')

if __name__=='__main__':
    main()
