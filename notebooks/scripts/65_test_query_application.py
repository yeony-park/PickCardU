"""Guard the saved evaluation paths against generic answers to same-card AND queries.

This reads the actual pre-answer registry and exercises the frozen scorer; no API.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'core65_query_application_tests', Path(__file__).with_name('63_core_evaluation.py'))
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


@pytest.fixture(scope='module')
def registry():
    return json.loads((ROOT / 'notebooks/data/65_fixed_profile_confirmation/source_registry.json').read_text())


@pytest.mark.parametrize('query_id', ['R14', 'R15'])
def test_generic_basic_discount_does_not_resolve_both_requested_benefits(registry, query_id):
    # A generic answer covers these four claims, but never connects either query target.
    card_key = 'BC/BC_KBank_SIMPLE'
    generic_claims = {
        card_key + ':basic_discount.' + field
        for field in ('target', 'spend', 'cap', 'exclusions')
    }
    card = next(row for row in registry['records']
                if row['query_id'] == query_id and row['card_key'] == card_key)
    paths = [path for path in card['core_paths'] if generic_claims <= set(path)]
    assert paths, 'The valid general-discount alternative must remain available.'
    source = {fact['condition_id']: fact for fact in card['facts']}
    for path in paths:
        conditions = [dict(source_status=source[condition]['source_status'],
                           input_status='supported',
                           answer_status='supported' if condition in generic_claims else 'absent')
                      for condition in path]
        assert not E.path_supported(dict(requirements=conditions), answer=True), (
            query_id + ': generic domestic/foreign discount claims must not resolve the two-target query')
