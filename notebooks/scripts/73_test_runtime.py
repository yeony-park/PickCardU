"""Safety checks for the new card-only answer condition."""
import importlib.util
import json
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location('runtime73_test', Path(__file__).with_name('73_answer_runtime.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_card_only_rejects_any_structured_suffix():
    m = module()
    result = {'cards': [{'card_key': 'a'}], 'evidence': [{'card_key': 'a', 'chunk_id': 's68:x', 'origin': 'structured'}]}
    with pytest.raises(ValueError, match='(?i)structured'):
        m.validate_card_only(result)


def test_card_only_rejects_evidence_owned_by_other_card():
    m = module()
    result = {'cards': [{'card_key': 'a'}], 'evidence': [{'card_key': 'b', 'chunk_id': 'chunk'}]}
    with pytest.raises(ValueError, match='owner'):
        m.validate_card_only(result)


def test_unaccepted_gate_stops_before_the_provider(tmp_path):
    m = module()
    (tmp_path / 'pre_paid_acceptance.json').write_text(json.dumps({'status': 'pending'}))
    with pytest.raises(RuntimeError, match='not accepted'):
        m.require_gate(tmp_path)


def test_budget_reservation_rejects_unknown_and_over_cap():
    m = module()
    with pytest.raises(RuntimeError, match='unknown'):
        m.check_total_budget({'unknown_cost_records': 1, 'budget_debit_usd': '1'}, '1')
    with pytest.raises(RuntimeError, match='8'):
        m.check_total_budget({'unknown_cost_records': 0, 'budget_debit_usd': '7.9'}, '.2')


def test_accepted_gate_cannot_omit_required_job_or_contract(tmp_path):
    m = module()
    for missing in ('notebooks/data/73_repaired_card_scope/jobs.json',
                    'notebooks/data/73_repaired_card_scope/answer_payload_contract.json'):
        files = {p: 'x' for p in m.REQUIRED_GATE_FILES if p != missing}
        (tmp_path / 'pre_paid_acceptance.json').write_text(json.dumps({
            'status': 'accepted', 'independent_review_accepted': True, 'files_sha256': files}))
        with pytest.raises(ValueError, match='required'):
            m.require_gate(tmp_path)


def test_accepted_gate_rejects_duplicate_json_keys(tmp_path):
    m = module()
    (tmp_path / 'pre_paid_acceptance.json').write_text('{"status":"accepted","status":"accepted"}')
    with pytest.raises(ValueError, match='duplicate'):
        m.require_gate(tmp_path)


def test_exact_matrix_rejects_changed_metadata_with_same_count():
    import copy
    m = module()
    jobs = m.read(m.BASE / 'jobs.json')['records']
    assert m.validate_matrix(jobs)
    modified = copy.deepcopy(jobs)
    modified[0]['query_id'] = 'C99'
    with pytest.raises(ValueError, match='matrix'):
        m.validate_matrix(modified)
