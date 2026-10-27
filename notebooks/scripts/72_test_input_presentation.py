"""Lossless business-field projection and request-local ownership regressions."""
import copy
import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).with_name('72_input_presentation.py')
    assert path.exists(), '72 input formatter is not implemented'
    spec = importlib.util.spec_from_file_location('presentation72_test', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def fact():
    return dict(card_key='owner/A', card_name='A카드', issuer='발급사', fact_id='fact-original',
                source_relative_path='original/path.json', fields=dict(target='커피\n카페',
                action='할인',benefit_type='커피',value='10',unit='%',condition='전월 30만원',
                cap='할인받기 전 이용금액 기준 1만원',period='',frequency='월 2회',exceptions='백화점 제외'),
                derived=dict(action_status='true',percent_state='known',spend_state='known',
                             cap_state='unknown',cap_period_state='unknown'),
                field_evidence={'cap':[{'line_id':'L1'}]}, ordinal=1)


def test_projection_preserves_all_nonempty_raw_fields_and_unknown():
    m=module();f=fact();p=m.project(f)
    assert p['fields']=={k:v for k,v in f['fields'].items() if v not in ('',None)}
    assert p['states']['cap_state']=='unknown'
    assert 'cap_krw' not in p and 'derived' not in p
    assert p['fields']['cap'].startswith('할인받기 전 이용금액 기준')


def test_json_text_identical_projection_with_multiline_values():
    m=module();p=m.project(fact())
    text=m.render_text(p)
    assert m.parse_text(text)==p
    assert '커피\n  카페' in text and '백화점 제외' in text


def test_alias_roundtrip_does_not_change_business_strings():
    m=module();f=fact();f['fields']['target']='owner/A original/path.json'
    alias,reverse=m.alias_records([f])
    assert alias[0]['fields']==f['fields']
    assert alias[0]['card_key']=='C1' and alias[0]['fact_id']=='F1'
    assert alias[0]['source_relative_path']=='P1'
    assert m.restore_records(alias,reverse)==[f]


def test_mismatched_owner_and_mutated_raw_field_rejected():
    m=module();f=fact();bad=copy.deepcopy(f);bad['fields']['value']='99'
    with pytest.raises(ValueError):m.assert_sql_fields(bad, f['fields'])
    with pytest.raises(ValueError):m.build_payload('질문', [dict(card_key='other',card_name='X',issuer='X')],
        [dict(card_key=f['card_key'],text='본문',origin='structured')], [f], 'B')


def test_u_prefix_and_citations_unchanged_all_formats():
    m=module();f=fact();cards=[{k:f[k] for k in ('card_key','card_name','issuer')}]
    evidence=[dict(card_key=f['card_key'],text='기존 본문 그대로\n두 번째 줄'),
              dict(card_key=f['card_key'],text='old structured',origin='structured')]
    for arm in 'ABC':
        p,reverse=m.build_payload('원질문',cards,evidence,[f],arm)
        assert p['evidence'][0]==dict(evidence_id='e1',card_ref='C1',text=evidence[0]['text'])
        assert p['evidence'][1]['evidence_id']=='e2'
        assert p['cards']==[dict(card_ref='C1',card_name='A카드',issuer='발급사')]
        assert reverse['cards']['C1']=='owner/A'


def test_missing_raw_field_and_unknown_arm_rejected():
    m=module();f=fact();del f['fields']['exceptions']
    with pytest.raises(ValueError):m.project(f)
    with pytest.raises(ValueError):m.build_payload('',[],[],[],'D')


def test_wire_serialization_stable_after_sorted_journal_roundtrip():
    import json
    m=module();payload={'z':{'b':2,'a':1},'a':'질문'}
    restored=json.loads(json.dumps(payload,sort_keys=True))
    assert m.compact(restored)==m.compact(payload)


@pytest.fixture(scope='module')
def runtime():
    path=Path(__file__).with_name('72_presentation_runtime.py')
    spec=importlib.util.spec_from_file_location('runtime72_regression',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def test_actual_normalized_input_bound_for_both_attempt_prompts(runtime):
    from pickcardu_rag.answering import _generated_answer_model
    m=runtime;j=m.read(m.BASE/'jobs.json')['records'][0]
    api=m.Cache(72,'answer',j['identity'],allow_network=False)
    for prompt in m.read(m.BASE/'prompts.json'):
        kw=dict(model='gpt-6-luna',input=[],instructions=prompt,text_format=_generated_answer_model(len(j['evidence'])))
        value=api.check_contract(kw)
        assert api.normalized(value)['input']==value['input']
    with pytest.raises(RuntimeError,match='prompt changed'):
        api.check_contract({**kw,'instructions':'different prompt'})


def test_288_attempt_reservation_covers_every_logical_twice(runtime):
    from decimal import Decimal
    m=runtime;r=m.read(m.BASE/'cost_reservation.json');jobs=m.read(m.BASE/'jobs.json')['records']
    assert len(jobs)==len(r['records'])==144 and r['maximum_physical_requests']==288
    assert {j['identity'] for j in jobs}=={v['identity'] for v in r['records']}
    assert sum(Decimal(v['per_physical_upper_usd'])*2 for v in r['records'])==Decimal(r['maximum_new_usd'])
    assert Decimal(r['starting_budget_debit_usd'])+Decimal(r['maximum_new_usd'])<=8


def test_resume_validates_actual_raw_request_not_only_checkpoint_seal(runtime,tmp_path,monkeypatch):
    import json
    import shutil
    from pickcardu_rag.answering import _generated_answer_model,_restore_generated_answer
    m=runtime;original=m.BASE
    j=m.read(original/'jobs.json')['records'][0];manifest=m.read(original/'execution_manifest.json')
    contract=m.read(original/'answer_payload_contract.json');schema=_generated_answer_model(len(j['evidence']))
    for name in ('payloads.json','execution_manifest.json'):
        shutil.copyfile(original/name,tmp_path/name)
    monkeypatch.setattr(m,'BASE',tmp_path)
    api=m.Cache(72,'answer',j['identity'],allow_network=False)
    kw=dict(model='gpt-6-luna',reasoning={'effort':'medium'},input=[],instructions=m.read(original/'prompts.json')[0],
            text_format=schema,tools=[],store=False,max_output_tokens=2400)
    normalized=api.normalized(kw);key=api.key(kw)
    request={k:v for k,v in normalized.items() if k not in ('text_format','timeout')};request['schema']=schema.model_json_schema()
    parsed=dict(answer_status='answered',answer_text='근거에 따른 답변',recommendations=[],claims=[dict(text='근거에 따른 주장',citations=['e1'])])
    generated=schema.model_validate(parsed);answer=_restore_generated_answer(generated,j['evidence']).model_dump(mode='json')
    raw=dict(status='ok',request=request,usage={},raw_response={'usage':{}},parsed=generated.model_dump(mode='json'),experiment_identity=j['identity'])
    row=dict(status='ok',answer=answer,job_sha256=m.sha(j),manifest_sha256=m.sha(manifest),
             payload_sha256=contract['input_sha256_by_identity'][j['identity']],events=[dict(key=key)])
    def fixture_write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False))
    fixture_write(tmp_path/'api_cache'/(key+'.json'),raw)
    fixture_write(tmp_path/'answers'/(j['identity']+'.seal.json'),dict(row_sha256=m.sha(row)))
    assert m.validate_saved(row,j,manifest,contract)
    raw['request']['input']=[dict(role='user',content='tampered actual input')]
    fixture_write(tmp_path/'api_cache'/(key+'.json'),raw)
    with pytest.raises(RuntimeError,match='actual provider request'):
        m.validate_saved(row,j,manifest,contract)


