"""Version binding and conservative combined-budget boundary checks."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal

import pytest


def runtime():
    path = Path(__file__).with_name('56_structured_experiment_runtime.py')
    spec = importlib.util.spec_from_file_location('runtime56_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sql_udf_uses_copy_facts_and_restores_original_even_after_failure():
    m = runtime()
    original = lambda: {'original': 1}
    q = SimpleNamespace(facts_by_id=original)
    with pytest.raises(RuntimeError):
        with m.bound_facts(q, {'copy': 2}):
            assert q.facts_by_id() == {'copy': 2}
            raise RuntimeError('SQL failed')
    assert q.facts_by_id is original


def test_unknown_usage_stops_even_when_nominal_budget_is_large():
    m = runtime()
    with pytest.raises(RuntimeError, match='Unknown'):
        m.require_budget(dict(known_cost_usd='0', unknown_cost_records=1), Decimal('.001'), '8')


def test_combined_parser_allocation_reserves_next_request_before_call():
    m = runtime()
    spent = dict(known_cost_usd='.499', unknown_cost_records=0)
    with pytest.raises(RuntimeError, match='allocation'):
        m.require_budget(spent, Decimal('.002'), '.50')
    m.require_budget(spent, Decimal('.001'), '.50')


def test_same_card_and_uses_separate_benefit_rows_but_never_different_cards(tmp_path):
    import sqlite3
    m = runtime()
    db = sqlite3.connect(tmp_path / 'structured.sqlite')
    db.executescript('CREATE TABLE documents(document_id TEXT); CREATE TABLE facts(fact_id TEXT,document_id TEXT,ordinal INTEGER);')
    db.executemany('INSERT INTO documents VALUES(?)', [('both',), ('coffee_only',), ('netflix_only',)])
    rows = [('a','both',1,'커피'), ('b','both',2,'넷플릭스'),
            ('c','coffee_only',1,'커피'), ('d','netflix_only',1,'넷플릭스')]
    db.executemany('INSERT INTO facts VALUES(?,?,?)', [r[:3] for r in rows]); db.commit(); db.close()
    facts = {fid:dict(fact_id=fid, fields=dict(target=target, frequency='', cap=''), derived=dict(
        target_normalized=target, action_kind='discount', action_status='true', exceptions_normalized=''))
        for fid, card, ordinal, target in rows}
    plan = m.Q.plan([m.Q.requirement(['커피']), m.Q.requirement(['넷플릭스'])])
    result = m.query_database(plan, None, tmp_path, facts=facts)
    assert result['eligible_cards'] == ['both']
    assert result['matching_fact_ids']['both'] == ['a','b']


def test_db_technical_error_is_not_reported_as_zero_matches(tmp_path):
    m = runtime()
    import sqlite3
    with pytest.raises(sqlite3.Error):
        m.query_database(m.Q.plan([]), None, tmp_path, facts={})


def test_short_detail_condition_review_is_bound_to_both_texts_and_equal_signatures():
    import copy
    m = runtime()
    row = dict(query_id='Q', short_question='커피 할인 카드 찾아줘.',
               detailed_question='커피 할인 혜택이 있는 카드를 찾아줘.')
    signature = dict(requirements=[dict(targets=['커피'],action='discount')],numeric={})
    row['condition_review'] = {form:dict(question_sha256=m.P.sha(row[form+'_question']),
        explicit_condition_signature=copy.deepcopy(signature), reviewer='main Codex',
        reason='혜택 대상과 할인 조건이 같고 숫자 제약은 없다.') for form in ['short','detailed']}
    m.validate_question_pairs([row])
    changed = copy.deepcopy(row)
    changed['condition_review']['short']['explicit_condition_signature']['numeric']['min_percent'] = 10
    with pytest.raises(RuntimeError, match='condition'):
        m.validate_question_pairs([changed])
    changed = copy.deepcopy(row);changed['short_question'] += '월 3회 이상'
    with pytest.raises(RuntimeError, match='text'):
        m.validate_question_pairs([changed])
