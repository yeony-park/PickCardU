"""Source-backed regressions for the isolated, corrected experiment SQL view."""
from pathlib import Path
import importlib.util
import sys

import pytest

HERE = Path(__file__).parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules():
    return load("predicate_test_base", "50_query_contract.py"), load("predicate_test_fix", "52_query_predicate_correction.py")


def fact(Q, card, text):
    return next(f for f in Q.facts_by_id().values() if f["document_id"] == card and text in f["fields"]["target"])


def test_positive_siren_order_exception_does_not_remove_coffee(modules):
    Q, C = modules
    f = fact(Q, "samsung/Samsung_iD_ON", "스타벅스")
    req = Q.requirement(["커피"], action="discount")
    assert not Q.evaluate_fact(f, req)  # preserved original failure
    assert C.evaluate_fact(Q, f, req)
    assert "사이렌오더" in Q.serialize_fact(f)


@pytest.mark.parametrize("card,target", [
    ("shinhan/Shinhan_Toss_Mr.Life_20251231", "LPG"),
    ("NH/NH_AllWonderful", "동물병원"),
    ("kookmin/Kookmin_K-Pass_20240424", "고속버스"),
])
def test_actual_excluded_targets_do_not_gain_a_witness(modules, card, target):
    Q, C = modules
    req = Q.requirement([target], action="discount")
    assert not any(C.evaluate_fact(Q, f, req) for f in Q.facts_by_id().values() if f["document_id"] == card)


def test_subtype_exclusion_does_not_remove_regular_bus(modules):
    Q, C = modules
    f = fact(Q, "kookmin/Kookmin_K-Pass_20240424", "버스")
    req = Q.requirement(["버스"], action="discount")
    assert not Q.evaluate_fact(f, req)
    assert C.evaluate_fact(Q, f, req)
    assert "고속버스" in Q.serialize_fact(f)


@pytest.mark.parametrize("changes", [
    {"exclude_targets": ["스타벅스"]}, {"min_percent": 31}, {"action": "earn"},
    {"min_frequency": 3, "frequency_period": "month"},
])
def test_explicit_exclusions_and_other_constraints_still_reject(modules, changes):
    Q, C = modules
    req = Q.requirement(["커피"], action="discount")
    req.update(changes)
    assert not C.evaluate_fact(Q, fact(Q, "samsung/Samsung_iD_ON", "스타벅스"), req)


@pytest.mark.parametrize("changes,expected", [
    ({"min_percent": 10}, True), ({"min_percent": 10.01}, False),
    ({"min_cap_krw": 3000, "cap_period": "month"}, True),
    ({"min_cap_krw": 3001, "cap_period": "month"}, False),
    ({"max_spend_krw": 200000}, True), ({"max_spend_krw": 199999}, False),
    ({"min_frequency": 2, "frequency_period": "month"}, True),
    ({"min_frequency": 2, "frequency_period": "day"}, False),
])
def test_numeric_and_period_boundaries_are_unchanged(modules, changes, expected):
    Q, C = modules
    req = Q.requirement(["커피"], action="discount")
    req.update(changes)
    f = Q.facts_by_id()["fact_e1da0e4696793ed908db92b5"]
    assert Q.evaluate_fact(f, req) is expected
    assert C.evaluate_fact(Q, f, req) is expected


def test_fixed_won_amount_is_not_a_percentage(modules):
    Q, C = modules
    f = next(f for f in Q.facts_by_id().values() if f["document_id"] == "ibk/IBK_KPass(Credit)"
             and "버스" in f["fields"]["target"] and f["fields"]["unit"] == "원")
    req = Q.requirement(["버스"], action="discount", min_percent=1)
    assert not Q.evaluate_fact(f, req)
    assert not C.evaluate_fact(Q, f, req)


def test_install_is_idempotent_and_preserves_original_files_and_cost(modules):
    _, C = modules
    X = load("predicate_install_runtime", "51_experiment_runtime.py")
    Q = X.Q
    paths = [HERE / "50_query_contract.py", Q.ROOT / "notebooks/data/51_query_extraction_scope/repeat1_plans.json"]
    before = {str(p): X.P.sha(p.read_bytes()) for p in paths}
    old_function = Q.evaluate_fact
    C.install(X)
    changed_function = Q.evaluate_fact
    assert changed_function is not old_function
    C.install(X)
    assert Q.evaluate_fact is changed_function
    assert before == {str(p): X.P.sha(p.read_bytes()) for p in paths}
    original = Q.ROOT / "notebooks/data/52_four_retrieval_modes"
    assert sum(p == original for p in X.P.F.STAGES.values()) == 1
    assert X.P.F.STAGES[520] == original
    assert X.P.F.STAGES[52].name == "52_corrected_four_retrieval_modes"
    assert C.legacy_cost_usd(X) + C.remaining_stage_cap(X) == C.TOTAL_STAGE_CAP


def test_query_keeps_same_card_AND_and_empty_AND_contract(modules):
    _, C = modules
    X = load("predicate_and_runtime", "51_experiment_runtime.py")
    C.install(X)
    Q = X.Q
    plan = Q.plan([Q.requirement(["커피"]), Q.requirement(["넷플릭스"])])
    result = Q.query(plan, ["BC/BC_KBank_SIMPLE"])
    assert result["eligible_cards"] == ["BC/BC_KBank_SIMPLE"]
    with pytest.raises(ValueError):
        Q.query({"groups": [[]], "requested_fields": [], "unsupported_filters": []})
    assert not Q.target_matches("기차", "전기차")


def test_complete_development_predicate_diff_matches_reviewed_pairs(modules):
    Q, C = modules
    probe = Q.P.F.read_json(C.LEGACY / 'predicate_difference_probe.json')
    review = Q.P.F.read_json(C.LEGACY / 'predicate_difference_review.json')
    requirements = {Q.P.F.canonical(p['requirement']): p['requirement'] for p in probe['changed_pairs']}
    expected = {(p['fact_id'], Q.P.F.canonical(p['requirement'])) for p in probe['changed_pairs']}
    changed = set()
    for key, req in requirements.items():
        for fid, f in Q.facts_by_id().items():
            old, new = Q.evaluate_fact(f, req), C.evaluate_fact(Q, f, req)
            if old != new:
                assert not old and new
                changed.add((fid, key))
    assert changed == expected
    assert len(changed) == review['fully_classified_pairs'] == 187
    assert review['uncertain_or_entire_requested_target_exclusion_pairs'] == 0
