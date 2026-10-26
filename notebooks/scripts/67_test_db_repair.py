"""Conservative, same-row spend parsing; no network or original DB writes."""
import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('db67_under_test', Path(__file__).with_name('67_db_repair.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def fact(text, money, *, state='unknown', evidence=None):
    fragments = [{'line_id':'P0001-L0002','fragment':text if evidence is None else evidence}]
    raw = {'condition':text, 'target':'커피', 'field_evidence':{'target':[{'line_id':'P0001-L0001','fragment':'커피'}], 'condition':fragments},
           'relation_scope':{'scope_type':'bounded_block','line_ids':['P0001-L0001','P0001-L0002']},
           'typed_normalization':{'condition':[{'kind':'KRW','decimal':str(n)} for n in money], 'condition_operators':['gte']}}
    return {'fact_id':'fixture','document_id':'issuer/A','fields':{'condition':text,'target':'커피'},
            'raw_fact':raw,'field_evidence':raw['field_evidence'],'relation_scope':raw['relation_scope'],
            'typed_normalization':raw['typed_normalization'],
            'derived':{'spend_state':state,'spend_threshold_krw':None,'spend_operator':None,'spend_reason':'old','percent':10}}


@pytest.mark.parametrize('text', ['전월실적 3만원 이상', '전월 실적 30,000원 이상', '전월 30000원 이상'])
def test_money_surface_forms_agree_on_the_same_threshold(text):
    assert D.spend_patch(fact(text,[30000]))['updates']['spend_threshold_krw'] == 30000


def test_transaction_amount_does_not_replace_the_connected_previous_spend():
    patch = D.spend_patch(fact('1만원 이상 결제건에 한함 전월 실적이 15만원 이상인 경우', [10000,150000]))
    assert patch['updates']['spend_threshold_krw'] == 150000


@pytest.mark.parametrize('text,money', [
    ('전월실적 40만원 이상~80만원 미만',[400000,800000]),
    ('신규 발급 다음 달까지 전월실적 30만원 이상 조건을 면제',[300000]),
    ('전월실적 30만원 미만에도 제공',[300000]),
    ('전월실적 30만원 초과',[300000]),
    ('이번 달 이용액 30만원 이상',[300000]),
    ('전전월 이용액 30만원 이상',[300000]),
    ('전월실적 30만원 이상 또는 최근3개월 매달20만원 이상',[300000,200000]),
    ('1차년도 전월50만원 이상, 2차년도 이후 연600만원 이상',[500000,6000000]),
    ('전월50만원 이상/80만원 이상 구간별 혜택',[500000,800000]),
    ('전월실적 0원 이상',[0]),
    ('전월 프랜드카드로 30만원 이상 결제금액 회수 시',[300000]),
    ('실적 조건 없음',[]),
    ('',[]),
])
def test_unresolved_or_different_relations_are_not_converted_to_a_regular_threshold(text,money):
    assert D.spend_patch(fact(text,money)) is None


def test_raw_money_must_agree_with_typed_normalization_and_owning_evidence():
    assert D.spend_patch(fact('전월실적 30만원 이상',[30000])) is None
    assert D.spend_patch(fact('전월실적 30만원 이상',[300000],evidence='다른 혜택 조건')) is None
    f=fact('전월실적 30만원 이상',[300000]);f['relation_scope']['line_ids']=['P0001-L0001']
    assert D.spend_patch(f) is None


def test_existing_known_zero_and_not_applicable_states_are_immutable():
    for state in ['known','not_applicable']:
        f=fact('전월실적 30만원 이상',[300000],state=state);old=copy.deepcopy(f)
        assert D.spend_patch(f) is None
        assert f == old
