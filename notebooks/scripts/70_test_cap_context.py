"""A purchase ceiling must not be exposed as a benefit payout cap."""
import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('cap70_tested', Path(__file__).with_name('70_cap_context.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def fixture(fragment='디지털구독·멤버십 1회 1만원', prefix='- ※ 할인받기 전 이용금액 기준 공과금 1회 5만원, '):
    line = prefix + fragment + ','
    proof = dict(line_id='P0005-L0027', char_start=len(prefix), char_end=len(prefix)+len(fragment), fragment=fragment)
    return dict(fact_id='fixture', document_id='issuer/card', fields={'cap':fragment},
                field_evidence={'cap':[proof]}, raw_fact={'cap':fragment},
                derived=dict(cap_krw=10000,cap_state='known',cap_reason='old',cap_period='month',
                             cap_period_state='known',cap_period_reason='old',percent=20)), {'P0005-L0027':line}


def test_restores_own_qualifier_without_copying_another_benefits_amount():
    fact, lines = fixture(); before = copy.deepcopy(fact)
    patch = D.cap_context_patch(fact, lines)
    assert patch['fields']['cap'] == '할인받기 전 이용금액 기준 디지털구독·멤버십 1회 1만원'
    assert '5만원' not in patch['fields']['cap']
    assert patch['derived']['cap_state'] == 'unknown'
    assert patch['derived']['cap_krw'] is None
    assert patch['derived']['cap_period'] is None
    assert patch['derived']['percent'] == 20
    assert fact == before
    assert all(lines[p['line_id']][p['char_start']:p['char_end']] == p['fragment']
               for p in patch['field_evidence']['cap'])
    assert patch['source_line'] == lines['P0005-L0027']


@pytest.mark.parametrize('prefix', ['월 할인한도 ', '할인금액 기준 ', '다른 혜택 안내; 할인 전 금액 ', ''])
def test_unsupported_or_payout_wording_is_unchanged(prefix):
    fact, lines = fixture(prefix=prefix)
    assert D.cap_context_patch(fact, lines) is None


def test_missing_or_mismatched_evidence_is_never_guessed():
    for change in ('missing_line','fragment','span','different_cap'):
        fact, lines = fixture()
        if change == 'missing_line': lines = {}
        elif change == 'fragment': fact['field_evidence']['cap'][0]['fragment'] = '다른 값'
        elif change == 'span': fact['field_evidence']['cap'][0]['char_start'] += 1
        else: fact['fields']['cap'] = '월 지급한도 1만원'
        assert D.cap_context_patch(fact, lines) is None


def test_an_intervening_payout_qualifier_does_not_inherit_the_purchase_prefix():
    fact, lines = fixture(prefix='할인받기 전 이용금액 기준 커피 1회 1만원; 월 할인한도 ')
    assert D.cap_context_patch(fact, lines) is None


def test_rule_is_identity_independent_and_idempotent():
    fact, lines = fixture(); other = copy.deepcopy(fact)
    other.update(fact_id='any-other-id',document_id='other/owner')
    left = D.cap_context_patch(fact, lines); right = D.cap_context_patch(other, lines)
    assert left == right
    repaired = copy.deepcopy(fact)
    repaired.update({key:left[key] for key in ('fields','derived','field_evidence')})
    assert D.cap_context_patch(repaired, lines) is None


def test_source_line_numbers_use_the_saved_normalized_page_without_reflow():
    assert D.source_lines({'pages':[{'page':5,'text':'first\n\nthird'}]}) == {
        'P0005-L0001':'first','P0005-L0002':'','P0005-L0003':'third'}
