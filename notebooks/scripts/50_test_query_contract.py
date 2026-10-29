"""Offline regressions for the shared 50–54 query contract; no paid calls."""
import importlib.util
import os
from pathlib import Path
import sys

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("query_contract50", HERE / "50_query_contract.py")
Q = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = Q
spec.loader.exec_module(Q)


def test_detail_request_must_reach_db():
    value = Q.plan([Q.requirement(["커피"], action="discount")], requested_fields=["frequency", "condition", "cap"])
    result = Q.query(value, scope=["BC/BC_Green_v2"])
    assert result["db_attempted"] and result["eligible_cards"] == ["BC/BC_Green_v2"]
    assert "fact_e1da0e4696793ed908db92b5" in result["matching_fact_ids"]["BC/BC_Green_v2"]


def test_two_benefits_and_details_must_reach_db():
    value = Q.plan([Q.requirement(["넷플릭스"], action="discount", min_percent=20),
                    Q.requirement(["유튜브"], action="discount", min_percent=20)], requested_fields=["frequency", "condition"])
    assert "NH/NH_Olbareun_FLEX" in Q.query(value)["eligible_cards"]


def test_train_is_not_electric_vehicle():
    assert not Q.target_matches("기차", "전기차 충전소")
    assert Q.target_matches("철도", "철도, 고속버스, 택시")


def test_empty_and_is_invalid():
    import pytest
    with pytest.raises(ValueError):
        Q.query({**Q.plan([]), "groups": [[]]})


def test_bc_scope_excludes_ibk():
    scope = Q.original_scope("BC 카드 중 커피 할인 있는 카드 찾아줘")
    assert scope["card_keys"] and all(k.startswith("BC/") for k in scope["card_keys"])
    assert not any(k.startswith("ibk/") for k in scope["card_keys"])


def test_same_card_and_not_cross_card_union():
    value = Q.plan([Q.requirement(["철도"], action="discount"),
                    Q.requirement(["고속버스"], action="discount"),
                    Q.requirement(["택시"], action="discount")])
    result = Q.query(value)
    assert "ibk/IBK_KPass(Credit)" in result["eligible_cards"]
    for key in result["eligible_cards"]:
        assert len(result["requirement_fact_ids"][key]["0"]) == 3


def test_frequency_is_filter_when_requested():
    value = Q.plan([Q.requirement(["커피"], action="discount", min_frequency=3, frequency_period="month")])
    assert not Q.query(value, scope=["BC/BC_Green_v2"])["eligible_cards"]
    value["groups"][0][0]["min_frequency"] = 2
    assert Q.query(value, scope=["BC/BC_Green_v2"])["eligible_cards"] == ["BC/BC_Green_v2"]


def test_numeric_inclusive_and_period():
    r = Q.requirement(["커피"], action="discount", max_spend_krw=200000,
                      min_cap_krw=3000, cap_period="month")
    assert Q.query(Q.plan([r]), scope=["BC/BC_Green_v2"])["eligible_cards"]
    r["max_spend_krw"] = 199999
    assert not Q.query(Q.plan([r]), scope=["BC/BC_Green_v2"])["eligible_cards"]
    r["max_spend_krw"] = 200000
    r["cap_period"] = "day"
    assert not Q.query(Q.plan([r]), scope=["BC/BC_Green_v2"])["eligible_cards"]


def test_zero_results_preserve_original_issuer_scope():
    scope = Q.original_scope("BC 카드 중 민트우주가맹점 할인 카드")
    value = Q.plan([Q.requirement(["민트우주가맹점"], action="discount")])
    result = Q.route(value, scope)
    assert result["db_attempted"] and result["reason"] == "no_results"
    assert result["scope"] == scope["card_keys"]


def test_serialization_keeps_full_fields_and_source():
    fact = Q.facts_by_id()["fact_e1da0e4696793ed908db92b5"]
    text = Q.serialize_fact(fact)
    assert all(str(fact["fields"][field]) in text for field in Q.FIELDS)
    assert fact["fact_id"] in text and fact["source_relative_path"] in text


def test_missing_frequency_field_is_schema_failure_not_empty_query():
    value = Q.plan([Q.requirement(["커피"])])
    del value["groups"][0][0]["frequency_period"]
    got = Q.route(value, Q.original_scope("BC 카드 커피 혜택"))
    assert got["reason"] == "invalid_schema" and not got["db_attempted"]
    assert got["scope"] and all(key.startswith("BC/") for key in got["scope"])


def test_billing_date_cannot_establish_monthly_benefit_cap():
    import copy
    fact = copy.deepcopy(Q.facts_by_id()["fact_e1da0e4696793ed908db92b5"])
    fact["fields"]["cap"] = "3,000원 한도"
    fact["fields"]["period"] = "다음 월 21일"
    r = Q.requirement(["커피"], min_cap_krw=3000, cap_period="month")
    assert not Q.evaluate_fact(fact, r)
