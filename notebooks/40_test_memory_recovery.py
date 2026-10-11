"""Resource-only adapter must preserve exact original documents and token guards."""
import importlib.util
from pathlib import Path
import sys
import pytest
s=importlib.util.spec_from_file_location('recovery_under_test',Path(__file__).with_name('40_memory_recovery.py'))
M=importlib.util.module_from_spec(s);sys.modules[s.name]=M;s.loader.exec_module(M)

def test_original_score_receives_one_whole_document_in_order():
    class Delegate:
        calls=[]
        def score(self,mode,question,documents,**kwargs):
            self.calls.append((mode,question,list(documents),kwargs))
            return [float(len(documents[0]))],{'device':'cpu','dtype':'float32','batch_count':1,'input_token_count':len(documents[0]),'truncated_count':0}
    original=Delegate();adapter=M.OneDocumentReranker(original)
    docs=['가'*4000,'나'*100,'다'*20];scores,trace=adapter.score('bge','질문',docs,document_token_limit=4096)
    assert scores==[4000.0,100.0,20.0]
    assert [c[2] for c in original.calls]==[[d] for d in docs]
    assert all(c[3]=={'document_token_limit':4096} for c in original.calls)
    assert trace['batch_size']==1 and trace['truncated_count']==0

def test_guard_failure_is_not_swallowed_or_skipped():
    class Delegate:
        def score(self,*args,**kwargs):raise ValueError('document exceeds original limit')
    with pytest.raises(ValueError,match='original limit'):M.OneDocumentReranker(Delegate()).score('bge','질문',['원문'])

def test_recovery_contract_keeps_original_snapshot_on_second_resume(tmp_path):
    import json,hashlib
    class Common:
        ROOT=tmp_path
        @staticmethod
        def digest(value):
            if not isinstance(value,bytes):value=json.dumps(value,sort_keys=True).encode()
            return hashlib.sha256(value).hexdigest()
        @staticmethod
        def read(path):return json.loads(path.read_text())
        @staticmethod
        def freeze(path,value):
            result={**value,'sha256':Common.digest(value)};path.write_text(json.dumps(result));return result
    cached=tmp_path/'old.json';cached.write_text('unchanged')
    first={'source_protocol_sha256':'p','runner_sha256':'r','adapter_sha256':'a','parity_sha256':'v',
           'completed_keys':['one'],'pending_keys':['two'],'before_manifest':{'old.json':Common.digest(cached.read_bytes())},
           'completed_rows':1,'api_records':1,'old_batch_size':2,'new_max_documents_per_call':1}
    path=tmp_path/'contract.json';bound=M.bind_recovery_contract(Common,path,first)
    later={**first,'completed_keys':['one','two'],'pending_keys':[],'completed_rows':2,'api_records':2}
    assert M.bind_recovery_contract(Common,path,later)==bound
    cached.write_text('changed')
    with pytest.raises(RuntimeError,match='snapshot'):M.bind_recovery_contract(Common,path,later)

def test_recovery_contract_rejects_changed_adapter(tmp_path):
    import json
    class Common:
        ROOT=tmp_path
        @staticmethod
        def read(path):return json.loads(path.read_text())
    path=tmp_path/'contract.json';path.write_text(json.dumps({'source_protocol_sha256':'p','runner_sha256':'r','adapter_sha256':'a','parity_sha256':'v'}))
    with pytest.raises(RuntimeError,match='binding'):
        M.bind_recovery_contract(Common,path,{'source_protocol_sha256':'p','runner_sha256':'r','adapter_sha256':'changed','parity_sha256':'v'})

def test_memory_reserve_covers_observed_increment_and_stops_before_call():
    assert M.required_headroom_kib(800000,1843616)==1567904
    assert M.required_headroom_kib(1843616,1843616)==1048576
    with pytest.raises(M.MemoryStop):M.check_memory_headroom(700000,1843616,1843616)
    assert M.check_memory_headroom(1600000,800000,1843616)==1567904

def test_parity_baseline_preflight_rejects_changed_file(tmp_path):
    import hashlib,json
    class Common:
        ROOT=tmp_path
        @staticmethod
        def digest(value):return hashlib.sha256(value).hexdigest()
        @staticmethod
        def read(path):return json.loads(path.read_text())
    path=tmp_path/'baseline.json';path.write_text('{"score":1.25}')
    row={'baseline_cache_path':'baseline.json','baseline_cache_sha256':Common.digest(path.read_bytes()),'cached_score':1.25}
    M.validate_parity_baselines(Common,{'records':[row]})
    path.write_text('{"score":2.25}')
    with pytest.raises(RuntimeError,match='baseline'):M.validate_parity_baselines(Common,{'records':[row]})
