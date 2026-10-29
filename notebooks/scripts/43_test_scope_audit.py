"""Scope must be proved by components and owners, not a null inner trace."""
import importlib.util
from pathlib import Path
import sys
import copy
import pytest

spec=importlib.util.spec_from_file_location('scope43_tests',Path(__file__).with_name('43_scope_audit.py'))
V=importlib.util.module_from_spec(spec);sys.modules[spec.name]=V;spec.loader.exec_module(V)

def fixture():
    component={'identity':{'scope':['A']},'bm25':[{'chunk_id':'good'}],'vector':[{'chunk_id':'good'}]}
    component['sha256']=V.C.R.digest(component)
    result={'trace':{'bm25':[{'chunk_id':'good'}],'vector':[{'chunk_id':'good'}],'scope':{'target_card_keys':None}},
            'cards':[{'card_key':'A'}],'evidence':[{'card_key':'A','chunk_id':'good'}]}
    return component,{'good':'A','bad':'B'},result

def test_legitimate_outer_scope_with_null_inner_trace_is_accepted():
    assert V.check_scope(['A'],*fixture()) is True

@pytest.mark.parametrize('mutation',['identity','digest','candidate_owner','trace','final_evidence','forged_owner_label'])
def test_scope_escape_or_stale_trace_is_rejected(mutation):
    component,owners,result=fixture()
    if mutation=='identity':component['identity']['scope']=['B']
    elif mutation=='digest':component['sha256']='0'*64
    elif mutation=='candidate_owner':
        component['bm25']=[{'chunk_id':'bad'}];result['trace']['bm25']=[{'chunk_id':'bad'}]
        component['sha256']=V.C.R.digest({k:v for k,v in component.items() if k!='sha256'})
    elif mutation=='trace':result['trace']['vector']=[{'chunk_id':'bad'}]
    elif mutation=='final_evidence':result['evidence'][0]['card_key']='B'
    else:result['evidence'][0]['chunk_id']='bad'
    with pytest.raises(RuntimeError):V.check_scope(['A'],component,owners,result)
