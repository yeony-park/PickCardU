"""Focused regressions for stage-60 copied experiment DB normalization."""

import copy
import importlib.util
from pathlib import Path

import pytest


def normalize(raw, derived):
    path = Path(__file__).with_name("60_normalize_experiment_db.py")
    spec = importlib.util.spec_from_file_location("normalization60", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize_fact(raw, derived)


def load_module():
    path = Path(__file__).with_name("60_normalize_experiment_db.py")
    spec = importlib.util.spec_from_file_location("normalization60_receipt", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state(**updates):
    value = {
        "spend_threshold_krw": None,
        "spend_operator": None,
        "spend_state": "unknown",
        "spend_reason": "missing",
        "action_kind": None,
        "action_status": "unknown",
        "action_reason": "unsupported or ambiguous action",
        "cap_krw": None,
        "cap_state": "unknown",
        "percent": None,
        "percent_state": "unknown",
        "target_normalized": "공항라운지",
    }
    value.update(updates)
    return value


def related_raw(**updates):
    value = {
        "target": "공항 라운지",
        "action": "무료 이용",
        "benefit_type": "공항 라운지 서비스",
        "value": "",
        "unit": "",
        "condition": "",
        "exceptions": "",
        "field_evidence": {
            "target": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
            "action": [{"line_id": "P0002-L0001", "fragment": "무료 이용"}],
            "benefit_type": [{"line_id": "P0002-L0001", "fragment": "공항 라운지 서비스"}],
            "value": [],
            "condition": [],
        },
        "relation_scope": {
            "scope_type": "bounded_block",
            "line_ids": ["P0002-L0001", "P0002-L0002"],
            "row_line_ids": [],
        },
    }
    value.update(updates)
    return value


@pytest.mark.parametrize(
    "condition",
    [
        "전월실적 조건 및 할인한도 없음",
        "전월실적조건 및 할인한도 제한없이",
        "전월 이용금액 조건 및 할인한도 제한 없이 신용결제 이용 시",
        "전월실적 조건 없음 이용 시",
    ],
)
def test_compound_explicit_no_spend_is_zero_minimum(condition):
    raw = related_raw(
        action="청구할인",
        condition=condition,
        field_evidence={
            "target": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
            "action": [{"line_id": "P0002-L0001", "fragment": "청구할인"}],
            "condition": [{"line_id": "P0002-L0002", "fragment": condition}],
        },
    )
    result, rules = normalize(raw, state(action_kind="discount", action_status="true"))
    assert (result["spend_threshold_krw"], result["spend_operator"], result["spend_state"]) == (0, "gte", "known")
    assert rules == ("explicit_compound_no_spend",)


@pytest.mark.parametrize(
    "condition",
    [
        "",
        "전월 이용금액 미기재",
        "전월실적 30만원 이상, 추가 혜택은 전월실적 조건 없음",
        "신규 발급월에는 전월실적 조건 및 할인한도 없음",
        "카드 등록월부터 다음달 말일까지 전월실적 조건 없음",
    ],
)
def test_missing_conflicting_other_benefit_or_temporal_no_spend_stays_unknown(condition):
    raw = related_raw(
        action="청구할인",
        condition=condition,
        field_evidence={
            "target": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
            "action": [{"line_id": "P0002-L0001", "fragment": "청구할인"}],
            "condition": [{"line_id": "P0002-L0002", "fragment": condition}],
        },
    )
    result, rules = normalize(raw, state(action_kind="discount", action_status="true"))
    assert result["spend_threshold_krw"] is None
    assert result["spend_state"] == "unknown"
    assert not rules


def test_no_spend_without_bounded_evidence_relation_stays_unknown():
    raw = related_raw(
        action="청구할인",
        condition="전월실적 조건 및 할인한도 없음",
        field_evidence={"condition": [{"line_id": "P0009-L0001", "fragment": "전월실적 조건 및 할인한도 없음"}]},
    )
    result, rules = normalize(raw, state(action_kind="discount", action_status="true"))
    assert result["spend_state"] == "unknown"
    assert not rules


@pytest.mark.parametrize("outside_field", ["target", "action"])
def test_no_spend_requires_target_action_and_condition_in_same_scope(outside_field):
    evidence = {
        "target": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
        "action": [{"line_id": "P0002-L0001", "fragment": "청구할인"}],
        "condition": [
            {
                "line_id": "P0002-L0002",
                "fragment": "전월실적 조건 및 할인한도 없음",
            }
        ],
    }
    evidence[outside_field] = [
        {"line_id": "P0009-L0001", "fragment": evidence[outside_field][0]["fragment"]}
    ]
    raw = related_raw(
        action="청구할인",
        condition="전월실적 조건 및 할인한도 없음",
        field_evidence=evidence,
    )
    result, rules = normalize(raw, state(action_kind="discount", action_status="true"))
    assert result["spend_state"] == "unknown"
    assert not rules


@pytest.mark.parametrize(
    "raw",
    [
        related_raw(action="무료 이용"),
        related_raw(
            action="무료 입장",
            value="무료",
            field_evidence={
                "target": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
                "action": [{"line_id": "P0002-L0001", "fragment": "무료 입장"}],
                "benefit_type": [{"line_id": "P0002-L0001", "fragment": "공항 라운지 서비스"}],
                "value": [{"line_id": "P0002-L0001", "fragment": "무료"}],
            },
        ),
        related_raw(
            action="제공",
            benefit_type="공항 라운지 무료 이용 서비스",
            field_evidence={
                "target": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
                "action": [{"line_id": "P0002-L0001", "fragment": "제공"}],
                "benefit_type": [{"line_id": "P0002-L0001", "fragment": "공항 라운지 무료 이용 서비스"}],
                "value": [],
            },
        ),
    ],
)
def test_explicit_positive_free_use_is_supported_without_discount_kind(raw):
    result, rules = normalize(raw, state())
    assert result["action_status"] == "true"
    assert result["action_kind"] is None
    assert rules == ("explicit_free_use_positive",)


@pytest.mark.parametrize(
    "updates",
    [
        {"action": "제공되지 않습니다", "benefit_type": "공항 라운지 무료 이용 서비스"},
        {"action": "무료 이용 불가", "benefit_type": "공항 라운지"},
        {"action": "자동 가입", "benefit_type": "유료 멤버십", "value": "무료 숙박권 2매"},
        {"action": "무료교환", "benefit_type": "엔진오일 무료교환 서비스"},
        {"action": "무료 가입", "benefit_type": "보험서비스 무료 가입"},
        {"action": "교환", "benefit_type": "포인트 교환바우처", "value": "무료"},
        {"action": "캐시백", "benefit_type": "이용료 캐시백", "exceptions": "무료서비스 이용기간 중 캐시백되지 않음"},
    ],
)
def test_negative_paid_voucher_exchange_or_non_use_free_phrases_are_not_positive(updates):
    raw = related_raw(**updates)
    result, rules = normalize(raw, state())
    assert result["action_status"] == "unknown"
    assert result["action_kind"] is None
    assert not rules


def test_free_use_without_complete_relation_or_with_existing_false_is_not_flipped():
    raw = related_raw(
        field_evidence={
            "action": [{"line_id": "P0009-L0001", "fragment": "무료 이용"}],
            "benefit_type": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
        }
    )
    result, rules = normalize(raw, state())
    assert result["action_status"] == "unknown" and not rules

    result, rules = normalize(related_raw(), state(action_status="false", action_reason="explicit denial"))
    assert result["action_status"] == "false" and not rules


@pytest.mark.parametrize("outside_field", ["target", "action"])
def test_free_use_requires_target_and_action_in_same_scope(outside_field):
    evidence = {
        "target": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
        "action": [{"line_id": "P0002-L0001", "fragment": "무료 이용"}],
        "benefit_type": [
            {"line_id": "P0002-L0001", "fragment": "공항 라운지 서비스"}
        ],
        "value": [],
    }
    evidence[outside_field] = [
        {"line_id": "P0009-L0001", "fragment": evidence[outside_field][0]["fragment"]}
    ]
    result, rules = normalize(related_raw(field_evidence=evidence), state())
    assert result["action_status"] == "unknown"
    assert not rules


def test_normalization_is_nonmutating_idempotent_and_preserves_unrelated_fields():
    raw = related_raw(
        condition="전월실적 조건 및 할인한도 없음",
        field_evidence={
            "target": [{"line_id": "P0002-L0001", "fragment": "공항 라운지"}],
            "action": [{"line_id": "P0002-L0001", "fragment": "무료 이용"}],
            "benefit_type": [{"line_id": "P0002-L0001", "fragment": "공항 라운지 서비스"}],
            "condition": [{"line_id": "P0002-L0002", "fragment": "전월실적 조건 및 할인한도 없음"}],
        },
    )
    before = state(percent=7.5, percent_state="known", target_normalized="라운지")
    saved = copy.deepcopy((raw, before))
    result, rules = normalize(raw, before)
    again, second_rules = normalize(raw, result)
    assert (raw, before) == saved
    assert set(rules) == {"explicit_compound_no_spend", "explicit_free_use_positive"}
    assert again == result and not second_rules
    assert result["percent"] == 7.5
    assert result["target_normalized"] == "라운지"


def test_manual_receipt_requires_fact_and_rule_specific_claim_binding():
    module = load_module()
    record = {
        "fact_ids": ["fact_a"],
        "supports_rules": ["explicit_free_use_positive"],
        "claim_bindings": [
            {
                "fact_id": "fact_a",
                "rule": "explicit_free_use_positive",
                "observation": "이 fact의 무료 이용 긍정만 확인",
            }
        ],
    }
    assert module._manual_receipt_covers(
        record, "fact_a", "explicit_free_use_positive"
    )
    assert not module._manual_receipt_covers(
        record, "fact_missing", "explicit_free_use_positive"
    )
    assert not module._manual_receipt_covers(
        record, "fact_a", "explicit_compound_no_spend"
    )


def test_manual_receipt_rejects_page_level_approval_without_specific_claim():
    module = load_module()
    record = {
        "fact_ids": ["fact_a"],
        "supports_rules": ["explicit_free_use_positive"],
        "claim_bindings": [],
    }
    assert not module._manual_receipt_covers(
        record, "fact_a", "explicit_free_use_positive"
    )


def test_fact_export_comparison_checks_raw_and_derived_payloads():
    module = load_module()
    database_records = {
        "fact_a": ({"target": "라운지"}, {"action_status": "true"}),
        "fact_b": ({"target": "주유"}, {"action_status": "unknown"}),
    }
    matching = [
        {
            "fact_id": "fact_a",
            "raw_fact": {"target": "라운지"},
            "derived": {"action_status": "true"},
        },
        {
            "fact_id": "fact_b",
            "raw_fact": {"target": "주유"},
            "derived": {"action_status": "unknown"},
        },
    ]
    assert module._fact_export_mismatches(database_records, matching) == []

    mismatching = copy.deepcopy(matching)
    mismatching[1]["derived"]["action_status"] = "true"
    assert module._fact_export_mismatches(database_records, mismatching) == [
        "fact_b"
    ]
