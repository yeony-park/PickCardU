"""Fixed boundary regression suite; TARGET_SCOPE_IMPL enables old-code RED replay."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE/file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


K = load('scope64_test_impl', os.environ.get('TARGET_SCOPE_IMPL', '64_scope_repair.py'))
OLD = load('scope64_test_old', '60_query_repair.py')
Q = load('scope64_test_query', '50_query_contract.py')
DB = ROOT/'notebooks/data/60_structured_retrieval_repair/db_v3'
DOCUMENTS = [json.loads(s) for s in (DB/'documents.jsonl').read_text().splitlines()]
FACTS = {r['fact_id']: r for r in map(json.loads, (DB/'facts.jsonl').read_text().splitlines())}


def issuer_keys(issuer):
    return sorted(d['document_id'] for d in DOCUMENTS if d['document_id'].split('/')[0] == issuer)


SCOPE_CASES = [
    ('롯데 시네마 할인되는 카드 추천해줘', None),
    ('롯데시네마 할인되는 카드 추천해줘', None),
    ('삼성 페이로 결제할 때 적립되는 카드 추천해줘', None),
    ('삼성페이로 결제할 때 적립되는 카드 추천해줘', None),
    ('현대 백화점 할인되는 카드 추천해줘', None),
    ('현대백화점 할인되는 카드 추천해줘', None),
    ('롯데 마트 할인되는 카드 추천해줘', None),
    ('롯데마트 할인되는 카드 추천해줘', None),
    ('롯데카드 중 영화 할인되는 카드 추천해줘', issuer_keys('lotte')),
    ('삼성 카드 중 커피 할인되는 카드 추천해줘', issuer_keys('samsung')),
    ('BC 중에서 커피 할인 카드 찾아줘', issuer_keys('BC')),
    ('우리카드 중에서 병원 할인 카드 찾아줘', issuer_keys('woori')),
    ('우리 카드 중에서 병원 할인 카드 찾아줘', issuer_keys('woori')),
    ('토스 신한카드 Mr.Life의 커피 시간 알려줘', ['shinhan/Shinhan_Toss_Mr.Life_20251231']),
    ('우리 D4카드의정석Ⅱ의 커피 횟수 알려줘', ['woori/Woori_D4_Classic_II']),
    ('그린카드 v2의 커피 조건 알려줘', ['BC/BC_Green_v2']),
    ('우리 생활비를 줄일 카드 찾아줘', None),
    ('커피 할인 카드 하나 추천해줘', None),
    ('국민 모두에게 유용한 카드 찾아줘', None),
    ('기업 직원이 일상에서 쓸 카드 찾아줘', None),
    ('롯데카드와 삼성카드 중 커피 카드 찾아줘', sorted(issuer_keys('lotte')+issuer_keys('samsung'))),
    ('우리카드와 하나카드 중 병원 카드 찾아줘', sorted(issuer_keys('woori')+issuer_keys('hana'))),
    ('롯데카드X-999의 커피 조건 알려줘', []),
    ('스카이패스카드의 조건을 알려줘', []),
]


@pytest.mark.parametrize('question,expected', SCOPE_CASES)
def test_literal_scope_boundary(question, expected):
    result = K.original_scope(question, DOCUMENTS)
    assert result['card_keys'] == expected
    assert result['clarify'] == (expected == [])


def scope():
    return K.original_scope('BC카드 중에서 커피 할인 카드 찾아줘', DOCUMENTS)


def route(plan, *, failed=False, directory=DB):
    return OLD.route(Q, plan, scope(), directory, FACTS, repaired=True, parser_failed=failed)


def test_empty_outer_conditions_restore_original_scope():
    result = route(Q.plan([]))
    assert result['route'] == 'U' and result['scope'] == issuer_keys('BC')


def test_empty_and_is_schema_failure_and_restores_original_scope():
    invalid = dict(groups=[[]], requested_fields=[], unsupported_filters=[])
    with pytest.raises(ValueError, match='Empty inner AND'):
        Q.QueryPlan.model_validate(invalid)
    result = route(None, failed=True)
    assert result['route'] == 'U' and result['scope'] == issuer_keys('BC')


def test_partial_and_does_not_narrow_to_partial_cards():
    plan = Q.plan([Q.requirement(['커피']), Q.requirement(['절대없는혜택64'])])
    result = route(plan)
    assert result['eligible_cards'] == [] and result['scope'] == issuer_keys('BC')


def test_and_is_intersection_of_cards_not_same_fact_row():
    coffee, subscription = Q.requirement(['커피']), Q.requirement(['넷플릭스'])
    a = set(route(Q.plan([coffee]))['eligible_cards'])
    b = set(route(Q.plan([subscription]))['eligible_cards'])
    assert set(route(Q.plan([coffee, subscription]))['eligible_cards']) == a & b


def test_positive_structured_result_is_search_scope():
    result = route(Q.plan([Q.requirement(['커피'])]))
    assert result['route'] == 'SEQ' and result['eligible_cards']
    assert result['scope'] == result['eligible_cards']
    assert set(result['scope']) <= set(issuer_keys('BC'))


def test_parser_failure_keeps_trusted_scope():
    result = route(None, failed=True)
    assert result['scope'] == issuer_keys('BC') and not result['db_attempted']


def test_database_exception_is_not_a_zero_result(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        route(Q.plan([Q.requirement(['커피'])]), directory=tmp_path/'absent')


def test_returned_fields_are_not_where_constraints():
    a = Q.plan([Q.requirement(['커피'])])
    b = {**a, 'requested_fields': ['frequency', 'cap', 'condition']}
    assert route(a)['eligible_cards'] == route(b)['eligible_cards']


@pytest.mark.parametrize('question', [
    'IBK K-패스(신용)로 코레일 공식 앱에서 기차표를 사면 할인돼? 택시 앱 결제와 차이도 알려줘.',
    'IBK K-패스 (신용)의 택시 할인 조건 알려줘',
    'ibk K-패스(신용)의 버스 할인 알려줘',
])
def test_explicit_issuer_adjacent_to_catalog_product(question):
    actual = K.original_scope(question, DOCUMENTS)
    assert actual['card_keys'] == ['ibk/IBK_KPass(Credit)']
    assert actual['issuer'] == 'ibk' and not actual['clarify']
