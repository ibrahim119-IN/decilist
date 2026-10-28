"""CLI wiring and frozen-comparison input boundaries, without study inference."""
import asyncio
import json
from pathlib import Path

import pytest

from decilist import review_repair_cli
from decilist.review_repair import RepairResult, ReviewRepairEngine, ProviderResponseError


def test_collect_omits_secrets_dependencies_and_prior_runs(tmp_path):
    (tmp_path/'index.js').write_text('export const value = 1;')
    (tmp_path/'package.json').write_text('{"type":"module"}')
    (tmp_path/'.env').write_text('PRIVATE=do-not-read')
    for folder in ('node_modules','.git','.decilist-review'):
        (tmp_path/folder).mkdir();(tmp_path/folder/'secret.js').write_text('not source')
    result=review_repair_cli.collect_project(tmp_path)
    assert set(result)=={'index.js','package.json'}


def test_cli_requires_explicit_execution_before_touching_project(tmp_path):
    with pytest.raises(SystemExit) as failure:
        review_repair_cli.main([str(tmp_path),'--source','index.js','--regression','test.cjs','--task','known public behavior'])
    assert failure.value.code==2
    assert not (tmp_path/'.decilist-review').exists()


def test_cli_records_abstention_without_changing_original(tmp_path,monkeypatch,capsys):
    (tmp_path/'index.js').write_text('export const value = 1;')
    (tmp_path/'test.cjs').write_text('// existing tests')
    class Engine:
        def __init__(self,*args):pass
        async def run(self,sources,**kwargs):
            assert sources['index.js']=='export const value = 1;'
            assert kwargs['allowed_paths']=={'index.js'}
            return RepairResult(status='unproven',reason='no_hypothesis',source_sha256='test',attempts=[],latency_ms=1)
    monkeypatch.setattr(review_repair_cli,'ReviewRepairEngine',Engine)
    assert review_repair_cli.main([str(tmp_path),'--source','index.js','--regression','test.cjs','--task','Export a constant','--allow-execution'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='unproven'
    assert result['contract_validation']=='not_performed'
    assert result['review_required'] is True
    assert Path(result['report']).is_file()
    assert 'patch' not in result
    assert (tmp_path/'index.js').read_text()=='export const value = 1;'


def test_relative_path_must_stay_in_project(tmp_path):
    with pytest.raises(ValueError):review_repair_cli._relative_file(tmp_path,'../missing.js')


def test_oversized_project_is_not_silently_truncated(tmp_path):
    (tmp_path/'index.js').write_text('x'*512001)
    with pytest.raises(ValueError):review_repair_cli.collect_project(tmp_path)


@pytest.mark.parametrize('code', ['prohibited_reproduction', 'response_truncated', 'private source must not leak'])
def test_bad_proposal_is_invalid_without_executing_or_exposing_private_text(tmp_path, code):
    class Provider:
        async def hypothesize(self, snapshot, task):
            raise ProviderResponseError(code)
    class Host:
        async def reproduce(self, *args):
            pytest.fail('An invalid proposal must never execute')
        async def regress(self, *args):
            pytest.fail('An invalid proposal must never execute')
    result = asyncio.run(ReviewRepairEngine(Provider(), Host()).run(
        {'index.js':'export const value = 1;'}, task='Export a constant.',
        allowed_paths={'index.js'}, scratch_root=tmp_path))
    assert result.status == 'invalid'
    assert result.reason == (code if code in {'prohibited_reproduction', 'response_truncated'} else 'invalid_provider_output')
    assert result.candidate_workspace is None
    assert result.contract_validation == 'not_performed' and result.review_required
    assert 'private source' not in result.model_dump_json()
