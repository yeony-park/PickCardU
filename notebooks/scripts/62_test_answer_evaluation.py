import copy
import importlib.util
from pathlib import Path

import pytest


def module():
    spec=importlib.util.spec_from_file_location('evaluate62',Path(__file__).with_name('62_evaluate_answers.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def fixture():
    m=module();j=dict(identity='N01:p:U:r1',query_id='N01',profile='p',mode='U',repeat=1,
        result=dict(cards=[dict(card_key='a')],evidence=[dict(card_key='a',text='커피10% 전월30만원')]))
    a=dict(status='ok',input_sha256='i',answer=dict(answer_text='커피10% 전월30만원',recommendations=[dict(card_key='a')],claims=[]))
    pre=dict(card_reviews=[dict(card_key='a',relevant=True,facts=[dict(condition_id='rate',source_status='verified',input_status='supported'),dict(condition_id='spend',source_status='verified',input_status='supported')])])
    r=dict(identity=j['identity'],answer_row_sha256=m.digest(a),retrieval_result_sha256=m.digest(j['result']),
        retrieval_review_sha256=m.digest(pre),actual_input_sha256='i',reviewed_by='manual',reason='source checked',verdict='PASS',
        critical_error_ids=[],false_certainty=False,material_card_audits=[dict(card_key='a',actual_role='recommendation',reason='valid')],
        conditions=[dict(card_key='a',condition_id='rate',answer_status='supported',answer_quotes=['커피10%'],reason='correct'),dict(card_key='a',condition_id='spend',answer_status='supported',answer_quotes=['전월30만원'],reason='correct')])
    return j,a,pre,r


def score(j,a,p,r):
    return module().score_answer(j,a,r,p,'explicit',answer_by_card=lambda a,c,s:{'a':a.get('answer_text','')})


def test_input_conditions_are_not_automatically_counted_as_answered():
    j,a,p,r=fixture();assert score(j,a,p,r)['answer_reflection']==1
    r['conditions'][1].update(answer_status='absent',answer_quotes=[]);r['verdict']='PARTIAL'
    out=score(j,a,p,r);assert out['answer_reflection']==.5 and not out['passed']


def test_technical_failure_stays_in_quality_denominator():
    j,a,p,r=fixture();a.update(status='failed',answer=None);r.update(answer_row_sha256=module().digest(a),verdict='PASS')
    with pytest.raises(ValueError):score(j,a,p,r)
    r.update(verdict='TECHNICAL_FAILURE',material_card_audits=[],conditions=[])
    assert score(j,a,p,r)['passed']is False


def test_material_condition_cannot_be_dropped_from_adjudication():
    j,a,p,r=fixture();r['conditions'].pop()
    with pytest.raises(ValueError):score(j,a,p,r)


def test_cross_card_answer_quote_and_stale_bindings_are_rejected():
    j,a,p,r=fixture();r['conditions'][0]['answer_quotes']=['not actually answered']
    with pytest.raises(ValueError):score(j,a,p,r)
    j,a,p,r=fixture();r['answer_row_sha256']='stale'
    with pytest.raises(ValueError):score(j,a,p,r)


def test_source_correct_without_input_is_not_grounded_reflection():
    j,a,p,r=fixture();p['card_reviews'][0]['facts'][1]['input_status']='absent';r['retrieval_review_sha256']=module().digest(p)
    out=score(j,a,p,r);assert out['answer_reflection']==1 and out['correct_without_input']==1


def test_answer_aggregation_requires_all_repeats_and_frozen_groups():
    m=module();j,a,p,r=fixture();first=score(j,a,p,r);rows=[]
    for rep in(1,2,3):rows.append({**first,'identity':f'N01:p:U:r{rep}','repeat':rep})
    kwargs=dict(group_by_query={'N01':'explicit'},profiles=['p'],modes=['U'])
    assert m.aggregate(rows,**kwargs)[0]['pass_macro']==1
    for bad in [rows[:-1],rows+rows[:1],[rows[0],rows[1],{**rows[2],'group':'general'}]]:
        with pytest.raises(ValueError):m.aggregate(bad,**kwargs)


def selection_fixture():
    source=[];answers=[];rows=[]
    for mode,complete in [('U',.5),('SEQ_BASE',.75),('SEQ_REPAIRED',.8)]:
        for group in ('explicit','general','uncertain'):
            source.append(dict(profile='p',mode=mode,group=group,hit_at3_macro=1,mrr_at3_macro=1,complete_evidence_macro=complete))
        answers.append(dict(profile='p',mode=mode,pass_macro=.7,subgroups={g:dict(pass_macro=.7)for g in ['explicit','general']}))
        for q,group in [('N01','explicit'),('G01','general'),('N31','uncertain')]:
            rows.append(dict(identity=f'{q}:p:{mode}:r1',query_id=q,profile='p',mode=mode,repeat=1,group=group,false_certainty=False,critical_error_ids=[]))
    return source,answers,rows


def select(e,a,r):
    return module().select(e,a,r,profiles=['p'],group_by_query={'N01':'explicit','G01':'general','N31':'uncertain'},repeats=(1,))


def test_selection_requires_an_explicit_complete_question_contract():
    m=module();e,a,r=selection_fixture()
    with pytest.raises(ValueError):
        m.select(e,a,r,profiles=['p'])


def test_raw_answer_gain_cannot_hide_generic_or_baseline_regression():
    e,a,r=selection_fixture();assert select(e,a,r)[0]['eligible']
    a[-1]['pass_macro']=.9;a[-1]['subgroups']['general']['pass_macro']=.6
    assert not select(e,a,r)[0]['eligible']
    e,a,r=selection_fixture();next(x for x in e if x['mode']=='SEQ_REPAIRED'and x['group']=='explicit')['complete_evidence_macro']=.7
    assert not select(e,a,r)[0]['eligible']


def test_equal_evidence_to_u_is_not_the_required_strict_improvement():
    e,a,r=selection_fixture();e[0]['complete_evidence_macro']=.8
    assert not select(e,a,r)[0]['eligible']


def test_new_wrong_benefit_relation_fails_even_with_a_better_pass_average():
    e,a,r=selection_fixture();r[-1]['critical_error_ids']=['relation:cardA:wrong_cap']
    assert not select(e,a,r)[0]['eligible']


def test_selection_rejects_missing_rows_or_duplicate_summaries():
    e,a,r=selection_fixture()
    with pytest.raises(ValueError):select(e,a,r[:-1])
    with pytest.raises(ValueError):select(e+e[:1],a,r)
