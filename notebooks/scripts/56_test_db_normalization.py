"""Independent semantic regression cases for the copied experiment DB only."""
import copy
import importlib.util
from pathlib import Path

import pytest


def normalizer():
    path = Path(__file__).with_name('56_normalize_experiment_db.py')
    spec = importlib.util.spec_from_file_location('normalization56', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize(raw, derived):
    return normalizer().normalize_fact(raw, derived)


def state(**updates):
    return dict(spend_threshold_krw=None, spend_operator=None, spend_state='unknown',
                spend_reason='missing', action_kind='discount', action_status='true',
                action_reason='positive discount', cap_krw=10000, cap_state='known',
                cap_reason='explicit', cap_period='month', cap_period_state='known',
                cap_period_reason='explicit month', percent=10, percent_state='known',
                target_normalized='커피', **updates)


@pytest.mark.parametrize('condition', ['전월실적 조건 없음', '전월 실적 조건 없음',
                                       '전월 이용금액 조건 없음'])
def test_explicit_absence_is_zero_minimum_required_spend(condition):
    result, rules = normalize(dict(condition=condition), state())
    assert (result['spend_threshold_krw'], result['spend_operator'], result['spend_state']) == (0, 'gte', 'known')
    assert rules


@pytest.mark.parametrize('condition', ['', '미확인', 'unknown', '전월실적 30만원 이상',
                                       '전월실적 조건 없음 단 일부 서비스 30만원 이상',
                                       '이용실적 0원인 고객', '전월실적 관계없이'])
def test_missing_customer_zero_or_conflicting_conditions_are_not_card_no_spend(condition):
    before = state()
    result, _ = normalize(dict(condition=condition), before)
    assert result['spend_threshold_krw'] is None
    assert result['spend_state'] == 'unknown'


def test_no_limit_does_not_imply_no_previous_month_spend():
    result, _ = normalize(dict(condition='', cap='할인 한도 조건 없음'), state())
    assert result['spend_state'] == 'unknown'


def test_exchange_voucher_is_not_newly_earned_points():
    before = state()
    before.update(action_kind='earn', action_reason='point token')
    raw = dict(benefit_type='포인트 교환바우처 사용안내', action='교환해 드리는',
               value='9', unit='만포인트', condition='본인회원', cap='')
    result, rules = normalize(raw, before)
    assert result['action_kind'] is None and result['action_status'] == 'unknown'
    assert rules


def test_earning_points_that_can_later_be_exchanged_remains_earn():
    before = state()
    before.update(action_kind='earn')
    result, _ = normalize(dict(benefit_type='적립 포인트 교환바우처',
                               action='포인트 적립 및 바우처 교환', cap=''), before)
    assert result['action_kind'] == 'earn' and result['action_status'] == 'true'


@pytest.mark.parametrize('raw', [
    dict(benefit_type='주유 서비스 월 이용금액 한도', cap='15만원', condition='30만원 이상'),
    dict(benefit_type='기본캐시백', cap='최대 월 50만원까지 캐시백 대상으로 인정', condition=''),
    dict(benefit_type='주유소', cap='회당 주유금액 10만원 한도', condition='전월 50만원 이상'),
    dict(benefit_type='할인 한도', cap='전월 실적 30만원 이상에 해당하는 할인 한도', condition=''),
])
def test_eligible_purchase_or_previous_spend_is_not_benefit_payout_cap(raw):
    result, rules = normalize(raw, state())
    assert result['cap_krw'] is None and result['cap_state'] == 'unknown'
    assert result['cap_period'] is None and result['cap_period_state'] == 'unknown'
    assert rules


@pytest.mark.parametrize('cap', ['월 최대 2천원 할인', '월간 통합할인한도 2만원',
                                '건당 1만원 한도', '연간 2만원 한도',
                                '추가캐시백 한도 10만원'])
def test_actual_payout_caps_or_unresolved_caps_are_not_cleared(cap):
    before = state()
    result, _ = normalize(dict(benefit_type='할인서비스', action='할인', cap=cap), before)
    assert result['cap_krw'] == before['cap_krw'] and result['cap_state'] == 'known'


def test_normalization_is_nonmutating_idempotent_and_preserves_unrelated_fields():
    raw = dict(condition='전월실적 조건 없음', target='커피', value='10', unit='%',
               fact_id='independent_fixture', field_evidence={}, relation_scope={})
    before = state()
    saved = copy.deepcopy((raw, before))
    result, _ = normalize(raw, before)
    again, rules = normalize(raw, result)
    assert (raw, before) == saved
    assert again == result and not rules
    allowed = {f'{prefix}_{suffix}' for prefix, suffixes in [
        ('spend', ['threshold_krw', 'operator', 'state', 'reason']),
        ('action', ['kind', 'status', 'reason']),
        ('cap', ['krw', 'state', 'reason', 'period', 'period_state', 'period_reason'])]
        for suffix in suffixes}
    assert {key for key in before.keys() | result.keys() if before.get(key) != result.get(key)} <= allowed
    assert result['target_normalized'] == before['target_normalized']
    assert result['percent'] == before['percent']


def test_post_percentage_reward_amount_cap_is_a_benefit_cap_not_purchase_base():
    # Original PDF defines reward application amount as spend multiplied by
    # percentage: 80,000 * 1% = 800, then 1,000 units become vouchers.
    # A conversion step alone does not turn its monthly KRW cap into spend.
    raw = dict(action='리워드 발행', unit='%', value='1',
               frequency='누적 이용 금액 10만원당 1천원',
               cap='월 3만원 (초과 시 미적용)',
               relation_scope=dict(scope_type='table_row',row_line_ids=['P0004-L0003']),
               field_evidence={f:[dict(line_id='P0004-L0003')] for f in ['cap','value','frequency']})
    before = state()
    before.update(cap_krw=30000)
    result, _ = normalize(raw, before)
    assert result['cap_krw'] == 30000 and result['cap_state'] == 'known'


def test_non_krw_ceiling_requires_the_bound_per_liter_source_not_just_fuel_word():
    raw = dict(action='적립',unit='M포인트',value='60-80',benefit_type='주유',
        condition='주중60 주말80 M포인트 적립', cap='1회 10만원 한도',
        relation_scope=dict(scope_type='bounded_block',line_ids=['P0001-L0001','P0001-L0002']),
        field_evidence={f:[dict(line_id='P0001-L0001',fragment='60-80')] for f in ['cap','value','condition']})
    result, _ = normalize(raw,state())
    assert result['cap_state'] == 'known'
    raw['_source_line_text_by_id']={'P0001-L0001':'GS칼텍스 리터당60·80 M포인트 적립'}
    result, _ = normalize(raw,state())
    assert result['cap_state'] == 'unknown'


def test_a_real_page_line_without_the_decisive_meaning_cannot_confirm_change():
    import json
    root = Path(__file__).resolve().parents[2]
    review = json.loads((root/'notebooks/data/56_structured_normalization/db_source_review/normalization_source_review.json').read_text())
    support = copy.deepcopy(review['records'][0]['source_support'][0])
    module = normalizer()
    assert module._decisive_proof_complete(root,support,{})
    # This line really is on the page but says nothing about the 300,000 KRW
    # being a performance tier rather than a payout cap.
    support['decisive_proof']['quotes']=[dict(page=2,text='할인 한도')]
    assert not module._decisive_proof_complete(root,support,{})


def test_invented_quote_cannot_pass_even_with_correct_decisive_terms():
    import json
    root=Path(__file__).resolve().parents[2]
    review=json.loads((root/'notebooks/data/56_structured_normalization/db_source_review/normalization_source_review.json').read_text())
    support=copy.deepcopy(review['records'][0]['source_support'][0])
    support['decisive_proof']['quotes'][0]['text'] += '존재하지 않는 검증 문장'
    assert not normalizer()._decisive_proof_complete(root,support,{})
