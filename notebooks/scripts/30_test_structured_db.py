from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("30_build_structured_db.py")
SPEC = importlib.util.spec_from_file_location("build_structured_db_30", MODULE_PATH)
assert SPEC and SPEC.loader
db = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = db
SPEC.loader.exec_module(db)


def fact(
    fact_id: str,
    card: str,
    target: str,
    *,
    action: str = "discount",
    action_status: str = "true",
    percent: float | None = None,
    percent_state: str = "not_applicable",
    spend: int | None = None,
    spend_state: str = "not_applicable",
    cap: int | None = None,
    cap_state: str = "not_applicable",
    cap_period: str | None = None,
    cap_period_state: str = "not_applicable",
    exceptions: str = "",
) -> dict:
    return {
        "fact_id": fact_id,
        "document_id": card,
        "fields": {"target": target},
        "derived": {
            "target_normalized": db._canonical(target),
            "action_kind": action,
            "action_status": action_status,
            "percent": percent,
            "percent_state": percent_state,
            "spend_threshold_krw": spend,
            "spend_state": spend_state,
            "cap_krw": cap,
            "cap_state": cap_state,
            "cap_period": cap_period,
            "cap_period_state": cap_period_state,
            "exceptions_normalized": db._canonical(exceptions),
        },
    }


def requirement(**overrides) -> dict:
    value = {
        "targets": [], "action": "any", "min_percent": None,
        "max_spend_krw": None, "min_cap_krw": None, "exclude_targets": [],
        "cap_period": "any",
    }
    value.update(overrides)
    return value


def plan(groups, card_keys=None, unresolved=None) -> dict:
    return {
        "route": "structured_then_unstructured", "groups": groups,
        "card_keys": card_keys or [], "unresolved": unresolved or [],
    }


def test_percent_ratio_scaling_requires_raw_agreement() -> None:
    raw = {"value": "10", "unit": "%", "typed_normalization": {"value": [{"kind": "ratio", "decimal": "0.1"}]}}
    assert db._derive_percent(raw)[:2] == (10.0, "known")


def test_multiple_percentages_remain_unknown() -> None:
    raw = {
        "value": "10% 또는 20%", "unit": "%",
        "typed_normalization": {"value": [{"kind": "ratio", "decimal": "0.1"}, {"kind": "ratio", "decimal": "0.2"}]},
    }
    assert db._derive_percent(raw)[1] == "unknown"


@pytest.mark.parametrize(
    "value,unit,typed",
    [
        ("3~5", "%", [{"kind": "ratio", "decimal": "0.05"}]),
        ("최대 5", "%", [{"kind": "ratio", "decimal": "0.05"}]),
        (
            "국내 15,000원~35,000원, 해외 8%",
            "",
            [
                {"kind": "KRW", "decimal": "15000"},
                {"kind": "KRW", "decimal": "35000"},
                {"kind": "ratio", "decimal": "0.08"},
            ],
        ),
    ],
)
def test_percent_range_upper_bound_and_mixed_money_remain_unknown(value, unit, typed) -> None:
    raw = {"value": value, "unit": unit, "typed_normalization": {"value": typed}}
    assert db._derive_percent(raw)[1] == "unknown"


def test_single_percent_remains_known() -> None:
    raw = {"value": "5", "unit": "%", "typed_normalization": {"value": [{"kind": "ratio", "decimal": "0.05"}]}}
    assert db._derive_percent(raw)[:2] == (5.0, "known")


@pytest.mark.parametrize(
    "cap,period,expected,state",
    [
        ("월 5,000원", "", "month", "known"),
        ("5,000원", "매월 말일에 소멸", "month", "known"),
        ("1일 최대 5,000원", "", "day", "known"),
        ("5,000원", "", None, "unknown"),
        ("월 10,000원, 1일 최대 5,000원", "", None, "unknown"),
    ],
)
def test_cap_period_derivation(cap, period, expected, state) -> None:
    assert db._derive_cap_period({"cap": cap, "period": period})[:2] == (expected, state)


