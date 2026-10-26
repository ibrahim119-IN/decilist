"""Private corpus tests. Never include these or the catalog in participant input."""
from collections import Counter
import json
from pathlib import Path
from uuid import uuid4

import pytest

from benchmarks import triage_v6_lab as lab


@pytest.fixture(scope='module')
def replayed():
    root = lab.LAB_ROOT / ('unit-proof-' + uuid4().hex[:12])
    return root, lab.verify_all(root)


def fresh(case_id='d01', *, work_root=None):
    root = Path(work_root) if work_root is not None else lab.LAB_ROOT
    return Path(lab.prepare_case(case_id, root / ('unit-' + uuid4().hex[:12]), work_root=work_root)['case_dir'])


def test_frozen_membership_and_explicit_seed_provenance():
    catalog = lab.load_catalog()
    assert catalog['version'] == 6
    assert catalog['base_revision'] == '8327d24aceee015af3c79bf8a679cd7aad6917f7'
    assert Counter(row['split'] for row in catalog['cases']) == {'development': 2, 'evaluation': 6, 'reserved': 4, 'controls': 2}
    assert lab.development_case_ids() == ('d01', 'd02')
    assert lab.trial_case_ids() == ('r01', 'r02', 'r03', 'r04')
    assert all(lab.get_case_role(cid) == 'trial_regression' for cid in lab.trial_case_ids())
    assert 'no claim of naturally occurring production bugs' in catalog['purpose']
    assert 'does not establish human difficulty' in catalog['difficulty_policy']
    previous_seeds = set()
    for version in (2, 3, 4, 5):
        previous = json.loads((lab.REPO_ROOT / f'benchmarks/triage_v{version}_cases.json').read_text())
        previous_seeds.update((m['path'], m.get('old'), m.get('new')) for c in previous['cases'] for m in c['mutations'])
    for case in catalog['cases']:
        assert len({path for path, _ in case['investigation_surface']}) >= 2
        assert sum(len(symbols) for _, symbols in case['investigation_surface']) >= 5
        assert '@pytest.mark.parametrize' in case['case_tests'] and '@pytest.mark.parametrize' in case['regression_tests']
        assert case['evidence_mode'] == 'full'
        assert not case['structural_indicators']['human_difficulty_established']
        assert all((m['path'], m['old'], m['new']) not in previous_seeds for m in case['mutations'])
        assert bool(case['mutations']) == (case['id'] != 'c01')
    assert len({c['mechanism_family'] for c in catalog['cases']}) == 14
    with pytest.raises(ValueError, match='unknown'): lab.get_case_role('unknown')
    with pytest.raises(ValueError, match='unknown split'): lab.case_ids('unknown')


@pytest.mark.parametrize('case_id', ['d01','d02','e01','e02','e03','e04','e05','e06','r01','r02','r03','r04','c01','c02'])
def test_actual_original_seed_reference_observations(case_id, replayed):
    root, proof = replayed
    row = next(row for row in proof['cases'] if row['case_id'] == case_id)
    assert row['verified'], {key: row.get(key) for key in ('case_id','error','baseline','mutation','reference_repair')}
    assert row['baseline']['exit_code'] == 0 and row['reference_repair']['passed']
    assert row['mutation']['exit_code'] == (0 if case_id == 'c01' else 1)
    assert row['candidate_status'] == ('non_failing_reproduction' if case_id == 'c01' else 'executable_regression')
    for run in (row['baseline'], row['mutation'], row['reference_repair']['run']):
        assert run['protected_files_ok'] and run['source_stable'] and run['checks_complete']
        assert run['test_counts']['collected'] == lab._case(case_id)['expected_check_count'] >= 6
        assert run['test_counts']['executed'] == run['test_counts']['collected']
        assert run['test_counts']['skipped'] == 0 and run['test_counts']['xfailed'] == 0
        assert (root / case_id / run['log_path']).is_file()
        assert lab.digest((root / case_id / run['audit_path']).read_bytes()) == run['audit_sha256']


def test_private_proof_snapshots_and_development_only_view(replayed):
    root, proof = replayed
    assert proof['candidate_count'] == proof['verified_count'] == 14 and proof['all_verified']
    assert (root / 'catalog_snapshot.json').read_bytes() == lab.CATALOG.read_bytes()
    assert (root / 'lab_snapshot.py').read_bytes() == Path(lab.__file__).read_bytes()
    dev = json.loads((root / 'development-proof.json').read_text())
    assert [r['case_id'] for r in dev['cases']] == ['d01', 'd02']
    assert len(json.loads((root / 'proof.json').read_text())['cases']) == 14