@pytest.mark.parametrize('gate',['missing','pending'])
def test_unaccepted_gate_prevents_actual_provider_fetch(runtime,tmp_path,monkeypatch,gate):
    import json
    import shutil
    from pickcardu_rag.answering import _generated_answer_model
    m=runtime;original=m.BASE;j=m.read(original/'jobs.json')['records'][0]
    shutil.copyfile(original/'payloads.json',tmp_path/'payloads.json')
    if gate=='pending':
        name='notebooks/notes/72_input_presentation_plan.md'
        (tmp_path/'pre_paid_acceptance.json').write_text(json.dumps(dict(status='pending',
            relation_review_accepted=False,independent_code_review_accepted=False,
            files_sha256={name:m.digest(m.ROOT/name)})))
    monkeypatch.setattr(m,'BASE',tmp_path)
    api=m.Cache(72,'answer',j['identity'],allow_network=True)
    def forbidden_fetch(kwargs):raise AssertionError('Provider was called before accepted gate')
    monkeypatch.setattr(api,'fetch',forbidden_fetch)
    kwargs=dict(model='gpt-6-luna',input=[],instructions=m.read(original/'prompts.json')[0],
                text_format=_generated_answer_model(len(j['evidence'])),tools=[],store=False,max_output_tokens=2400)
    with pytest.raises((FileNotFoundError,RuntimeError),match='pre_paid_acceptance|not accepted'):
        api.parse(**kwargs)
    assert api.events==[]


@pytest.mark.parametrize('changed',['source','acceptance'])
def test_reused_reservation_rechecks_source_and_acceptance(runtime,tmp_path,monkeypatch,changed):
    import json
    import shutil
    m=runtime;original=m.BASE
    for name in ('cost_reservation.json','execution_manifest.json'):
        shutil.copyfile(original/name,tmp_path/name)
    name='notebooks/notes/72_input_presentation_plan.md'
    if changed=='source':
        manifest=m.read(tmp_path/'execution_manifest.json');manifest['source_sha256'][name]='wrong digest'
        (tmp_path/'execution_manifest.json').write_text(json.dumps(manifest))
    else:
        (tmp_path/'pre_paid_acceptance.json').write_text(json.dumps(dict(files_sha256={name:'wrong digest'})))
    monkeypatch.setattr(m,'BASE',tmp_path)
    with pytest.raises(ValueError,match='Preserved file changed'):
        m.prepare()
