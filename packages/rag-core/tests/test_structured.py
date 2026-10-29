import json
import sqlite3

import pytest
from pydantic import ValidationError

from pickcardu_rag.structured import QueryPlan, StructuredStore, evaluate_fact, explicit_scope, narrowing_reason
from pickcardu_rag.structured_values import FIELDS, _derive_fact


@pytest.fixture
def structured_store(tmp_path):
    """Exercise SQL selection without the historical experiment database."""
    path = tmp_path / 'structured.sqlite'
    columns = ','.join(f'{field}_raw TEXT NOT NULL' for field in FIELDS)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE documents (document_id TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE facts (fact_id TEXT PRIMARY KEY, document_id TEXT, '
                   + columns + ',derived_json TEXT NOT NULL)')
        db.executemany('INSERT INTO documents VALUES (?)',
                       [('fixture/both',), ('fixture/coffee',), ('fixture/netflix',)])
        rows = [('fixture/both', '커피'), ('fixture/both', '넷플릭스'),
                ('fixture/coffee', '커피'), ('fixture/netflix', '넷플릭스')]
        for index, (card, target) in enumerate(rows):
            fields = {field: '' for field in FIELDS}
            fields.update(target=target, action='할인', value='10', unit='%')
            derived = {'action_status': 'true', 'action_kind': 'discount',
                       'percent': 10, 'percent_state': 'known'}
            values = (str(index), card, *(fields[field] for field in FIELDS), json.dumps(derived))
            db.execute('INSERT INTO facts VALUES (' + ','.join('?' for _ in values) + ')', values)
    return StructuredStore(path)


def requirement(target, **updates):
    return dict(targets=[target], action='any', min_percent=None, max_spend_krw=None,
                min_cap_krw=None, cap_period='any', exclude_targets=[], min_frequency=None,
                frequency_period='any') | updates


def plan(*requirements):
    return QueryPlan(groups=[list(requirements)] if requirements else [],
                     requested_fields=[], unsupported_filters=[])


def test_same_card_and_and_partial_hits(structured_store):
    store = structured_store
    coffee = set(store.query(plan(requirement('커피'))))
    netflix = set(store.query(plan(requirement('넷플릭스'))))
    assert coffee == {'fixture/both', 'fixture/coffee'}
    assert netflix == {'fixture/both', 'fixture/netflix'}
    assert set(store.query(plan(requirement('커피'), requirement('넷플릭스')))) == coffee & netflix
    assert not store.query(plan(requirement('커피'), requirement('없는혜택대상')))


def test_empty_and_unsupported_plans_do_not_select_all(structured_store):
    store = structured_store
    assert store.query(plan()) == ()
    unsupported = plan(requirement('커피')).model_copy(update={'unsupported_filters': ['연회비 1만원 이하']})
    assert store.query(unsupported) == ()
    with pytest.raises(ValidationError):
        QueryPlan(groups=[[]], requested_fields=[], unsupported_filters=[])


def test_empty_scope_is_not_global_and_unknown_scope_fails(structured_store):
    store = structured_store
    assert store.query(plan(requirement('커피')), ()) == ()
    with pytest.raises(ValueError):
        store.query(plan(requirement('커피')), ('missing/card',))


def test_numeric_filter_guard_does_not_block_explanation():
    assert narrowing_reason(plan(requirement('커피', max_spend_krw=300000))) == 'numeric_guard'
    assert narrowing_reason(plan(requirement('커피', min_cap_krw=10000))) == 'numeric_guard'
    details = plan(requirement('커피')).model_copy(update={'requested_fields': ['condition', 'cap']})
    assert narrowing_reason(details) is None
    assert narrowing_reason(plan()) == 'empty_conditions'


CATALOG = (
    {'card_key': 'shinhan/deep', 'card_name': '신한 딥드림 카드', 'issuer': '신한카드'},
    {'card_key': 'samsung/taptap', 'card_name': '삼성 탭탭 카드', 'issuer': '삼성카드'},
)


def test_issuer_product_and_allowed_scopes_intersect_independently():
    assert explicit_scope('삼성카드 중 신한 딥드림 카드', CATALOG) == ()
    assert explicit_scope('신한 딥드림 카드', CATALOG, ('samsung/taptap',)) == ()
    assert explicit_scope('삼성카드 중 커피 혜택', CATALOG) == ('samsung/taptap',)
    assert explicit_scope('커피 추천', CATALOG) is None


def test_nested_product_name_does_not_add_another_card_but_separate_mentions_do():
    catalog = (
        {'card_key':'shinhan/plan', 'card_name':'신한카드 Point Plan', 'issuer':'신한카드'},
        {'card_key':'shinhan/plus', 'card_name':'신한카드 Point Plan+', 'issuer':'신한카드'},
        {'card_key':'lotte/sky', 'card_name':'DIGILOCA SKYPASS', 'issuer':'롯데카드'},
        {'card_key':'NH/sky', 'card_name':'SKYPASS', 'issuer':'NH농협카드'},
    )
    assert explicit_scope('신한카드 Point Plan+ 혜택 알려줘', catalog) == ('shinhan/plus',)
    assert explicit_scope('DIGILOCA SKYPASS 혜택', catalog) == ('lotte/sky',)
    assert explicit_scope('Point Plan과 Point Plan+ 둘 다 비교', catalog) == ('shinhan/plan', 'shinhan/plus')


@pytest.mark.parametrize('target,exceptions,query_target,expected', [
    ('스타벅스', '스타벅스 제외', '스타벅스', False),
    ('스타벅스', '백화점 입점 매장 제외', '스타벅스', True),
    ('스타벅스', '', '스타벅스', True),
    ('커피', '스타 벅스 제외', '커피', False),
])
def test_excluded_benefit_target_is_not_a_positive_match(target, exceptions, query_target, expected):
    fields = {field: '' for field in FIELDS}
    fields.update(target=target, action='할인', exceptions=exceptions)
    fact = dict(fact_id='fixture', fields=fields, derived=_derive_fact(fields))
    assert evaluate_fact(fact, requirement(query_target)) is expected


def test_sql_selection_keeps_valid_cards_and_rejects_excluded_target(structured_store):
    with sqlite3.connect(structured_store.path) as db:
        db.execute("UPDATE facts SET exceptions_raw=?, derived_json=json_set(derived_json, "
                   "'$.exceptions_normalized', ?) WHERE document_id=?",
                   ('스타벅스 제외', '스타벅스제외', 'fixture/coffee'))
    assert structured_store.query(plan(requirement('커피'))) == ('fixture/both',)
