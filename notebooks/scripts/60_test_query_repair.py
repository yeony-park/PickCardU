"""Regression boundaries for the experiment-only literal/scope repair."""
import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location('query60_tested', Path(__file__).with_name('60_query_repair.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('text', ['EV(전기차) 충전소', '전기차, 수소차 충전'])
def test_explicit_surface_form_keeps_electric_vehicle_target(text):
    assert module().target_matches('전기차 충전', text)


@pytest.mark.parametrize('text', ['전기차 충전소', '철도 할인'])
def test_train_is_not_electric_vehicle_or_a_new_rail_synonym(text):
    assert not module().target_matches('기차', text)


def test_parenthesized_exclusion_does_not_become_a_positive_target():
    m = module()
    assert not m.target_matches('스타벅스', '커피(스타벅스 제외)')
    assert m.target_matches('커피', '커피(스타벅스 제외)')


def catalog():
    return [
        dict(document_id='woori/d4', identity=dict(card_name='D4카드의정석 II', issuer_name='우리카드')),
        dict(document_id='shinhan/dp', identity=dict(card_name='신한카드 Discount Plan+', issuer_name='신한카드')),
        dict(document_id='samsung/global', identity=dict(card_name='삼성 iD GLOBAL 카드', issuer_name='삼성카드')),
        dict(document_id='ibk/pass', identity=dict(card_name='K-패스 (신용)', issuer_name='IBK기업은행')),
        dict(document_id='other/pass', identity=dict(card_name='K-패스 (신용)', issuer_name='다른카드')),
    ]


@pytest.mark.parametrize('question,expected', [
    ('우리 D4카드의정석Ⅱ 실적 알려줘', ['woori/d4']),
    ('신한 Discount Plan+ 넷플릭스 할인', ['shinhan/dp']),
    ('삼성 iD GLOBAL의 구독 혜택', ['samsung/global']),
    ('우리카드에서 병원 할인 추천해줘', ['woori/d4']),
    ('IBK K-패스(신용)으로 코레일 앱 결제', ['ibk/pass']),
])
def test_catalog_backed_unique_scope(question, expected):
    scope = module().original_scope(question, catalog())
    assert scope['card_keys'] == expected
    assert not scope['clarify']


def test_ambiguous_catalog_name_requires_clarification():
    scope = module().original_scope('K-패스 (신용) 카드 알려줘', catalog())
    assert scope['clarify'] and scope['card_keys'] == []


def test_general_recommendation_has_no_invented_scope():
    assert module().original_scope('생활비에 두루 쓸 카드 추천해줘', catalog())['card_keys'] is None


def test_issuer_prefix_is_not_a_substring_of_a_different_word():
    assert module().original_scope('우리나라에서 쓸 카드 추천해줘', catalog())['issuer'] is None


def test_one_card_is_not_the_hana_issuer_and_our_family_is_not_woori():
    docs = catalog() + [dict(document_id='hana/one', identity=dict(card_name='하나 원스토어 1', issuer_name='하나카드'))]
    for question in ['커피 할인되는 카드 하나 추천해줘.', '우리 가족이 일상에서 쓸 카드가 필요해.']:
        scope = module().original_scope(question, docs)
        assert scope['issuer'] is None and scope['card_keys'] is None
    assert module().original_scope('하나 원스토어 1 카드 혜택', docs)['card_keys'] == ['hana/one']
