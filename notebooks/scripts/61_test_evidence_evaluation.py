import copy
import importlib.util
from pathlib import Path

import pytest


def module():
    spec=importlib.util.spec_from_file_location('evaluate61',Path(__file__).with_name('61_evaluate_evidence.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def fixture():
    job=dict(identity='N01:p:U:r1',query_id='N01',profile='p',mode='U',repeat=1,question='커피',
        result=dict(cards=[dict(card_key='alternative',rank=1)],evidence=[dict(card_key='alternative',text='커피10% 전월30만원')]))
    m=module(); review=dict(identity=job['identity'],result_sha256=m.digest(job['result']),reviewed_by='manual',reason='checked',
        card_reviews=[dict(card_key='alternative',source_pdf_sha256='pdfA',relevant=True,reason='valid alternative',facts=[
            dict(condition_id='rate',source_status='verified',source_support=[{'sha256':'pdfA'}],input_status='supported',input_quotes=['커피10%']),
            dict(condition_id='spend',source_status='verified',source_support=[{'sha256':'pdfA'}],input_status='supported',input_quotes=['전월30만원'])])])
    return job,review


def score(job,review,group='explicit'):
    return module().score_retrieval(job,review,group,validate_source=lambda proof:None,source_pdf_sha_by_card={'alternative':'pdfA'})


def test_actual_alternative_card_can_have_complete_evidence():
    j,r=fixture();out=score(j,r)
    assert out['complete_evidence'] and out['hit_at3']==1 and out['mrr_at3']==1


def test_missing_condition_is_not_complete_even_when_card_hit_succeeds():
    j,r=fixture();r['card_reviews'][0]['facts'][1].update(input_status='absent',input_quotes=[])
    out=score(j,r);assert out['hit_at3']==1 and not out['complete_evidence']


def test_uncertain_relation_is_not_supported_by_cooccurring_numbers():
    j,r=fixture();r['card_reviews'][0]['facts'][1]['input_status']='ambiguous'
    assert not score(j,r)['complete_evidence']


def test_other_cards_conditions_cannot_be_combined():
    j,r=fixture();j['result']['evidence'][0]['card_key']='other';r['result_sha256']=module().digest(j['result'])
    with pytest.raises(ValueError):score(j,r)


def test_general_and_unconfirmable_questions_do_not_get_rank_gold():
    j,r=fixture();assert score(j,r,'general')['hit_at3'] is None
    assert score(j,r,'uncertain')['complete_evidence'] is None


def test_unbound_and_incomplete_manual_rows_are_rejected():
    j,r=fixture()
    for bad in [dict(r,result_sha256='stale'),dict(r,card_reviews=[]),dict(r,reviewed_by='')]:
        with pytest.raises(ValueError):score(j,bad)


def test_another_cards_source_pdf_is_not_a_condition_proof():
    j,r=fixture();r['card_reviews'][0]['facts'][0]['source_support'][0]['sha256']='otherPDF'
    with pytest.raises(ValueError):score(j,r)


def test_group_means_are_query_first_not_extra_weight_for_repeats():
    m=module();j,r=fixture();a=score(j,r);b=copy.deepcopy(a);b.update(identity='N01:p:U:r2',repeat=2,complete_evidence=False)
    c=copy.deepcopy(a);c.update(identity='N02:p:U:r1',query_id='N02',complete_evidence=False)
    d=copy.deepcopy(c);d.update(identity='N02:p:U:r2',repeat=2)
    out=m.aggregate([a,b,c,d],group_by_query={'N01':'explicit','N02':'explicit'},profiles=['p'],modes=['U'],repeats=[1,2])[0]
    assert out['complete_evidence_macro']==.25 and out['questions']==2


def test_incomplete_repeat_or_changed_group_never_becomes_a_macro():
    m=module();j,r=fixture();a=score(j,r);b=copy.deepcopy(a);b.update(identity='N01:p:U:r2',repeat=2)
    c=copy.deepcopy(a);c.update(identity='N01:p:U:r3',repeat=3)
    kwargs=dict(group_by_query={'N01':'explicit'},profiles=['p'],modes=['U'])
    assert m.aggregate([a,b,c],**kwargs)[0]['logical_rows']==3
    for bad in [[a,b],[a,b,{**c,'group':'general'}],[a,b,c,{**c,'identity':'extra'}],
                [a,b,{**c,'profile':'other'}],[a,b,{**c,'mode':'PAR'}],[a,b,{**c,'query_id':'unknown'}]]:
        with pytest.raises(ValueError):m.aggregate(bad,**kwargs)
