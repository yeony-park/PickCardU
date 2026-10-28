from pathlib import Path

import pytest
from pydantic import ValidationError

from pickcardu_rag.structured import QueryPlan, StructuredStore, explicit_scope, narrowing_reason


ROOT = Path(__file__).resolve().parents[3]
DB3 = ROOT / 'notebooks/data/60_structured_retrieval_repair/db_v3/structured.sqlite'


def requirement(target, **updates):
    return dict(targets=[target], action='any', min_percent=None, max_spend_krw=None,
                min_cap_krw=None, cap_period='any', exclude_targets=[], min_frequency=None,
                frequency_period='any') | updates


def plan(*requirements):
    return QueryPlan(groups=[list(requirements)] if requirements else [],
                     requested_fields=[], unsupported_filters=[])


def test_same_card_and_and_partial_hits():
    store = StructuredStore(DB3)
    coffee = set(store.query(plan(requirement('커피'))))
    netflix = set(store.query(plan(requirement('넷플릭스'))))
    assert coffee and netflix and coffee != netflix
    assert set(store.query(plan(requirement('커피'), requirement('넷플릭스')))) == coffee & netflix
    assert not store.query(plan(requirement('커피'), requirement('없는혜택대상')))


def test_empty_and_unsupported_plans_do_not_select_all():
    store = StructuredStore(DB3)
    assert store.query(plan()) == ()
    unsupported = plan(requirement('커피')).model_copy(update={'unsupported_filters': ['연회비 1만원 이하']})
    assert store.query(unsupported) == ()
    with pytest.raises(ValidationError):
        QueryPlan(groups=[[]], requested_fields=[], unsupported_filters=[])


def test_empty_scope_is_not_global_and_unknown_scope_fails():
    store = StructuredStore(DB3)
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
