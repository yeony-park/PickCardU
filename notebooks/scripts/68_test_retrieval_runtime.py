"""Scoped search invariants with the real checkpoint serializer and fake U."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

s=importlib.util.spec_from_file_location('retrieval68_test',Path(__file__).with_name('68_69_retrieval_runtime.py'))
R=importlib.util.module_from_spec(s);s.loader.exec_module(R);T=R.T


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(T,'ROOT',tmp_path)
    for common in T.stage_registries():
        monkeypatch.setattr(common,'ROOT',tmp_path);monkeypatch.setitem(common.STAGES,68,tmp_path/'stage68')
    source=tmp_path/'source.txt';source.write_text('fixed')
    manifest=dict(source_sha256={'source.txt':T.E.file_digest(source)},notebook_phase_code_sha256={})
    calls=[]
    def search(question,vector,profile,*,scope):
        calls.append(scope)
        key=(scope or ['a'])[0]
        return dict(cards=[dict(card_key=key)],evidence=[dict(card_key=key,text='커피')])
    runtime=SimpleNamespace(path=tmp_path/'stage68',manifest=manifest,search=search)
    question=dict(query_id='Q',question='커피 할인 카드')
    return runtime,question,np.zeros(1536,dtype=np.float32),calls


def test_positive_scope_has_one_U_call_and_never_a_global_union(setup):
    runtime,q,v,calls=setup
    route=dict(scope=['a'],route='SEQ',db_wall_seconds=.1)
    first=R.retrieve(runtime,q,v,'benefit',1,route,dict(card_keys=None,clarify=False))
    second=R.retrieve(runtime,q,v,'benefit',2,route,dict(card_keys=None,clarify=False))
    assert calls==[['a']] and second['reused_search_identity']==first['identity']
    assert first['result']==second['result']


def test_zero_SQL_falls_back_to_original_scope_once(setup):
    runtime,q,v,calls=setup
    R.retrieve(runtime,q,v,'benefit',1,dict(scope=['b'],route='U'),dict(card_keys=['b'],clarify=False))
    assert calls==[['b']]


def test_checkpoint_seal_or_source_change_stops_reuse(setup):
    runtime,q,v,calls=setup
    route=dict(scope=['a'],route='SEQ');original=dict(card_keys=None,clarify=False)
    row=R.retrieve(runtime,q,v,'benefit',1,route,original)
    path=runtime.path/'retrievals'/(row['identity']+'.json')
    bad=json.loads(path.read_text());bad['result']['evidence'][0]['text']='changed';path.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError,match='checkpoint'):R.retrieve(runtime,q,v,'benefit',1,route,original)
    assert len(calls)==1
    (T.ROOT/'source.txt').write_text('changed source')
    with pytest.raises(ValueError,match='changed'):R.retrieve(runtime,q,v,'benefit',2,route,original)


def test_U_scope_escape_is_rejected_before_checkpoint(setup):
    runtime,q,v,calls=setup
    with pytest.raises((RuntimeError,ValueError)):
        R.retrieve(runtime,q,v,'benefit',1,dict(scope=['a']),dict(card_keys=['b'],clarify=False))


def test_new_process_resumes_prior_search_as_verified_reuse(setup):
    runtime,q,v,calls=setup;route=dict(scope=['a'],route='SEQ');original=dict(card_keys=None,clarify=False)
    first=R.retrieve(runtime,q,v,'benefit',1,route,original)
    del runtime.reused_searches
    assert R.retrieve(runtime,q,v,'benefit',1,route,original)==first
    row=R.retrieve(runtime,q,v,'benefit',2,route,original)
    assert len(calls)==1 and row['reused_search_identity']==first['identity']
