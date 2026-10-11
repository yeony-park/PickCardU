"""No API: catch invented conditions, lost intent and unsafe expansion."""
import copy
from types import SimpleNamespace
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('expansion_test', Path(__file__).with_name('41_query_expansion.py'))
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)
ALIASES = {'여행': ['여행', '항공', '호텔', '라운지'],
           '커피': ['커피', '카페', '스타벅스'], '카페': ['커피', '카페', '스타벅스']}

def requirement(target, action='any', **kwargs):
    return {'targets': [target], 'action': action, 'min_percent': None,
            'max_spend_krw': None, 'min_cap_krw': None, 'cap_period': 'any',
            'exclude_targets': [], **kwargs}

def plan(*groups, **kwargs):
    return {'route': 'structured_then_unstructured', 'groups': list(groups),
            'card_keys': [], 'unresolved': [], **kwargs}

def test_category_expansion_is_disclosed_and_preserves_original_question():
    result = X.expand_query('여행 혜택 카드', plan([requirement('여행')]), ALIASES)
    assert result['search_question'].startswith('여행 혜택 카드')
    assert result['added_terms'] == ['항공', '호텔', '라운지']
    assert all(r['relation'] == 'hierarchical_expansion' for r in result['relations'])

def test_unresolved_intent_uses_original_query():
    question = '생활비 25만원이면 좋은 카드'
    result = X.expand_query(question, plan(unresolved=['소비 예산은 전월실적이 아님']), ALIASES)
    assert result['search_question'] == question
    assert result['fallback_reason'] == 'unstructured_or_unresolved'

def test_parser_number_is_never_added_to_search_text():
    result = X.expand_query('커피 10% 할인 카드', plan([requirement('커피', 'discount', min_percent=50)]), ALIASES)
    assert '50' not in result['search_question']
    assert '스타벅스' in result['added_terms']

def test_unanchored_target_falls_back_instead_of_inventing_preference():
    result = X.expand_query('여행 카드', plan([requirement('병원')]), ALIASES)
    assert result['search_question'] == '여행 카드'
    assert result['fallback_reason'] == 'unanchored_target'

def test_unanchored_action_falls_back():
    result = X.expand_query('커피 적립 카드', plan([requirement('커피', 'discount')]), ALIASES)
    assert result['search_question'] == '커피 적립 카드'
    assert result['fallback_reason'] == 'unanchored_action'

def test_excluded_target_is_not_added_as_positive_expansion():
    result = X.expand_query('호텔을 제외한 여행 혜택', plan([requirement('여행', exclude_targets=['호텔'])]), ALIASES)
    assert '호텔' not in result['added_terms']
    assert '항공' in result['added_terms']

def test_or_groups_remain_or_in_serialized_search_text():
    result = X.expand_query('커피 또는 여행 혜택', plan([requirement('커피')], [requirement('여행')]), ALIASES)
    assert ') 또는 (' in result['search_question']

def test_expansion_does_not_mutate_explicit_card_scope_or_source_plan():
    value = plan([requirement('여행')], card_keys=['issuer/explicit_card'])
    before = copy.deepcopy(value)
    result = X.expand_query('여행 혜택', value, ALIASES)
    assert value == before
    assert 'explicit_card' not in result['search_question']

def test_budget_stop_happens_before_started_request_journal(tmp_path):
    from pydantic import BaseModel
    specification = importlib.util.spec_from_file_location('runtime_test', Path(__file__).with_name('41_followup_runtime.py'))
    runtime_module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(runtime_module)
    class Output(BaseModel):
        value: str
    runtime = runtime_module.Runtime.__new__(runtime_module.Runtime)
    runtime.path = tmp_path
    runtime.A = runtime_module.load('cache_under_test', '35_answer_experiment.py')
    runtime.A.F.ROOT = tmp_path
    runtime.A.F.STAGES[41] = tmp_path
    def stop(*args):
        raise runtime_module.ExperimentStop('budget exhausted')
    runtime.reserve = stop
    api = runtime.cache()
    with pytest.raises(runtime_module.ExperimentStop, match='budget exhausted'):
        api.parse(model='gpt-6-luna', instructions='Unique offline budget fixture', input=[], text_format=Output,
                  max_output_tokens=2400, tools=[], store=False)
    assert list((tmp_path / 'api_cache').glob('*.json')) == []

def test_modified_old_manifest_cannot_become_new_preservation_baseline(tmp_path, monkeypatch):
    specification = importlib.util.spec_from_file_location('boundary_test', Path(__file__).with_name('41_followup_runtime.py'))
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'DATA', tmp_path / 'data')
    stage = tmp_path / 'new'
    monkeypatch.setitem(module.STAGES, 41, stage)
    old = module.DATA / '34_bundle_comparison/protected_files.json'
    old.parent.mkdir(parents=True)
    old.write_text('{"files": []}')
    receipt = module.DATA / '40_new_query_comparison/authorized_document_change_binding.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"path":"docs/EVALUATION_METRICS.md", "after":{}}')
    runtime = module.Runtime.__new__(module.Runtime)
    runtime.path, runtime.stage = stage, 41
    runtime.F = SimpleNamespace(file_record=lambda p: {'path': str(p.relative_to(tmp_path)),
                                                       'sha256': module.digest(p.read_bytes())})
    with pytest.raises(RuntimeError, match='Original preservation snapshot changed'):
        runtime.snapshot()

def test_parser_expanded_or_targets_authorized_by_original_category():
    requirement_value = requirement('여행')
    requirement_value['targets'] = ['여행', '항공', '호텔', '라운지']
    result = X.expand_query('공항 라운지 같은 여행 혜택', plan([requirement_value]), ALIASES)
    assert result['fallback_reason'] is None
    assert result['added_terms'] == ['항공', '호텔']
    assert all(r['source_target'] == '여행' for r in result['relations'])

def test_merchant_only_does_not_authorize_expanding_to_whole_category():
    result = X.expand_query('호텔 할인', plan([requirement('여행', 'discount')]), ALIASES)
    assert result['search_question'] == '호텔 할인'
    assert result['fallback_reason'] == 'unanchored_target'

def test_specific_hotel_target_stays_specific_even_with_travel_context():
    result = X.expand_query('여행 갈 때 호텔 할인 카드', plan([requirement('호텔', 'discount')]), ALIASES)
    assert '항공' not in result['added_terms']
    assert '라운지' not in result['added_terms']
