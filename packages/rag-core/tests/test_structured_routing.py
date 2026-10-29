from types import SimpleNamespace

import pytest

from pickcardu_rag.structured import QueryPlan
from pickcardu_rag.structured_routing import structured_scope


CATALOG = tuple({'card_key': k, 'card_name': k, 'issuer': '발급사'} for k in ('a', 'b', 'c', 'd'))
PLAN = QueryPlan.model_validate({'groups': [[dict(targets=['커피'], action='any', min_percent=None,
    max_spend_krw=None, min_cap_krw=None, cap_period='any', exclude_targets=[], min_frequency=None,
    frequency_period='any')]], 'requested_fields': [], 'unsupported_filters': []})


class Extractor:
    def __init__(self, plan=PLAN):
        self.plan, self.calls = plan, []

    def extract(self, question):
        self.calls.append(question)
        return self.plan, {'provider_called': True, 'usage_complete': True, 'extraction_ms': 1}


class Store:
    def __init__(self, matches=(), error=False):
        self.matches, self.error = set(matches), error

    def query(self, plan, scope):
        if self.error:
            raise RuntimeError('DB unavailable')
        return tuple(k for k in scope if k in self.matches)


def call(store=Store(), extractor=None, **kwargs):
    return structured_scope('커피 추천', CATALOG, store, extractor or Extractor(), **kwargs)


def test_zero_results_and_query_error_have_distinct_fallback_reasons():
    for store, reason in [(Store(), 'no_results'), (Store(error=True), 'query_error')]:
        allowed, usage = call(store, wallet=('a', 'b'))
        assert allowed == ('a', 'b')
        assert usage['reason'] == reason


def test_wallet_new_are_narrowed_separately_and_empty_side_falls_back():
    extractor = Extractor()
    allowed, usage = call(Store(('b',)), extractor, wallet=('a', 'b'), new=('c', 'd'))
    assert allowed == ('b', 'c', 'd')
    assert usage['groups']['wallet']['reason'] == 'matched'
    assert usage['groups']['new']['reason'] == 'no_results'
    assert len(extractor.calls) == 1


def test_precision_comparison_skips_extraction():
    extractor = Extractor()
    allowed, usage = call(extractor=extractor, targets=('a', 'b'))
    assert allowed is None and not extractor.calls
    assert usage['reason'] == 'precision_comparison'


def test_extraction_failure_preserves_wallet_and_numeric_guard_preserves_base():
    allowed, usage = call(extractor=Extractor(None), wallet=('a', 'b'))
    assert allowed == ('a', 'b') and usage['reason'] == 'extraction_failure'
    numeric = PLAN.model_copy(deep=True)
    numeric.groups[0][0].max_spend_krw = 300000
    allowed, usage = call(Store(('a',)), Extractor(numeric), wallet=('a', 'b'))
    assert allowed == ('a', 'b') and usage['reason'] == 'numeric_guard'


def test_explicit_product_outside_wallet_does_not_fallback_or_extract():
    catalog = ({'card_key': 'issuer/a', 'card_name': '알파특별카드', 'issuer': '발급사'},
               {'card_key': 'issuer/b', 'card_name': '베타특별카드', 'issuer': '발급사'})
    extractor = Extractor()
    allowed, usage = structured_scope('베타특별카드 커피', catalog, Store(), extractor, wallet=('issuer/a',))
    assert allowed == () and not extractor.calls and usage['reason'] == 'empty_explicit_scope'
