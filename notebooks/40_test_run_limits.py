"""Offline tests for paid-run stopping and embedding response preservation."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
s=importlib.util.spec_from_file_location('limits_under_test',Path(__file__).with_name('40_new_query_comparison.py'))
M=importlib.util.module_from_spec(s);sys.modules[s.name]=M;s.loader.exec_module(M)

def test_request_cap_is_fatal_before_next_record(monkeypatch):
    monkeypatch.setattr(M,'cost',lambda:{'summary':{'record_count':235,'known_cost_usd':'0'}})
    with pytest.raises(BaseException) as error:M.check_budget({'input':'질문'})
    assert type(error.value).__name__=='BudgetLimit'
    assert not isinstance(error.value,Exception)

def test_cost_cap_is_fatal_control_flow(monkeypatch):
    monkeypatch.setattr(M,'cost',lambda:{'summary':{'record_count':10,'known_cost_usd':'1'}})
    with pytest.raises(BaseException) as error:M.check_budget({'input':'질문'})
    assert type(error.value).__name__=='BudgetLimit'
    assert not isinstance(error.value,Exception)

def test_embedding_raw_response_preserved(monkeypatch,tmp_path):
    stage=tmp_path/'private_stage40';monkeypatch.setitem(M.C.STAGES,40,stage)
    monkeypatch.setitem(M.C.F.STAGES,40,stage)
    monkeypatch.setattr(M.C.F,'ROOT',tmp_path)
    monkeypatch.setattr(M,'check_budget',lambda *a,**k:None)
    vector=[0.0]*1536
    raw={'model':'text-embedding-3-small','data':[{'index':0,'embedding':vector}],
         'usage':{'prompt_tokens':2,'total_tokens':2},'object':'list'}
    response=SimpleNamespace(data=[SimpleNamespace(index=0,embedding=vector)],model_dump=lambda **k:raw)
    fake=SimpleNamespace(api_client=lambda:SimpleNamespace(embeddings=SimpleNamespace(create=lambda **k:response)))
    monkeypatch.setattr(M.A.F,'load_module',lambda *a,**k:fake)
    M.Embeddings().embeddings(['테스트'])
    record=M.C.read(next((stage/'api_cache').glob('embedding_batch_*.json')))
    assert record['raw_response']==raw
    assert record['raw_response_sha256']==M.C.digest(raw)

def test_optimized_python_rejected_before_import_or_provider():
    import subprocess
    result=subprocess.run([sys.executable,'-B','-O',str(Path(__file__).with_name('40_new_query_comparison.py'))],capture_output=True,text=True)
    assert result.returncode!=0 and 'Optimized Python is forbidden' in result.stderr

def test_budget_stop_does_not_create_started_record(monkeypatch,tmp_path):
    from pydantic import BaseModel
    class Output(BaseModel):text:str
    stage=tmp_path/'budget_stage';monkeypatch.setitem(M.C.STAGES,40,stage)
    monkeypatch.setattr(M,'cost',lambda:{'summary':{'record_count':235,'known_cost_usd':'0'}})
    api=M.RunCache()
    with pytest.raises(M.BudgetLimit):api.parse(model='gpt-6-luna',text_format=Output,input=[])
    assert not list(api.cache.glob('*.json'))
