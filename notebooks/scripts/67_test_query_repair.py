"""Read-only regression evidence for candidate target projection in experiment67."""
import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('repair67_under_test', Path(__file__).with_name('67_query_repair.py'))
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)


@pytest.mark.parametrize('target,expected', [
    ('치과 진료비', ['치과 진료비', '치과']),
    ('반려동물 진료비', ['반려동물 진료비', '반려동물']),
    ('헬스장 회비', ['헬스장 회비', '헬스장']),
    ('치과진료비', ['치과진료비', '치과']),
])
def test_explicit_target_is_recovered_without_adding_a_synonym(target, expected):
    assert K.target_candidates(target) == expected


@pytest.mark.parametrize('target', ['기차', '카피', '극장', '인터넷 서점', '음식 배달', '전기요금', '전기 요금', '배달비', '연회비'])
def test_other_targets_and_fees_are_not_silently_rewritten(target):
    assert K.target_candidates(target) == [target]


def test_only_positive_targets_change_and_the_input_is_immutable():
    requirement = {'targets': ['치과 진료비'], 'action': 'discount', 'min_percent': 10,
                   'max_spend_krw': 30000, 'min_cap_krw': 10000, 'cap_period': 'month',
                   'exclude_targets': ['치과 진료비'], 'min_frequency': 2, 'frequency_period': 'month'}
    saved = copy.deepcopy(requirement)
    actual = K.compile_requirement(requirement)
    assert requirement == saved
    assert actual['targets'] == ['치과 진료비', '치과']
    assert {k: v for k, v in actual.items() if k != 'targets'} == {k: v for k, v in saved.items() if k != 'targets'}


@pytest.fixture(scope='module')
def runtime():
    return K.load_runtime()


@pytest.fixture(scope='module')
def facts(runtime):
    return runtime.R.fact_map(runtime.M.DB_NEW)


def test_real_pet_rows_are_recovered_without_changing_action_or_numeric_filters(runtime, facts):
    requirement = runtime.Q.requirement(['반려동물 진료비'], action='discount')
    plan = runtime.Q.plan([requirement])
    baseline = K.query(runtime, plan, ['ibk/IBK_iAll'], runtime.M.DB_NEW, facts, matcher='baseline')
    projected = K.query(runtime, plan, ['ibk/IBK_iAll'], runtime.M.DB_NEW, facts, matcher='projected')
    assert baseline['eligible_cards'] == []
    assert projected['eligible_cards'] == ['ibk/IBK_iAll']
    assert projected['plan'] == plan
    for fid in projected['matching_fact_ids']['ibk/IBK_iAll']:
        assert '동물' in facts[fid]['fields']['target']
        assert facts[fid]['derived']['percent'] in [1.0, 1.5]
    for changes in [{'action': 'earn'}, {'min_percent': 5}, {'exclude_targets': ['동물']}, {'max_spend_krw': 0}]:
        result = K.query(runtime, runtime.Q.plan([{**requirement, **changes}]), ['ibk/IBK_iAll'], runtime.M.DB_NEW, facts, matcher='projected')
        assert result['eligible_cards'] == []


def test_same_card_and_is_not_replaced_by_partial_hits(runtime, facts):
    plan = runtime.Q.plan([runtime.Q.requirement(['커피'], action='discount'),
                           runtime.Q.requirement(['이 DB에 없는 검증용 대상'], action='discount')])
    assert K.query(runtime, plan, None, runtime.M.DB_NEW, facts, matcher='projected')['eligible_cards'] == []


def test_issuer_scope_empty_groups_and_unsupported_constraints_stay_binding(runtime, facts):
    plan = runtime.Q.plan([runtime.Q.requirement(['반려동물 진료비'], action='discount')])
    for scope in [[], ['BC/BC_Green_v2']]:
        assert K.query(runtime, plan, scope, runtime.M.DB_NEW, facts, matcher='projected')['eligible_cards'] == []
    for other in [runtime.Q.plan([]), {**plan, 'unsupported_filters': ['아직 표현하지 못하는 명시 조건']}]:
        assert K.query(runtime, other, None, runtime.M.DB_NEW, facts, matcher='projected')['eligible_cards'] == []


def test_no_spend_is_not_equated_with_unknown_or_amount_text(runtime, facts):
    known_zero = [f for f in facts.values() if f['derived']['spend_state'] == 'known' and f['derived']['spend_threshold_krw'] == 0]
    unknown = [f for f in facts.values() if f['derived']['spend_state'] == 'unknown']
    assert known_zero and unknown
    for f in [known_zero[0], unknown[0]]:
        requirement = runtime.Q.requirement([f['fields']['target']], max_spend_krw=0)
        actual = runtime.M.K.evaluate_fact(runtime.Q, f, requirement)
        if f['derived']['spend_state'] == 'unknown':
            assert not actual


def test_disjoint_cards_cannot_supply_each_others_and_witnesses(runtime, facts, tmp_path):
    import sqlite3

    examples = {}
    for key, target in [('coffee', '커피'), ('ott', '넷플릭스')]:
        requirement = runtime.Q.requirement([target], action='discount')
        examples[key] = copy.deepcopy(next(f for f in facts.values()
            if runtime.M.K.evaluate_fact(runtime.Q, f, requirement)))
    examples['coffee'].update(fact_id='coffee', document_id='issuer/A')
    examples['ott'].update(fact_id='ott', document_id='issuer/B')
    connection = sqlite3.connect(tmp_path / 'structured.sqlite')
    connection.executescript('CREATE TABLE documents(document_id TEXT); CREATE TABLE facts(fact_id TEXT, document_id TEXT, ordinal INTEGER);')
    connection.executemany('INSERT INTO documents VALUES (?)', [('issuer/A',), ('issuer/B',)])
    connection.executemany('INSERT INTO facts VALUES (?,?,?)', [(k, f['document_id'], 1) for k, f in examples.items()])
    connection.commit()
    plan = runtime.Q.plan([runtime.Q.requirement(['커피'], action='discount'), runtime.Q.requirement(['넷플릭스'], action='discount')])
    assert K.query(runtime, plan, None, tmp_path, examples, matcher='projected')['eligible_cards'] == []
    examples['same_card_ott'] = {**examples['ott'], 'fact_id': 'same_card_ott', 'document_id': 'issuer/A'}
    connection.execute('INSERT INTO facts VALUES (?,?,?)', ('same_card_ott', 'issuer/A', 2))
    connection.commit()
    connection.close()
    assert K.query(runtime, plan, None, tmp_path, examples, matcher='projected')['eligible_cards'] == ['issuer/A']
