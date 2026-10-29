"""Offline regressions for prose-only and comparison-card audit closure."""
import copy
import importlib.util
from pathlib import Path
import pytest


def module():
    path=Path(__file__).with_name('62_validate_answer_prose.py')
    spec=importlib.util.spec_from_file_location('prose62',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def case():
    m=module();text='A 할인. B 조건 확인 불가.'
    job={'result':{'cards':[{'card_key':'A'},{'card_key':'B'}],
        'evidence':[{'card_key':'A','text':'A 할인'},{'card_key':'B','text':'B 조건 미기재'}]}}
    answer={'input_sha256':'input','answer':{'answer_text':text,'claims':[], 'recommendations':[]}}
    fact={'condition_id':'f','source_status':'verified','input_status':'supported'}
    pre={'card_reviews':[{'card_key':k,'source_pdf_sha256':k,'facts':[copy.deepcopy(fact)]}for k in ('A','B')]}
    review={'material_card_audits':[],'conditions':[], 'prose_audit':{
        'answer_text_sha256':m.digest(text),'reviewed_by':'manual','reason':'본문 전수 확인',
        'segments':[{'start':0,'end':6,'card_key':'A','reason':'할인 설명'},
                    {'start':6,'end':len(text),'card_key':'B','reason':'비교 설명'}],
        'card_checks':[]}}
    for key,role,quote in [('A','explanation','A 할인'),('B','explicitly_rejected_or_comparison','B 조건 확인 불가')]:
        review['prose_audit']['card_checks'].append({'card_key':key,'actual_role':role,
            'source_pdf_sha256':key,'reason':'실제 본문과 원문 대조','input_sha256':'input',
            'conditions':[{'card_key':key,'condition_id':'f','answer_status':'supported'if key=='A'else'absent',
                'answer_quotes':[quote]if key=='A'else[],'reason':'수동 대조'}],
            'assertion_checks':[{'answer_quotes':[quote], 'source_support':[{'sha256':key,'quote':'source'}],
                'input_status':'supported','input_quotes':['A 할인'if key=='A'else'B 조건 미기재'],
                'reason':'주장과 현재 입력을 구분'}]})
    return job,answer,review,pre


def run(value):
    m=module();job,answer,review,pre=value
    def frozen(*args,**kwargs):
        return {'input_supported_conditions':0,'grounded_reflected_conditions':0,
            'answer_reflection':None,'correct_without_input':0,'generation_omissions':[]}
    def by_card(body,candidates,spans):
        result={}
        for span in spans:result[span['card_key']]=result.get(span['card_key'],'')+body['answer_text'][span['start']:span['end']]
        return result
    def proof(p):
        if p.get('quote')!='source':raise ValueError('Invalid source proof')
    return m.score_answer(job,answer,review,pre,'uncertain',score_frozen=frozen,
        answer_by_card=by_card,validate_source_support=proof)


def test_prose_only_fact_is_scored_and_comparison_stays_audited():
    v=case();before=copy.deepcopy(v);score=run(v)
    assert score['input_supported_conditions']==1
    assert score['grounded_reflected_conditions']==1
    assert score['active_card_keys']==['A']
    assert score['comparison_card_keys']==['B']
    assert v==before


@pytest.mark.parametrize('defect', ['missing_card','missing_comparison_condition','uncovered_text','overlap',
    'stale_text','wrong_pdf','fabricated_input','missing_assertion','stale_condition_reference'])
def test_prose_audit_rejects_missing_or_stale_closure(defect):
    v=case();r=v[2]['prose_audit']
    if defect=='missing_card':r['card_checks'].pop()
    if defect=='missing_comparison_condition':r['card_checks'][1]['conditions']=[]
    if defect=='uncovered_text':r['segments'][0]['end']=5
    if defect=='overlap':r['segments'][1]['start']=5
    if defect=='stale_text':r['answer_text_sha256']='old'
    if defect=='wrong_pdf':r['card_checks'][1]['assertion_checks'][0]['source_support'][0]['sha256']='A'
    if defect=='fabricated_input':r['card_checks'][1]['assertion_checks'][0]['input_quotes']=['전월 0원']
    if defect=='missing_assertion':r['card_checks'][1]['assertion_checks']=[]
    if defect=='stale_condition_reference':r['card_checks'][0].update(uses_existing_conditions=True,condition_review_sha256='old')
    with pytest.raises(ValueError):run(v)


def test_supported_prose_fact_cannot_use_no_source_assertion_exception():
    v=case();assertion=v[2]['prose_audit']['card_checks'][0]['assertion_checks'][0]
    assertion['source_support']=[]
    assertion['source_status']='not_asserted_only_current_input_insufficiency'
    with pytest.raises(ValueError):run(v)


def neutral_case(text, reviewed=True):
    value=case();m=module();job,answer,review,pre=value
    job['result']['cards'][0]['card_name']='CARD-A'
    answer['answer']['answer_text']=text
    neutral={'start':0,'end':len(text),'card_key':None,'reason':'중립 안내'}
    if reviewed:neutral['neutral_review']={'text_sha256':m.digest(text),'reviewed_by':'manual',
        'no_card_specific_fact_or_comparison':True,'reason':'카드별 사실이나 비교 주장이 없음'}
    review['prose_audit'].update(answer_text_sha256=m.digest(text),segments=[neutral],card_checks=[])
    return value


@pytest.mark.parametrize('text,reviewed', [('CARD-A는 할인돼요.',True),('모두 5% 할인돼요.',True),('신청 전 확인하세요.',False)])
def test_null_prose_cannot_hide_a_card_or_numeric_claim(text,reviewed):
    with pytest.raises(ValueError):run(neutral_case(text,reviewed))


def test_reviewed_non_card_notice_can_stay_neutral():
    score=run(neutral_case('신청 전 공식 상품설명서를 확인하세요.'))
    assert score['active_card_keys']==[]


def shared_case():
    value=case();m=module();job,answer,review,pre=value;text='A와 B는 5% 할인.'
    answer['answer']['answer_text']=text
    audit=review['prose_audit'];audit['answer_text_sha256']=m.digest(text)
    audit['segments']=[{'start':0,'end':3,'card_key':'A','reason':'첫 참가카드'},
        {'start':3,'end':len(text),'card_key':'B','reason':'두번째 카드와 공유 술어'}]
    for check in audit['card_checks']:
        for f in check['conditions']:f.update(answer_status='absent',answer_quotes=[])
        assertion=check['assertion_checks'][0];assertion['answer_quotes']=[text]
        assertion['shared_predicate_context']={'start':0,'end':len(text),'text_sha256':m.digest(text),
            'card_keys':['A','B'],'reason':'두 카드가 동일 술어에 연결됨을 각각 원문/입력 대조'}
    return value


def test_shared_predicate_requires_each_participant_source_and_input_audit():
    score=run(shared_case())
    assert score['audited_assertion_count']==2


def test_shared_predicate_cannot_borrow_an_unreviewed_other_card():
    value=shared_case()
    del value[2]['prose_audit']['card_checks'][1]['assertion_checks'][0]['shared_predicate_context']
    with pytest.raises(ValueError):run(value)
