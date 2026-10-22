"""Missing manual reviews and repeat denominators must never become PASS."""
import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('parser56_eval',Path(__file__).with_name('56_parser_evaluation.py'))
E=importlib.util.module_from_spec(spec);spec.loader.exec_module(E)


def test_equivalent_order_does_not_change_filter_identity_but_and_or_does():
    a={'targets':['커피'],'action':'discount'};b={'targets':['넷플릭스'],'action':'discount'}
    plan=dict(groups=[[a,b]],unsupported_filters=[],requested_fields=['cap'])
    same={**plan,'groups':[[b,a]],'requested_fields':['frequency']}
    either={**plan,'groups':[[a],[b]]}
    assert E.filter_key(plan)==E.filter_key(same)
    assert E.filter_key(plan)!=E.filter_key(either)


def test_no_review_is_an_error_and_transport_failure_is_separate():
    row=dict(source_query_id='H01',form='short',parser_version='old',parser=dict(repeat=1,status='ok',plan={'groups':[],'unsupported_filters':[]}))
    with pytest.raises(ValueError,match='manual review'):
        E.adjudicate(row,{},'gold')
    failed={**row,'parser':dict(repeat=1,status='failed',plan=None)}
    result=E.adjudicate(failed,{},'gold')
    assert result['transport_or_format_failure'] and result['strict_correct'] is False
    assert result['schema_filter_correct_given_output'] is None
    assert result['unrepresentable_qualifier_completeness'] is None


def test_question_macro_does_not_treat_repeats_as_independent_questions():
    from itertools import product
    rows=[dict(query_id=qid,strict_correct=qid=='q1',sensitive_correct=qid=='q1',form=form,
        parser_version=version,repeat=repeat,transport_or_format_failure=False)
        for qid,version,form,repeat in product(['q1','q2'],['old','new'],['short','detailed'],[1,2,3])]
    result=E.aggregate(rows,query_ids=['q1','q2'])
    assert result['old']['question_count']==2
    assert result['old']['strict_question_macro']==.5
    with pytest.raises(ValueError,match='matrix'):
        E.aggregate(rows[:-1],query_ids=['q1','q2'])
    with pytest.raises(ValueError,match='matrix'):
        E.aggregate([r for r in rows if r['parser_version']=='old'],query_ids=['q1','q2'])
    with pytest.raises(ValueError,match='Duplicate query IDs'):
        E.aggregate([r for r in rows if r['query_id']=='q1'],query_ids=['q1','q1'])
    with pytest.raises(ValueError,match='reviewed444'):
        E.select(rows,query_ids=['q1','q2'],review_completion={},gold_payload={},sensitivity_contract={},independent_review={})


def test_complete_parser_matrix_still_cannot_select_without_bound_review_completion():
    from itertools import product
    qids=['H04','H16']+['q'+str(i) for i in range(35)]
    gold=dict(records=[dict(query_id=qid) for qid in qids])
    sensitivity=dict(status='independent_review_after_freeze_before_manual_scoring',
        frozen_question_pairs_sha256=E.sha(gold),original_gold_modified=False,ambiguous={'H04':{},'H16':{}})
    rows=[dict(query_id=qid,strict_correct=True,sensitive_correct=True,form=form,
        parser_version=version,repeat=repeat,transport_or_format_failure=False,gold_sha256=E.sha(gold),score_scope=E.SCORE_SCOPE)
        for qid,version,form,repeat in product(qids,['old','new'],['short','detailed'],[1,2,3])]
    independent=dict(reviewer='registered Codex reviewer',independent_of_main=True,independent_human_review=False,
        status='accepted',adjudications_sha256=E.sha(rows),blocking_count=0,major_count=0)
    accepted=dict(status='accepted_after_independent_review',adjudications_sha256=E.sha(rows),query_ids=sorted(qids),reviewed_by='main Codex',
        gold_sha256=E.sha(gold),sensitivity_contract_sha256=E.sha(sensitivity),independent_review_sha256=E.sha(independent))
    kwargs=dict(query_ids=qids,review_completion=accepted,gold_payload=gold,sensitivity_contract=sensitivity,independent_review=independent)
    selected=E.select(rows,**kwargs)
    assert selected['parser_version']=='old'
    assert selected['summary']['old']['unambiguous_question_count']==35
    for changed in [{**accepted,'status':'partial'}, {**accepted,'adjudications_sha256':'wrong'}]:
        with pytest.raises(ValueError,match='reviewed444'):
            E.select(rows,**{**kwargs,'review_completion':changed})
    with pytest.raises(ValueError,match='reviewed444'):
        E.select(rows[:-1],**kwargs)
    with pytest.raises(ValueError,match='sensitivity contract'):
        E.select(rows,**{**kwargs,'sensitivity_contract':{**sensitivity,'ambiguous':{'H04':{}}}})
    with pytest.raises(ValueError,match='independent agent review'):
        E.select(rows,**{**kwargs,'independent_review':{**independent,'reviewer':'main Codex'}})
