"""Network-free guards for fresh, resumable extraction and the shared budget."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('execution67_under_test', Path(__file__).with_name('67_69_execution_runtime.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(T, 'ROOT', tmp_path)
    monkeypatch.setattr(T.R.X.R.P, 'ROOT', tmp_path)
    stage = 670
    base = tmp_path / T.LEAVES[stage]
    base.mkdir(parents=True)
    for common in T.stage_registries():
        monkeypatch.setitem(common.STAGES, stage, base)
        monkeypatch.setattr(common, 'ROOT', tmp_path)
    source = tmp_path / 'source.txt'
    source.write_text('fixed source')
    question = {'query_id':'T01','question':'커피 할인 카드를 추천해줘.'}
    identities = [T.attempt_identity(f'T01:parser:r{r}', a) for r in [1,2,3] for a in [1,2]]
    request = [{'role':'user','content':question['question']}]
    manifest = {'stage':stage,'parser_variant':'old','source_sha256':{'source.txt':T.E.file_digest(source)},'critical_data_sha256':{},
      'notebook_phase_code_sha256':{},'allowed_identities':{'parser':identities,'answer':[]},
      'allowed_request_by_identity':{i:T.R.P.sha(request) for i in identities},
      'logical_identity_by_physical':{i:i.split(':retry2')[0] for i in identities},
      'parser_instructions_sha256':T.R.P.sha(T.OLD_PARSER_PROMPT),
      'parser_schema_sha256':T.R.P.sha(T.R.Q.QueryPlan.model_json_schema()),
      'maximum_parser_requests':6,'maximum_answer_requests':0,'maximum_physical_requests':6,'stage_cost_cap_usd':'8'}
    (base/'execution_manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(T,'budget_ledger',lambda:{'unknown_cost_records':0,'known_cost_usd':'0','budget_debit_usd':'0'})
    count=[]
    def fetch(self,kwargs):
        count.append(self.identity)
        value=T.R.Q.plan([T.R.Q.requirement(['커피'],action='discount')])
        raw={'id':'response_'+str(len(count)),'model':'gpt-6-luna','service_tier':'default','status':'completed',
             'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20,'input_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}},'output':[]}
        return SimpleNamespace(model_dump=lambda **kw:raw, parsed_value=value)
    monkeypatch.setattr(T.Cache,'fetch',fetch)
    monkeypatch.setattr(T.Cache,'parse_raw',staticmethod(lambda response,schema:SimpleNamespace(output_parsed=schema.model_validate(response.parsed_value))))
    return stage,base,question,manifest,count


def test_same_checkpoint_resumes_without_another_paid_request(sandbox):
    stage,base,q,manifest,calls=sandbox
    first=T.extract_query(stage,q,1,allow_network=True)
    second=T.extract_query(stage,q,1,allow_network=True)
    assert first==second and len(calls)==1
    assert len(list((base/'api_cache').glob('*.json')))==1


def test_three_repetitions_use_three_fresh_response_identities(sandbox):
    stage,base,q,manifest,calls=sandbox
    rows=[T.extract_query(stage,q,r,allow_network=True)for r in [1,2,3]]
    assert len(calls)==3
    assert len({r['request_keys'][-1]for r in rows})==3


def test_changed_question_cannot_reuse_a_saved_plan(sandbox):
    stage,base,q,manifest,calls=sandbox
    T.extract_query(stage,q,1,allow_network=True)
    with pytest.raises(RuntimeError,match='question|checkpoint|payload'):
        T.extract_query(stage,{**q,'question':'넷플릭스 카드 추천'},1,allow_network=True)
    assert len(calls)==1


def test_budget_exhaustion_is_detected_before_network(sandbox,monkeypatch):
    stage,base,q,manifest,calls=sandbox
    monkeypatch.setattr(T,'budget_ledger',lambda:{'unknown_cost_records':0,'known_cost_usd':'7.999999','budget_debit_usd':'7.999999'})
    with pytest.raises(RuntimeError,match='8 USD|budget|cap'):
        T.extract_query(stage,q,1,allow_network=True)
    assert calls==[]


def test_unknown_new_usage_stops_before_network(sandbox,monkeypatch):
    stage,base,q,manifest,calls=sandbox
    monkeypatch.setattr(T,'budget_ledger',lambda:{'unknown_cost_records':1,'known_cost_usd':'0','budget_debit_usd':'0'})
    with pytest.raises(RuntimeError,match='Unknown|unknown'):
        T.extract_query(stage,q,1,allow_network=True)
    assert calls==[]


def test_source_mutation_blocks_resume_and_network(sandbox):
    stage,base,q,manifest,calls=sandbox
    T.extract_query(stage,q,1,allow_network=True)
    (T.ROOT/'source.txt').write_text('changed source')
    with pytest.raises((RuntimeError,ValueError),match='source|changed'):
        T.extract_query(stage,q,2,allow_network=True)
    assert len(calls)==1


def test_request_budget_and_schema_remain_the_operating_contract():
    q={'query_id':'Q','question':'카페 할인 카드'}
    for prompt in [T.OLD_PARSER_PROMPT,T.NEW_PARSER_PROMPT]:
        value=T.parser_kwargs(q,prompt)
        assert value['model']=='gpt-6-luna' and value['max_output_tokens']==3000
        assert value['text_format'].model_json_schema()==T.R.Q.QueryPlan.model_json_schema()
        assert value['input']==[{'role':'user','content':q['question']}]
    with pytest.raises(ValueError):T.attempt_identity('Q:parser:r1',3)


def test_direct_prompt_transform_is_idempotent():
    old='fixed existing answer instructions'
    new=T.transform_prompt(old,'direct')
    assert new!=old and T.transform_prompt(new,'direct')==new
    assert T.transform_prompt(old,'old')==old


def test_shared67_root_metadata_is_writable_without_becoming_a_paid_stage(tmp_path,monkeypatch):
    base=tmp_path/'67_structured_retrieval_repair'
    for common in T.stage_registries():
        monkeypatch.setattr(common,'ROOT',tmp_path)
        monkeypatch.setitem(common.STAGES,67,base)
    T.R.X.frozen(base/'acceptance.json',{'status':'offline_test'})
    assert json.loads((base/'acceptance.json').read_text())=={'status':'offline_test'}
    assert 67 not in T.R.P.STAGES and 67 not in T.LEAVES


def test_known_parse_failure_retries_with_distinct_preserved_raw(sandbox,monkeypatch):
    stage,base,q,manifest,calls=sandbox
    parse=T.Cache.parse_raw
    def first_fails(response,schema):
        if len(calls)==1:
            raise ValueError('known usage but invalid structured output')
        return parse(response,schema)
    monkeypatch.setattr(T.Cache,'parse_raw',staticmethod(first_fails))
    row=T.extract_query(stage,q,1,allow_network=True)
    assert row['status']=='ok' and row['physical_identities']==['T01:parser:r1','T01:parser:r1:retry2']
    raw=[json.loads((base/'api_cache'/(k+'.json')).read_text()) for k in row['request_keys']]
    assert [r['status'] for r in raw]==['failed','ok']
    assert all(r['usage']['total_tokens']==20 for r in raw)


def test_new_unknown_usage_preserves_checkpoint_and_stops_retry(sandbox,monkeypatch):
    stage,base,q,manifest,calls=sandbox
    def failed_fetch(self,kwargs):
        calls.append(self.identity)
        raise ConnectionError('no usage returned')
    monkeypatch.setattr(T.Cache,'fetch',failed_fetch)
    monkeypatch.setattr(T,'budget_ledger',lambda:{'unknown_cost_records':int(bool(calls)), 'known_cost_usd':'0'})
    with pytest.raises(RuntimeError,match='Unknown new API usage'):
        T.extract_query(stage,q,1,allow_network=True)
    row=json.loads((base/'plans/T01:parser:r1.json').read_text())
    assert len(calls)==1 and not row['complete'] and row['status']=='failed'
    assert len(list((base/'api_cache').glob('*.json')))==1


def test_resume_after_known_first_failure_only_performs_second_attempt(sandbox,monkeypatch):
    stage,base,q,manifest,calls=sandbox
    parse=T.Cache.parse_raw
    monkeypatch.setattr(T.Cache,'parse_raw',staticmethod(lambda response,schema: (_ for _ in ()).throw(ValueError('known parse failure'))))
    original_write=T.write
    def interrupt_after_checkpoint(path,value):
        original_write(path,value)
        raise KeyboardInterrupt('simulated process stop after first durable attempt')
    monkeypatch.setattr(T,'write',interrupt_after_checkpoint)
    with pytest.raises(KeyboardInterrupt):T.extract_query(stage,q,1,allow_network=True)
    assert calls==['T01:parser:r1']
    monkeypatch.setattr(T,'write',original_write)
    monkeypatch.setattr(T.Cache,'parse_raw',staticmethod(parse))
    row=T.extract_query(stage,q,1,allow_network=True)
    assert row['status']=='ok' and row['resumed_partial'] and len(calls)==2
    assert calls[-1].endswith(':retry2')


def test_saved_raw_input_mutation_blocks_resume(sandbox):
    stage,base,q,manifest,calls=sandbox
    row=T.extract_query(stage,q,1,allow_network=True)
    path=base/'api_cache'/(row['request_keys'][0]+'.json')
    raw=json.loads(path.read_text());raw['request']['input']=[{'role':'user','content':'changed'}]
    path.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError,match='physical request'):
        T.extract_query(stage,q,1,allow_network=True)
    assert len(calls)==1


def test_ledger_rechecks_historical_membership_and_bytes(tmp_path,monkeypatch):
    monkeypatch.setattr(T,'ROOT',tmp_path)
    folder=tmp_path/'old/api_cache';folder.mkdir(parents=True)
    old=folder/'one.json';old.write_text('{}')
    base=tmp_path/'notebooks/data/67_structured_retrieval_repair';base.mkdir(parents=True)
    value={'files_sha256':{'old/api_cache/one.json':T.E.file_digest(old)},
      'directory_members':{'old/api_cache':['old/api_cache/one.json']},
      'historical_budget_debit_usd':'1.658813910','historical_reserved_usd':'0.003'}
    (base/'shared_budget_baseline.json').write_text(json.dumps(value))
    for stage in T.LEAVES:monkeypatch.setitem(T.R.P.F.STAGES,stage,tmp_path/('new'+str(stage)))
    ledger=T.budget_ledger()
    assert ledger['budget_debit_usd']=='1.658813910' and ledger['new_known_API_cost_usd']=='0'
    (folder/'extra.json').write_text('{}')
    with pytest.raises(RuntimeError,match='membership'):T.budget_ledger()
    (folder/'extra.json').unlink()
    old.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='changed'):T.budget_ledger()