def test_spend_ceiling_is_not_treated_as_benefit_cap() -> None:
    raw = {
        "cap": "월100만원 이용금액까지",
        "typed_normalization": {"cap": [{"kind": "KRW", "decimal": "1000000"}]},
    }
    amount, state, _ = db._derive_cap(raw)
    assert amount is None and state == "unknown"


def test_explicit_monthly_discount_cap_remains_known() -> None:
    raw = {
        "cap": "월 5,000원 할인한도",
        "typed_normalization": {"cap": [{"kind": "KRW", "decimal": "5000"}]},
    }
    assert db._derive_cap(raw)[:2] == (5000, "known")


def test_missing_explicit_spend_threshold_is_unknown_not_zero() -> None:
    raw = {"condition": "조건은 상품설명서를 참고", "typed_normalization": {"condition": [{"kind": "KRW", "decimal": "300000"}]}}
    amount, _, state, _ = db._derive_spend_threshold(raw)
    assert amount is None and state == "unknown"


def test_same_benefit_prevents_cross_contamination() -> None:
    rows = [
        fact("coffee", "CARD/A", "커피", percent=5, percent_state="known"),
        fact("store", "CARD/A", "편의점", percent=10, percent_state="known"),
    ]
    result = db.select_cards(rows, plan([[requirement(targets=["커피"], action="discount", min_percent=10)]]))
    assert result["eligible_cards"] == []


def test_and_allows_different_benefit_facts_on_same_card() -> None:
    rows = [fact("f1", "CARD/A", "커피"), fact("f2", "CARD/A", "편의점")]
    result = db.select_cards(rows, plan([[requirement(targets=["커피"]), requirement(targets=["편의점"])]]))
    assert result["eligible_cards"] == ["CARD/A"]
    assert result["matching_fact_ids"]["CARD/A"] == ["f1", "f2"]


def test_or_groups_accept_either_group() -> None:
    rows = [fact("f1", "CARD/A", "호텔"), fact("f2", "CARD/B", "편의점")]
    result = db.select_cards(rows, plan([[requirement(targets=["여행"])], [requirement(targets=["편의점"])]]))
    assert result["eligible_cards"] == ["CARD/A", "CARD/B"]


def test_excluded_target_rejects_matching_fact() -> None:
    rows = [fact("f1", "CARD/A", "커피 및 스타벅스")]
    result = db.select_cards(rows, plan([[requirement(targets=["커피"], exclude_targets=["스타벅스"])]]))
    assert result["eligible_cards"] == []


def test_card_scope_is_preserved_for_unstructured_and_structured() -> None:
    rows = [fact("f1", "BC/ONE", "커피"), fact("f2", "BC/TWO", "커피")]
    structured = db.select_cards(rows, plan([[requirement(targets=["커피"])]], card_keys=["BC/TWO"]))
    assert structured["eligible_cards"] == ["BC/TWO"]
    unstructured_plan = {"route": "unstructured", "groups": [], "card_keys": ["BC/ONE"], "unresolved": []}
    assert db.select_cards(rows, unstructured_plan)["eligible_cards"] == ["BC/ONE"]


def test_unknown_numeric_requirement_is_reported_not_rejected() -> None:
    rows = [fact("f1", "CARD/A", "커피", percent_state="unknown")]
    result = db.select_cards(rows, plan([[requirement(targets=["커피"], min_percent=10)]]))
    assert result["eligible_cards"] == [] and result["unknown_cards"] == ["CARD/A"]


def test_cap_period_must_match_requested_period() -> None:
    rows = [
        fact("month", "CARD/MONTH", "커피", cap=5000, cap_state="known", cap_period="month", cap_period_state="known"),
        fact("day", "CARD/DAY", "커피", cap=5000, cap_state="known", cap_period="day", cap_period_state="known"),
        fact("unknown", "CARD/UNKNOWN", "커피", cap=5000, cap_state="known", cap_period_state="unknown"),
    ]
    monthly = db.select_cards(rows, plan([[requirement(targets=["커피"], min_cap_krw=5000, cap_period="month")]]))
    assert monthly["eligible_cards"] == ["CARD/MONTH"]
    assert monthly["unknown_cards"] == ["CARD/UNKNOWN"]
    daily = db.select_cards(rows, plan([[requirement(targets=["커피"], min_cap_krw=5000, cap_period="day")]]))
    assert daily["eligible_cards"] == ["CARD/DAY"]
    assert daily["unknown_cards"] == ["CARD/UNKNOWN"]


