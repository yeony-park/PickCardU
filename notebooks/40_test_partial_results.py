"""Partial audits must count real rows and preserve the first recovery boundary."""
import importlib.util
from pathlib import Path
import sys
import pytest

spec=importlib.util.spec_from_file_location('partial_verifier_test',Path(__file__).with_name('40_verify_results.py'))
M=importlib.util.module_from_spec(spec);sys.modules[spec.name]=M;spec.loader.exec_module(M)

def fixture():
    dataset={'queries':[{'query_id':'Q1'},{'query_id':'Q2'}]}
    protocol={'profiles':['p'],'modes':['U','S_THEN_U','S_PLUS_U'],'sha256':'frozen'}
    binding={'completed_keys':['Q1_p_U.json']}
    retrieval={key:{'status':'ok','protocol_sha256':'frozen'} for key in ['Q1_p_U.json','Q1_p_S_THEN_U.json','Q2_p_U.json']}
    answers={key:{'status':'ok','protocol_sha256':'frozen','retrieval_sha256':M.C.digest(row)} for key,row in retrieval.items()}
    summary={'execution_complete':False,'expected':6,'completed_answers':3,'completed_retrievals':3,'pending_answers':3}
    return summary,binding,protocol,dataset,retrieval,answers

def test_additional_completed_rows_after_initial_recovery_are_valid():
    assert M.validate_partial_grid(*fixture())==6

def test_declared_count_cannot_exceed_actual_results():
    args=fixture();args[0]['completed_answers']=4
    with pytest.raises(AssertionError):M.validate_partial_grid(*args)

def test_initial_completed_row_cannot_be_lost():
    args=fixture();args[4].pop('Q1_p_U.json');args[5].pop('Q1_p_U.json')
    args[0].update(completed_answers=2,completed_retrievals=2,pending_answers=4)
    with pytest.raises(AssertionError):M.validate_partial_grid(*args)

def test_unregistered_question_cannot_inflate_progress():
    args=fixture();args[4]['Q9_p_U.json']=args[4].pop('Q2_p_U.json');args[5]['Q9_p_U.json']=args[5].pop('Q2_p_U.json')
    with pytest.raises(AssertionError):M.validate_partial_grid(*args)

def test_answer_must_remain_bound_to_its_actual_retrieval():
    args=fixture();args[4]['Q2_p_U.json']['new_field']='modified'
    with pytest.raises(AssertionError):M.validate_partial_grid(*args)
