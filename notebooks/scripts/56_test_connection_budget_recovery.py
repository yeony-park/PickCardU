"""The approved connection exception must not cover any later unknown cost."""
import copy
import importlib.util
from pathlib import Path


def module():
    path=Path(__file__).with_name('56_connection_budget_recovery.py')
    spec=importlib.util.spec_from_file_location('recovery56_test',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_only_named_unknown_receipts_are_reserved_without_rewriting_actual_cost():
    m=module();raw=dict(known_cost_usd='.633330615',unknown_cost_records=2)
    original=copy.deepcopy(raw)
    adjusted=m.with_reservations(raw,{'old','new'},{'old':'.003127','new':'.0031075'})
    assert adjusted['known_cost_usd']=='0.639565115' and adjusted['unknown_cost_records']==0
    assert raw==original


def test_new_unknown_is_not_silently_added_to_the_exception():
    m=module();raw=dict(known_cost_usd='.633330615',unknown_cost_records=3)
    assert m.with_reservations(raw,{'old','new','third'},{'old':'.003127','new':'.0031075'})==raw


def test_parser_phase_reserves_only_its_own_unknown_not_old_stage51():
    m=module();raw=dict(known_cost_usd='.01',unknown_cost_records=1)
    adjusted=m.with_reservations(raw,{'new'},{'old':'.003127','new':'.0031075'})
    assert adjusted['known_cost_usd']=='0.0131075'
