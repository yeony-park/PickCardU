"""Network-free answer integration against the actual operating generation API."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec=importlib.util.spec_from_file_location('answers68_test',Path(__file__).with_name('68_69_answer_runtime.py'))
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
T=A.T


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(T,'ROOT',tmp_path);monkeypatch.setattr(T.R.X.R.P,'ROOT',tmp_path)
    base=tmp_path/'stage68';base.mkdir()
    for common in T.stage_registries():
        monkeypatch.setattr(common,'ROOT',tmp_path);monkeypatch.setitem(common.STAGES,68,base)
    source=tmp_path/'source.txt';source.write_text('immutable')
    job=dict(identity='Q:benefit:A_old:r1',query_id='Q',profile='benefit',mode='A_old',repeat=1,question='커피 할인 있는 카드?',
       result=dict(cards=[dict(card_key='a')],evidence=[dict(chunk_id='c1',card_key='a',card_name='A',issuer='은행',text='커피10%할인')]))
    identity=job['identity'];prompts=T.R.G.C.operating_prompts()
    manifest=dict(stage=68,model='gpt-6-luna',reasoning='medium',source_sha256={'source.txt':T.E.file_digest(source)},
        critical_data_sha256={},notebook_phase_code_sha256={},allowed_identities={'answer':[identity],'parser':[]},
        maximum_answer_requests=2,maximum_parser_requests=0,maximum_physical_requests=2,stage_cost_cap_usd='8',
        prompt_variant_by_identity={identity:'old'},answer_instructions_sha256_by_identity={identity:[T.R.P.sha(p)for p in prompts]})
    (base/'execution_manifest.json').write_text(json.dumps(manifest));contract=A.payload_contract([job],manifest)
    (base/'answer_payload_contract.json').write_text(json.dumps(contract))
    monkeypatch.setattr(T,'budget_ledger',lambda:{'known_cost_usd':'0','unknown_cost_records':0})
    calls=[]
    def fetch(self,kwargs):
        calls.append(kwargs)
        value=dict(answer_status='answered',answer_text='A는 커피10%할인 혜택이 있습니다.',
            recommendations=[dict(citations=['e1'],reason='커피10%할인')],
            claims=[dict(text='커피10%할인',citations=['e1'],value=10,unit='%',conditions=[])])
        raw=dict(id='r'+str(len(calls)),model='gpt-6-luna',service_tier='default',status='completed',usage=dict(input_tokens=10,output_tokens=10,total_tokens=20),output=[])
        return SimpleNamespace(model_dump=lambda **kw:raw,parsed_value=value)
    monkeypatch.setattr(T.Cache,'fetch',fetch)
    monkeypatch.setattr(T.Cache,'parse_raw',staticmethod(lambda response,schema:SimpleNamespace(output_parsed=schema.model_validate(response.parsed_value))))
    return base,job,manifest,contract,calls


def test_full_answer_restores_owned_card_and_resumes_without_fresh_call(setup):
    base,job,m,c,calls=setup
    row=A.generate(68,job,allow_network=True)
    assert row['status']=='ok' and row['answer']['recommendations'][0]['card_key']=='a'
    assert A.generate(68,job,allow_network=True)==row and len(calls)==1
    assert row['answer_ready_wall_seconds']>=0


def test_changed_U_evidence_cannot_reuse_output(setup):
    base,job,m,c,calls=setup
    A.generate(68,job,allow_network=True)
    altered=copy.deepcopy(job);altered['result']['evidence'][0]['text']='넷플릭스'
    with pytest.raises(RuntimeError,match='checkpoint'):A.generate(68,altered,allow_network=True)
    assert len(calls)==1


def test_answer_checkpoint_and_actual_raw_output_are_bound(setup):
    base,job,m,c,calls=setup
    row=A.generate(68,job,allow_network=True)
    rawpath=base/'api_cache'/(row['request_keys'][-1]+'.json');raw=json.loads(rawpath.read_text())
    raw['parsed']['answer_text']='변조된 답변';rawpath.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError,match='restored'):A.generate(68,job,allow_network=True)


def test_contract_rejects_missing_matrix_identity(setup):
    base,job,m,c,calls=setup
    with pytest.raises(ValueError,match='matrix'):A.payload_contract([] ,m)


def test_direct_prompt_is_applied_to_real_request_and_restoration(setup):
    base,job,m,c,calls=setup
    identity=job['identity'];m['prompt_variant_by_identity'][identity]='direct'
    m['answer_instructions_sha256_by_identity'][identity]=[T.R.P.sha(T.transform_prompt(p,'direct'))for p in T.R.G.C.operating_prompts()]
    (base/'execution_manifest.json').write_text(json.dumps(m));contract=A.payload_contract([job],m)
    (base/'answer_payload_contract.json').write_text(json.dumps(contract))
    row=A.generate(68,job,allow_network=True)
    assert row['status']=='ok' and len(calls)==1 and calls[0]['instructions'].endswith(T.DIRECT_PROMPT)
    assert A.generate(68,job,allow_network=False)==row


def test_known_validation_failure_obeys_exact_retry_prompt_order(setup,monkeypatch):
    base,job,m,c,calls=setup;original=T.Cache.parse_raw
    def parse(response,schema):
        if len(calls)==1:raise ValueError('technical format failure, usage known')
        return original(response,schema)
    monkeypatch.setattr(T.Cache,'parse_raw',staticmethod(parse))
    row=A.generate(68,job,allow_network=True)
    assert row['status']=='ok' and len(calls)==2
    assert [T.R.P.sha(v['instructions'])for v in calls]==m['answer_instructions_sha256_by_identity'][job['identity']]
    assert A.generate(68,job,allow_network=False)==row


def test_unknown_usage_persists_failure_and_stops_next_call(setup,monkeypatch):
    base,job,m,c,calls=setup
    def fail(self,kwargs):calls.append(kwargs);raise ConnectionError('usage unknown')
    monkeypatch.setattr(T.Cache,'fetch',fail)
    monkeypatch.setattr(T,'budget_ledger',lambda:{'known_cost_usd':'0','unknown_cost_records':int(bool(calls))})
    with pytest.raises(RuntimeError,match='Unknown new usage'):A.generate(68,job,allow_network=True)
    row=json.loads((base/'answers'/(job['identity']+'.json')).read_text())
    assert row['status']=='failed' and len(calls)==1


def test_cross_card_citations_remain_failed_after_operating_retry(setup,monkeypatch):
    base,job,m,c,calls=setup
    job['result']['evidence'].append(dict(chunk_id='c2',card_key='b',card_name='B',issuer='은행',text='커피 할인'))
    contract=A.payload_contract([job],m);(base/'answer_payload_contract.json').write_text(json.dumps(contract))
    fetch=T.Cache.fetch
    def mixed(self,kwargs):
        response=fetch(self,kwargs);response.parsed_value['recommendations'][0]['citations']=['e1','e2']
        return response
    monkeypatch.setattr(T.Cache,'fetch',mixed)
    row=A.generate(68,job,allow_network=True)
    assert row['status']=='failed' and len(calls)==2
    assert A.generate(68,job,allow_network=False)==row


def test_retrieval_failure_cannot_be_reported_as_successful_abstention(setup):
    base,job,m,c,calls=setup
    job['result'].update(technical_failure='reranker bounds',evidence=[],cards=[])
    contract=A.payload_contract([job],m);(base/'answer_payload_contract.json').write_text(json.dumps(contract))
    row=A.generate(68,job,allow_network=True)
    assert row['status']=='failed' and row['answer'] is None and calls==[]
    assert A.generate(68,job,allow_network=False)==row


def test_interruption_after_failed_first_validation_resumes_second_prompt_only(setup,monkeypatch):
    base,job,m,c,calls=setup;original=T.Cache.parse_raw
    def interrupted(response,schema):
        if len(calls)==1:raise ValueError('known technical validation failure')
        return original(response,schema)
    monkeypatch.setattr(T.Cache,'parse_raw',staticmethod(interrupted))
    original_parse=A.Cache.parse
    def stop_between_attempts(self,**kwargs):
        if len(self.events)==1:raise KeyboardInterrupt('process stops before attempt2')
        return original_parse(self,**kwargs)
    monkeypatch.setattr(A.Cache,'parse',stop_between_attempts)
    with pytest.raises(KeyboardInterrupt):A.generate(68,job,allow_network=True)
    assert len(calls)==1 and not (base/'answers'/(job['identity']+'.json')).exists()
    monkeypatch.setattr(A.Cache,'parse',original_parse)
    row=A.generate(68,job,allow_network=True)
    assert row['status']=='ok' and len(calls)==2
    assert row['events'][0]['cache_hit'] and not row['events'][1]['cache_hit']


def test_per_identity_contract_is_compatible_with66_actual_input_verifier(setup):
    base,job,m,c,calls=setup
    second=copy.deepcopy(job);second['identity']='Q:benefit:B_old:r1';second['mode']='B_old'
    m['allowed_identities']['answer'].append(second['identity'])
    m['prompt_variant_by_identity'][second['identity']]='old'
    m['answer_instructions_sha256_by_identity'][second['identity']]=m['answer_instructions_sha256_by_identity'][job['identity']]
    (base/'execution_manifest.json').write_text(json.dumps(m));contract=A.payload_contract([job,second],m)
    (base/'answer_payload_contract.json').write_text(json.dumps(contract))
    row=A.generate(68,job,allow_network=True)
    own=json.loads((base/'payload_contracts'/(job['identity']+'.json')).read_text())
    assert len(own['input_sha256_by_identity'])==1 and T.R.P.sha(own)==row['payload_contract_sha256']
    assert own!=contract
    # Verifier uses repository-relative receipts; its ROOT is the test workspace.
    old=T.E.ROOT;T.E.ROOT=T.ROOT
    try:receipt=T.E.verify_saved_input(job,row,base)
    finally:T.E.ROOT=old
    assert receipt['status']=='verified_actual_saved_input'
