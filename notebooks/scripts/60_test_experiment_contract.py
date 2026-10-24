"""Budget/reuse guards must fail before a paid request or a changed baseline."""
import copy
import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location('contracts60', Path(__file__).with_name('60_experiment_contract.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_combined_budget_counts_both_stages_and_reservations():
    m = module()
    m.check_budget('0.04', '0.70', '1.88', '0.01', 62)
    with pytest.raises(RuntimeError):
        m.check_budget('0.09', '0.89', '2.10', '0.03', 62)
    with pytest.raises(RuntimeError):
        m.check_budget('0.09', '0.00', '1.23', '0.02', 60)
    with pytest.raises(RuntimeError):
        m.check_budget('0.01', '0.01', '7.99', '0.02', 62)


def test_usage_unknown_never_becomes_zero_cost():
    with pytest.raises(RuntimeError):
        module().check_budget('0', '0', '0', '0', 62, unknown=1)


def fixture():
    return dict(query_id='N01', profile='benefit', repeat=1, question='커피',
                result=dict(evidence=[dict(chunk_id='a', card_key='card', text='커피')]))


def test_reuse_is_only_identical_payload_in_the_same_repeat():
    m = module(); a = fixture(); b = copy.deepcopy(a)
    assert m.same_payload(a, b, ['base', 'retry'])
    b['repeat'] = 2
    assert not m.same_payload(a, b, ['base', 'retry'])
    b = copy.deepcopy(a); b['result']['evidence'][0]['text'] = '커피 조건 변경'
    assert not m.same_payload(a, b, ['base', 'retry'])


def test_exact_matrix_rejects_missing_duplicate_and_wrong_modes():
    m = module(); rows = [dict(query_id='N01',profile='p',repeat=1,mode=mode)for mode in m.MODES]
    m.validate_matrix(rows, ['N01'], ['p'], [1])
    for changed in [rows[:-1], rows+[rows[0]], [{**rows[0], 'mode':'PAR'},*rows[1:]]]:
        with pytest.raises(ValueError):m.validate_matrix(changed, ['N01'], ['p'], [1])


def test_cached_score_binds_query_document_and_finite_value():
    m=module();row=dict(query_sha256='q',document_sha256='d',score=1.25)
    assert m.checked_score(row,'q','d')==1.25
    for bad in [dict(row,query_sha256='other'),dict(row,document_sha256='other'),dict(row,score=float('nan'))]:
        with pytest.raises(RuntimeError):m.checked_score(bad,'q','d')
