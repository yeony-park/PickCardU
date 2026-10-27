"""Evaluation boundary regressions; these do not decide answer semantics."""
import importlib.util
from pathlib import Path

import pytest


def module():
    p=Path(__file__).with_name('72_presentation_evaluation.py')
    spec=importlib.util.spec_from_file_location('eval72_test',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def fixture():
    m=module();row=dict(identity='Q:p:A:r1',query_id='Q',question='커피 카드?',profile='p',arm='A',repeat=1,status='ok',
        answer=dict(answer_text='커피 할인 카드입니다.',recommendations=[],claims=[dict(text='숨은 추가 내용')]))
    contract=dict(query_id='Q',question='커피 카드?',inherited_direct_requirements=[dict(id='Q.D1')])
    payload=dict(evidence=[dict(evidence_id='e1',card_ref='C1',text='커피 할인')]);catalog={'src':dict(card_key='A',source_status='verified',literal_quotes=['원문 커피 할인'])}
    row['payload_sha256']=m.sha([dict(role='user',content=__import__('json').dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')))])
    review=dict(identity=row['identity'],answer_row_sha256=m.sha(row),visible_sha256=m.sha(m.surfaces(row['answer'])),
        reviewed_by='manual',reason='manual source comparison',requirements=[dict(id='Q.D1',status='satisfied',reason='visible direct answer',
        quotes=[dict(surface='answer_text',quote='커피 할인')])],assertions=[dict(surface='answer_text',quote='커피 할인 카드입니다.',
        status='correct',reason='source checked',relation_review='same coffee benefit manually checked',source_refs=['src'],source_quotes=[dict(ref='src',quote='커피 할인')],
        input_support='supported',input_quotes=[dict(evidence_id='e1',quote='커피 할인')])])
    return m,row,contract,catalog,payload,review


def test_unasked_spend_omission_not_added_as_requirement():
    m,r,c,s,p,v=fixture();assert m.validate_review(v,r,c,s,p,{'C1':'A'})['passed']


def test_source_correct_and_input_unsupported_are_separate():
    m,r,c,s,p,v=fixture();v['assertions'][0]['input_support']='unsupported';v['assertions'][0]['input_quotes']=[]
    out=m.validate_review(v,r,c,s,p,{'C1':'A'});assert out['passed'] and not out['grounded']


def test_hidden_claim_cannot_rescue_missing_visible_direct_answer():
    m,r,c,s,p,v=fixture();v['requirements'][0].update(status='missing',quotes=[])
    assert not m.validate_review(v,r,c,s,p,{'C1':'A'})['passed']


@pytest.mark.parametrize('changed',['source_quote','input_quote','visible_quote','dropped_requirement','dropped_assertion'])
def test_false_quote_or_incomplete_review_rejected(changed):
    m,r,c,s,p,v=fixture()
    if changed=='source_quote':v['assertions'][0]['source_quotes'][0]['quote']='absent'
    if changed=='input_quote':v['assertions'][0]['input_quotes'][0]['quote']='absent'
    if changed=='visible_quote':v['assertions'][0]['quote']='absent'
    if changed=='dropped_requirement':v['requirements']=[]
    if changed=='dropped_assertion':v['assertions']=[]
    with pytest.raises(ValueError):m.validate_review(v,r,c,s,p,{'C1':'A'})


def test_new_error_category_rejects_average_tie():
    m=module();base=dict(pass_by_question={'Q':3},direct=3,accurate=3,confirmed_wrong_answers=0,
                       error_categories_by_question={'Q':[]},alias_leak=0,clean7_passed=3)
    candidate={**base,'error_categories_by_question':{'Q':['benefit_transfer']}}
    assert m.candidate_guards(base,candidate)['eligible'] is False


def test_old_high_level_note_cannot_default_to_grounded(tmp_path,monkeypatch):
    import json
    m=module();original=m.BASE;j=m.read(original/'jobs.json')['records'][0]
    row=m.read(original/'answers'/(j['identity']+'.json'))
    note=dict(direct=True,accurate=True,grounded=True,reviewed_by='manual',reason='high level only',answer_row_sha256=m.sha(row))
    files={'manual_review_notes.json':{'records':{j['identity']:note}},'jobs.json':{'records':[j]},
        'question_contract.json':m.read(original/'question_contract.json'),
        'payloads.json':m.read(original/'payloads.json'),'audit_sidecars.json':m.read(original/'audit_sidecars.json')}
    for name,value in files.items():(tmp_path/name).write_text(json.dumps(value,ensure_ascii=False))
    path=tmp_path/'answers'/(j['identity']+'.json');path.parent.mkdir();path.write_text(json.dumps(row,ensure_ascii=False))
    monkeypatch.setattr(m,'BASE',tmp_path)
    with pytest.raises(ValueError,match='explicit surface/requirement'):
        m.bind_manual_notes()


@pytest.mark.parametrize('change',['repeat','query','profile','arm','duplicate'])
def test_exact_matrix_rejects_count_preserving_replacement(change):
    m=module();assert hasattr(m,'validate_matrix'),'Exact matrix validator is missing'
    rows=[dict(query_id=q,profile=p,arm=a,repeat=r,identity=f'{q}:{p}:{a}:r{r}')
          for q in ('Q1','Q2') for p in ('card_page_section_benefit','parent_child_bundle') for a in 'ABC' for r in (1,2,3)]
    assert m.validate_matrix(rows,{'Q1','Q2'})
    if change=='repeat':rows[0]['repeat']=4
    if change=='query':rows[0]['query_id']='wrong'
    if change=='profile':rows[0]['profile']='wrong'
    if change=='arm':rows[0]['arm']='D'
    if change=='duplicate':rows[0]=dict(rows[1])
    with pytest.raises(ValueError):m.validate_matrix(rows,{'Q1','Q2'})


def test_other_card_source_quote_cannot_certify_surface():
    m,r,c,s,p,v=fixture();s['src']['card_key']='B'
    with pytest.raises(ValueError,match='owner'):
        m.validate_review(v,r,c,s,p,{'C1':'A'})


def test_recommendation_cannot_borrow_another_card_input():
    import copy
    m,r,c,s,p,v=fixture()
    r['answer']['recommendations']=[dict(card_key='A',reason='커피 할인 카드입니다.')]
    a=copy.deepcopy(v['assertions'][0]);a['surface']='recommendations.0.reason';v['assertions'].append(a)
    p['evidence'][0]['card_ref']='C2'
    r['payload_sha256']=m.sha([dict(role='user',content=__import__('json').dumps(p,ensure_ascii=False,sort_keys=True,separators=(',',':')))])
    v['answer_row_sha256']=m.sha(r);v['visible_sha256']=m.sha(m.surfaces(r['answer']))
    with pytest.raises(ValueError,match='Input quote owner'):
        m.validate_review(v,r,c,s,p,{'C1':'A','C2':'B'})


def test_same_card_context_with_unverified_relation_stays_uncertain():
    m,r,c,s,p,v=fixture();v['assertions'][0].update(input_support='uncertain',relation_review='same card, benefit relationship unverified')
    assert not m.validate_review(v,r,c,s,p,{'C1':'A'})['grounded']


@pytest.mark.parametrize('status',['wrong','uncertain'])
@pytest.mark.parametrize('span',[None,'화면에 없는 주장'])
def test_problem_judgment_requires_literal_visible_span(status,span):
    m,r,c,s,p,v=fixture();assertion=v['assertions'][0];assertion['status']=status
    if status=='wrong':assertion.update(error_type='wrong_benefit_rate',error_quote=span)
    else:assertion.update(uncertainty_type='payment_scope_wording',uncertainty_quote=span)
    with pytest.raises(ValueError,match='literal visible span'):
        m.validate_review(v,r,c,s,p,{'C1':'A'})


@pytest.mark.parametrize('gate',['missing','pending'])
def test_uncached_request_gate_isolated_after_generation(tmp_path,monkeypatch,gate):
    """The frozen pre-paid test assumed an empty real cache; isolate both caches.

    Keep that historical source byte-identical. This post-run check tests the
    new-provider-request gate, not the accepted offline-cache replay path.
    """
    import json
    import shutil
    from pickcardu_rag.answering import _generated_answer_model
    m=module().runtime();original=m.BASE;job=m.read(original/'jobs.json')['records'][0]
    shutil.copyfile(original/'payloads.json',tmp_path/'payloads.json')
    if gate=='pending':
        name='notebooks/notes/72_input_presentation_plan.md'
        (tmp_path/'pre_paid_acceptance.json').write_text(json.dumps(dict(status='pending',
            relation_review_accepted=False,independent_code_review_accepted=False,
            files_sha256={name:m.digest(m.ROOT/name)})))
    monkeypatch.setattr(m,'BASE',tmp_path)
    api=m.Cache(72,'answer',job['identity'],allow_network=True)
    api.cache=tmp_path/'empty_api_cache';api.cache.mkdir()
    api.old_cache=tmp_path/'empty_prior_cache';api.old_cache.mkdir()
    def forbidden_fetch(kwargs):raise AssertionError('Provider called before accepted gate')
    monkeypatch.setattr(api,'fetch',forbidden_fetch)
    kwargs=dict(model='gpt-6-luna',input=[],instructions=m.read(original/'prompts.json')[0],
        text_format=_generated_answer_model(len(job['evidence'])),tools=[],store=False,max_output_tokens=2400)
    with pytest.raises((FileNotFoundError,RuntimeError),match='pre_paid_acceptance|not accepted'):
        api.parse(**kwargs)
    assert api.events==[] and not list(api.cache.iterdir()) and not list(api.old_cache.iterdir())