def test_neutral_manifests_broad_roots_and_full_observations(replayed):
    root, proof = replayed
    wanted = {'schema_version','suite','case_id','allowed_production_roots','failing_tests','regression_tests','test_command'}
    forbidden = {'mutations','private_mechanism','repair_invariants','minimal_repair_invariants','inspection_targets',
                 'root_cause_discriminating_facts','acceptable_routes','reference_repair','mechanism_family'}
    for row in proof['cases']:
        directory = root / row['case_id']
        manifest = json.loads((directory / lab.MANIFEST_PATH).read_text())
        assert set(manifest) == wanted and manifest['allowed_production_roots'] == list(lab.PRODUCTION_ROOTS)
        assert manifest['failing_tests'] == [lab.TEST_PATH] and manifest['regression_tests'] == [lab.REGRESSION_TEST_PATH]
        assert not (directory / 'benchmarks').exists()
        evidence = lab.prepare_case_evidence(directory, row['mutation'])
        assert forbidden.isdisjoint(evidence) and evidence['exit_code'] == row['mutation']['exit_code']
        if row['case_id'] in lab.trial_case_ids():
            with pytest.raises(ValueError, match='reserved blind'): lab.prepare_machine_evidence(directory, row['mutation'])
        else:
            assert lab.prepare_machine_evidence(directory, row['mutation'])['log'] == evidence['log']
    adjacent = root / 'd01/vendor/httpx/_status_codes.py'
    original = adjacent.read_bytes()
    try:
        adjacent.write_bytes(original + b'\n')
        assert lab._integrity(root / 'd01', None)[0]
    finally:
        adjacent.write_bytes(original)


def test_nonfailing_and_multicause_controls_require_actual_unchanged_tests():
    passing = fresh('c01')
    run = lab.run_case(passing)
    assert run['exit_code'] == 0 and run['checks_complete']
    assert lab.prepare_case_evidence(passing, run)['exit_code'] == 0
    mixed = fresh('c02')
    case = lab._case('c02')
    assert len(case['mutations']) == 2
    for remaining in case['mutations']:
        lab._reference_repair(mixed)
        lab._apply_mutation(mixed, {'id':'c02','mutations':[remaining]})
        partial = lab.validate_repair(mixed)
        assert not partial['passed'] and partial['run']['exit_code'] == 1
        assert partial['run']['checks_complete'] and partial['run']['test_counts']['failed'] >= 1
    lab._reference_repair(mixed)
    assert lab.validate_repair(mixed)['passed']


@pytest.mark.parametrize('stop', ['skip', 'xfail'])
def test_skipped_or_xfailed_checks_cannot_be_reported_as_a_successful_repair(stop):
    directory = fresh('d01')
    lab._reference_repair(directory)
    target = directory / 'vendor/httpx/_exceptions.py'
    source = target.read_text()
    marker = '    try:\n        yield\n    except RequestError as exc:'
    assert source.count(marker) == 1
    target.write_bytes(source.replace(marker, '    import pytest\n    pytest.' + stop + '("execution completeness guard")\n' + marker).encode())
    outcome = lab.validate_repair(directory)
    assert outcome['run']['exit_code'] == 0
    assert outcome['run']['test_counts']['skipped'] > 0
    assert bool(outcome['run']['test_counts']['xfailed']) == (stop == 'xfail')
    assert not outcome['run']['checks_complete'] and not outcome['run']['test_execution_complete']
    assert not outcome['passed'] and outcome['integrity_ok']


def test_protected_tests_audit_and_manifest_cannot_be_redefined():
    directory = fresh()
    (directory / lab.TEST_PATH).write_text('def test_bypass(): assert True\n')
    (directory / lab.AUDIT_PLUGIN_PATH).write_text('')
    manifest = json.loads((directory / lab.MANIFEST_PATH).read_text())
    manifest['failing_tests'] = []
    (directory / lab.MANIFEST_PATH).write_text(json.dumps(manifest))
    outcome = lab.validate_repair(directory)
    assert not outcome['passed'] and not outcome['integrity_ok'] and outcome['run']['exit_code'] is None
    assert any('protected file changed' in issue for issue in outcome['run']['integrity_issues'])


def test_evidence_requires_retained_observation():
    directory = fresh()
    observed = lab.run_case(directory)
    observed['log_path'] = '.triage/runs/not-retained.log'
    with pytest.raises(ValueError, match='retained observation'): lab.prepare_case_evidence(directory, observed)


def test_portable_work_root_and_checkout_rejection(tmp_path):
    work_root = tmp_path / 'portable-work'
    directory = fresh(work_root=work_root)
    assert directory.is_relative_to(work_root) and lab._integrity(directory, work_root)[0]
    assert not lab._integrity(directory, None)[0]
    with pytest.raises(ValueError, match='outside the checkout'):
        lab.prepare_case('d01', lab.REPO_ROOT / 'forbidden', work_root=lab.REPO_ROOT)
    with pytest.raises(ValueError, match='already exists'): lab.prepare_case('d01', directory, work_root=work_root)