def test_cap_period_any_does_not_require_period_metadata() -> None:
    rows = [fact("unknown", "CARD/A", "커피", cap=5000, cap_state="known", cap_period_state="unknown")]
    result = db.select_cards(rows, plan([[requirement(targets=["커피"], min_cap_krw=5000)]]))
    assert result["eligible_cards"] == ["CARD/A"]


def test_negated_action_does_not_match() -> None:
    rows = [fact("f1", "CARD/A", "커피", action_status="false")]
    result = db.select_cards(rows, plan([[requirement(targets=["커피"], action="discount")]]))
    assert result["eligible_cards"] == [] and result["unknown_cards"] == []


def test_parameterized_sql_does_not_expand_injection_text(tmp_path: Path) -> None:
    database = tmp_path / "sample.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE facts(fact_id TEXT, document_id TEXT, ordinal INTEGER); INSERT INTO facts VALUES ('f1','BC/ONE',0);")
    malicious = "BC/ONE') OR 1=1 --"
    assert db.query_facts_by_cards(database, [malicious]) == []
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM facts").fetchone()[0] == 1


def test_unresolved_plan_keeps_cards_unknown() -> None:
    rows = [fact("f1", "CARD/A", "커피")]
    result = db.select_cards(rows, plan([[requirement(targets=["커피"])]], unresolved=["rate"] ))
    assert result["eligible_cards"] == [] and result["unknown_cards"] == ["CARD/A"]


def test_built_artifacts_preserve_all_source_facts() -> None:
    output_dir = MODULE_PATH.parent.parent / "data" / "30_structured_benefit_db"
    summary_path = output_dir / "db_build_summary.json"
    if not summary_path.exists():
        pytest.skip("build artifact has not been generated yet")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["counts"]["documents"] == 106
    assert summary["counts"]["pages"] == 617
    assert summary["counts"]["facts"] == 3403
    with sqlite3.connect(output_dir / "structured.sqlite") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT count(*) FROM facts").fetchone()[0] == 3403


def test_load_facts_restores_jsonl_exactly_from_sqlite() -> None:
    output_dir = MODULE_PATH.parent.parent / "data" / "30_structured_benefit_db"
    facts_path = output_dir / "facts.jsonl"
    if not facts_path.exists():
        pytest.skip("build artifact has not been generated yet")
    expected = [json.loads(line) for line in facts_path.read_text(encoding="utf-8").splitlines() if line]
    assert db.load_facts(output_dir) == expected


def test_actual_gaon_nuri_spend_ceilings_remain_unknown() -> None:
    output_dir = MODULE_PATH.parent.parent / "data" / "30_structured_benefit_db"
    if not (output_dir / "structured.sqlite").exists():
        pytest.skip("build artifact has not been generated yet")
    rows = [
        row
        for row in db.load_facts(output_dir)
        if row["document_id"] == "kookmin/Kookmin_Gaon_Nuri_20240105"
        and "이용금액까지" in row["fields"]["cap"]
    ]
    assert len(rows) == 5
    assert all(row["derived"]["cap_krw"] is None for row in rows)
    assert all(row["derived"]["cap_state"] == "unknown" for row in rows)


def test_structured_database_read_only_uri_rejects_writes() -> None:
    output_dir = MODULE_PATH.parent.parent / "data" / "30_structured_benefit_db"
    database = (output_dir / "structured.sqlite").resolve()
    if not database.exists():
        pytest.skip("build artifact has not been generated yet")
    uri = f"{database.as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO metadata(key, value_json) VALUES ('forbidden', '{}')")
