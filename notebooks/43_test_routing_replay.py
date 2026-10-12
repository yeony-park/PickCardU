"""Check frozen expected routing logic, distinct from semantic safety."""
import importlib.util
from pathlib import Path
import sys
import json
import pytest

spec=importlib.util.spec_from_file_location('replay43_tests',Path(__file__).with_name('43_routing_replay.py'))
V=importlib.util.module_from_spec(spec);sys.modules[spec.name]=V;spec.loader.exec_module(V)
FIXTURE=json.loads((Path(__file__).parent/'data/43_offline_validation/routing_fixtures.json').read_text())

@pytest.mark.parametrize('case',FIXTURE['cases'],ids=lambda c:c['name'])
def test_frozen_route_and_selector_expectations(case):
    row=V.replay(case['plan'],FIXTURE['facts'],FIXTURE['catalog'],parser_failed=case['parser_failed'])
    for field,wanted in case['expected'].items():
        assert row[field]==wanted,field

def test_nonempty_wrong_action_does_not_fall_back_and_loses_all_correct_cards():
    case=next(c for c in FIXTURE['cases'] if c['risk_kind']=='semantic_action')
    row=V.replay(case['plan'],FIXTURE['facts'],FIXTURE['catalog'])
    assert row['fallback'] is False
    assert not set(row['eligible_cards']) & set(case['oracle_eligible_cards'])

def test_original_plan_is_unchanged():
    import copy
    plan=copy.deepcopy(FIXTURE['cases'][0]['plan']);before=copy.deepcopy(plan)
    V.replay(plan,FIXTURE['facts'],FIXTURE['catalog'])
    assert plan==before