def test_installed_external_snapshot_and_actual_license_texts_are_pinned():
    catalog, files = lab.load_catalog(), lab._base_files()
    project = catalog['projects']['httpx']
    assert project['version'] == '0.28.1' and project['license'] == 'BSD-3-Clause'
    for relative, wanted in project['installed_source_sha256'].items(): assert lab.digest(files[relative]) == wanted
    assert lab.digest(files['vendor/LICENSE.httpx.md']) == project['license_sha256']
    assert lab.digest(files['LICENSE.decilist']) == catalog['projects']['decilist']['license_sha256']
    assert b'Apache License' in files['LICENSE.decilist'] and b'Version 2.0' in files['LICENSE.decilist']
    assert b'Redistribution and use in source and binary forms' in files['vendor/LICENSE.httpx.md']


def test_wrong_dependency_version_is_rejected(monkeypatch):
    import httpx
    lab._base_files.cache_clear()
    monkeypatch.setattr(httpx, '__version__', 'unverified')
    try:
        with pytest.raises(ValueError, match='already installed httpx==0.28.1'): lab._base_files()
    finally:
        lab._base_files.cache_clear()


def test_failed_authoring_candidates_are_retained(monkeypatch, tmp_path):
    def ineffective(*args, **kwargs): raise ValueError('ineffective candidate observation')
    monkeypatch.setattr(lab, 'verify_fixture', ineffective)
    proof = lab.verify_all(tmp_path / 'kept', ids=('d01','d02'), work_root=tmp_path)
    assert proof['candidate_count'] == 2 and proof['verified_count'] == 0 and not proof['all_verified']
    assert [r['case_id'] for r in proof['cases']] == ['d01','d02']
    assert all(r['candidate_status'] == 'retained_candidate_failure' for r in proof['cases'])
    assert (tmp_path / 'kept/catalog_snapshot.json').is_file()


def test_specific_freeze_covers_corpus_source_and_retained_proof():
    frozen = lab.verify_freeze()
    assert frozen['suite'] == 'testtriage-repair-v6'
    assert frozen['development_ids'] == ['d01','d02']
    assert len(frozen['evaluation_ids']) == 6 and len(frozen['reserved_trial_ids']) == 4
    assert set(frozen['files_sha256']) == {'benchmarks/triage_v6_lab.py','benchmarks/triage_v6_cases.json','tests/test_triage_v6_lab.py'}
    assert frozen['fixture_proof']['all_verified']
    assert frozen['authoring_attempts_retained']
    assert frozen['declared_manifest_identity'] and not frozen['retained_proof_verified']
    assert frozen['retained_proof_status'] == 'not_requested'


def test_portable_identity_does_not_silently_verify_missing_historical_proof(monkeypatch, tmp_path):
    monkeypatch.setattr(lab, 'LAB_ROOT', tmp_path / 'fresh-checkout-work')
    identity = lab.verify_freeze()
    assert identity['declared_manifest_identity']
    assert not identity['retained_proof_available'] and not identity['retained_proof_verified']
    with pytest.raises(ValueError, match='retained V6 proof is unavailable'):
        lab.verify_freeze(require_retained_proof=True)


def test_strict_historical_verification_uses_actual_fresh_replay(monkeypatch, tmp_path, replayed):
    root, proof = replayed
    frozen = json.loads(lab.FREEZE.read_text())
    frozen['fixture_proof'].update(relative_directory=root.name,
        proof_sha256=lab.digest((root / 'proof.json').read_bytes()),
        development_proof_sha256=lab.digest((root / 'development-proof.json').read_bytes()))
    local_freeze = tmp_path / 'private-proof-freeze.json'
    local_freeze.write_bytes((json.dumps(frozen, indent=2)+'\n').encode())
    monkeypatch.setattr(lab, 'FREEZE', local_freeze)
    strict = lab.verify_freeze(require_retained_proof=True)
    assert strict['declared_manifest_identity'] and strict['retained_proof_available']
    assert strict['retained_proof_verified'] and strict['retained_proof_status'] == 'verified'
    frozen['fixture_proof']['proof_sha256'] = '0'*64
    local_freeze.write_bytes((json.dumps(frozen, indent=2)+'\n').encode())
    with pytest.raises(ValueError, match='proof changed'):
        lab.verify_freeze(require_retained_proof=True)


def test_exact_frozen_check_count_rejects_dropped_checks(tmp_path, replayed):
    root, proof = replayed
    row = proof['cases'][0]
    audit = json.loads((root / row['case_id'] / row['baseline']['audit_path']).read_text())
    dropped = audit['collected'].pop()
    audit['reports'] = [r for r in audit['reports'] if r['nodeid'] != dropped]
    path = tmp_path / 'audit.json'
    path.write_bytes(json.dumps(audit).encode())
    result = lab._execution_audit(path, 0, lab._case(row['case_id'])['expected_check_count'])
    assert result['test_counts']['collected'] == 8 and not result['checks_complete']


def test_frozen_files_are_utf8_lf_bytes():
    for relative in ('benchmarks/triage_v6_lab.py', 'benchmarks/triage_v6_cases.json',
                     'benchmarks/triage_v6_freeze.json', 'tests/test_triage_v6_lab.py'):
        raw = (lab.REPO_ROOT / relative).read_bytes()
        assert b'\r\n' not in raw and not raw.startswith(b'\xef\xbb\xbf')
        raw.decode('utf-8')
