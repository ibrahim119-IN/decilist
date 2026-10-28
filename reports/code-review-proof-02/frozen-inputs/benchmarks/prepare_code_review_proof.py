"""Prepare byte-pinned upstream sources and protected proof-first review checks.

No inference is performed. The catalog and probes are host-private. Only the
allowlisted production source, purpose and shared public docs reach participants.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
CATALOG = HERE / 'code_review_proof_catalog.json'
PROBE = HERE / 'code_review_proof_probe.mjs'
MAX_ARCHIVE = 20_000_000


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        raise ValueError(f'refuse to replace modified prepared file: {path}')
    path.write_bytes(content)


def snapshot(case, state, target, archives):
    revision = case[state]
    archive = archives / (revision + '.zip')
    if not archive.exists():
        request = urllib.request.Request(
            f"https://codeload.github.com/{case['repository']}/zip/{revision}",
            headers={'User-Agent': 'Decilist-proof-corpus'},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE:
            raise ValueError('archive exceeds preparation size limit')
        archive.write_bytes(data)
    expected = case['variants'][state]['files']
    if 'archive_sha256' in case['variants'][state]:
        if digest(archive) != case['variants'][state]['archive_sha256']:
            raise ValueError('upstream archive hash differs from pinned archive')
    extracted = {}
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            parts = Path(member.filename).parts[1:]
            if not parts or member.is_dir():
                continue
            relative = Path(*parts)
            if relative.is_absolute() or '..' in parts or ':' in str(relative):
                raise ValueError('unsafe archive path')
            key = relative.as_posix()
            if key not in expected:
                continue
            if member.file_size > 1_000_000 or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('oversized source or archive link')
            content = bundle.read(member)
            if hashlib.sha256(content).hexdigest() != expected[key]:
                raise ValueError(f'upstream source hash mismatch: {case["id"]}/{state}/{key}')
            if key in extracted:
                raise ValueError('duplicate archive member')
            extracted[key] = content
    if set(extracted) != set(expected):
        raise ValueError('missing pinned upstream file')
    for key, content in extracted.items():
        write_once(target / key, content)
    return {'revision': revision, 'archive_sha256': digest(archive), 'files': expected,
            'source_lines': {f['path']: len((target / f['path']).read_text(encoding='utf-8').splitlines())
                             for f in case['source_files']}}


def prepare(case, root):
    folder = root / case['id']
    archives = root / 'archives'
    archives.mkdir(parents=True, exist_ok=True)
    variants = {state: snapshot(case, state, folder / state, archives) for state in ('broken', 'fixed')}
    dependencies = case['runtime_dependencies']
    package = {'name': 'decilist-proof-' + case['id'].lower(), 'version': '0.0.0',
               'private': True, 'dependencies': dependencies}
    write_once(folder / 'package.json', (json.dumps(package, indent=2) + '\n').encode())
    write_once(folder / 'shared-public-docs.md', (case['public_docs'] + '\n').encode())
    return {'id': case['id'], 'variants': variants, 'dependencies': dependencies,
            'shared_public_docs_sha256': digest(folder / 'shared-public-docs.md')}


def install(item, root, npm, env):
    folder = root / item['id']
    flags = ['--ignore-scripts', '--omit=dev', '--no-audit', '--no-fund']
    lock = folder / 'package-lock.json'
    commands = []
    # First preparation resolves exact direct dependencies. Thereafter npm ci
    # consumes the retained integrity-pinned transitive lock exclusively.
    if not lock.exists():
        commands.append([npm, 'install', '--package-lock-only', *flags])
    commands.append([npm, 'ci', *flags])
    log = []
    for command in commands:
        result = subprocess.run(command, cwd=folder, env=env, capture_output=True,
                                text=True, timeout=180)
        log.append(json.dumps({'command': command, 'returncode': result.returncode}) + '\n'
                   + result.stdout + result.stderr)
        (folder / 'install.log').write_text('\n'.join(log), encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'dependency installation failed for {item["id"]}; see install.log')
    record_installation(item, root)


def record_installation(item, root):
    folder = root / item['id']
    item['dependency_lock_sha256'] = digest(folder / 'package-lock.json')
    item['install_log_sha256'] = digest(folder / 'install.log')
    item['installed_dependency_files'] = {
        path.relative_to(folder).as_posix(): digest(path)
        for path in sorted((folder / 'node_modules').rglob('*')) if path.is_file()
    }


def preflight(prepared, root, node, env):
    rows = []
    for item in prepared:
        for state in ('broken', 'fixed'):
            for mode in ('regression', 'target'):
                command = [node, str(PROBE), item['id'], str(root / item['id'] / state), mode]
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
                output = result.stdout + result.stderr
                log = root / 'preflight-logs' / f'{item["id"]}-{state}-{mode}.log'
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(output, encoding='utf-8')
                try:
                    observation = json.loads(output.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    observation = {'status': 'invalid-probe-output'}
                expected = 'failed' if mode == 'target' and state == 'broken' else 'passed'
                status = observation.get('status')
                row = {'id': item['id'], 'state': state, 'mode': mode, 'expected': expected,
                       'command': command, 'returncode': result.returncode, 'observed': observation,
                       'matches_expected': status == expected and result.returncode == (0 if expected == 'passed' else 1),
                       'log_sha256': digest(log)}
                rows.append(row)
                print(json.dumps({key: row[key] for key in ('id', 'state', 'mode', 'matches_expected')}), flush=True)
    report = {'schema_version': 'review-proof-preflight.1', 'completed_at': datetime.now(timezone.utc).isoformat(),
              'node': subprocess.check_output([node, '--version'], env=env, text=True).strip(),
              'catalog_sha256': digest(CATALOG), 'probe_sha256': digest(PROBE),
              'preparer_sha256': digest(Path(__file__)), 'results': rows,
              'all_expected': all(row['matches_expected'] for row in rows)}
    (root / 'preflight.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if not report['all_expected']:
        raise RuntimeError('preflight failed; retain failed preparation evidence, do not expose participants')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--npm', default=shutil.which('npm.cmd' if os.name == 'nt' else 'npm'))
    args = parser.parse_args()
    args.root = args.root.resolve()
    # All preparation downloads, dependency caches and temporary files stay on L.
    if os.name == 'nt' and args.root.drive.upper() != 'L:':
        parser.error('--root must be on the active L project storage')
    if not args.node or not args.npm:
        parser.error('Node and npm executables are required')
    args.root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEMP='L:/CodexData/Decilist/tmp', TMP='L:/CodexData/Decilist/tmp',
               PIP_CACHE_DIR='L:/CodexData/Decilist/cache/pip',
               npm_config_cache='L:/CodexData/Decilist/cache/npm', npm_config_ignore_scripts='true')
    if subprocess.check_output([args.node, '--version'], env=env, text=True).strip() != 'v22.23.1':
        parser.error('the proof corpus requires the pinned Node v22.23.1 runtime')
    cases = json.loads(CATALOG.read_text(encoding='utf-8'))['cases']
    with ThreadPoolExecutor(max_workers=4) as pool:
        prepared = list(pool.map(lambda case: prepare(case, args.root), cases))
    for item in prepared:
        if args.install:
            install(item, args.root, args.npm, env)
        elif (args.root / item['id'] / 'package-lock.json').exists():
            record_installation(item, args.root)
        print(json.dumps({'prepared': item['id']}), flush=True)
    (args.root / 'sources.json').write_text(json.dumps({'cases': prepared}, indent=2) + '\n', encoding='utf-8')
    if args.preflight:
        preflight(prepared, args.root, args.node, env)


if __name__ == '__main__':
    main()
