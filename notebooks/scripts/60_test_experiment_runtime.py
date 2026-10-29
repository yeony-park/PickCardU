"""Offline integration of the new adapter with preserved typed/prompt guards."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import json
from types import SimpleNamespace

import pytest


@pytest.fixture(scope='module')
def runtime():
    spec=importlib.util.spec_from_file_location('runtime60_under_test',Path(__file__).with_name('60_62_experiment_runtime.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def test_parser_cache_keeps_new_parser_prompt_without_answer_variant(runtime):
    cache=runtime.Cache.__new__(runtime.Cache)
    cache.kind='parser';cache.policy='2400';cache.manifest={'prompt_variant_by_identity':{}};cache.identity='G01:parser:r1'
    value=cache.normalized(dict(instructions=runtime.R.NEW_PROMPT,text_format=runtime.Q.QueryPlan,input=[]))
    assert value['max_output_tokens']==3000 and value['instructions']==runtime.R.NEW_PROMPT
    assert value['reasoning']=={'effort':'medium'}


def test_answer_cache_keeps_existing_prompt_and_fixed_token_limit(runtime):
    cache=runtime.Cache.__new__(runtime.Cache)
    cache.kind='answer';cache.policy='2400';cache.manifest={'prompt_variant_by_identity':{'x':'old'}};cache.identity='x'
    value=cache.normalized(dict(instructions='unchanged',text_format=runtime.Q.QueryPlan,input=[]))
    assert value['max_output_tokens']==2400 and value['instructions']=='unchanged'


def toy(tmp_path):
    db=sqlite3.connect(tmp_path/'structured.sqlite')
    db.executescript('CREATE TABLE documents(document_id TEXT PRIMARY KEY); CREATE TABLE facts(fact_id TEXT PRIMARY KEY,document_id TEXT,ordinal INTEGER);')
    db.executemany('INSERT INTO documents VALUES(?)',[('both',),('coffee',),('netflix',)])
    data=[('a','both','커피'),('b','both','넷플릭스'),('c','coffee','커피'),('d','netflix','넷플릭스')]
    db.executemany('INSERT INTO facts VALUES(?,?,0)',[(fid,card)for fid,card,_ in data]);db.commit();db.close()
    facts={fid:dict(fact_id=fid,document_id=card,fields=dict(target=target,cap='',frequency=''),
        derived=dict(action_kind='discount',action_status='true',exceptions_normalized=''))for fid,card,target in data}
    return facts


def test_same_card_and_cannot_join_benefits_across_cards(runtime,tmp_path):
    facts=toy(tmp_path);q=runtime.Q
    plan=q.plan([q.requirement(['커피'],action='discount'),q.requirement(['넷플릭스'],action='discount')])
    result=runtime.K.query(q,plan,None,tmp_path,facts,repaired=True)
    assert result['eligible_cards']==['both']
    assert result['requirement_fact_ids']['both']['0']==[['a'],['b']]


def test_zero_result_reverts_to_original_scope_and_db_error_propagates(runtime,tmp_path):
    facts=toy(tmp_path);q=runtime.Q;scope=dict(card_keys=['coffee'],clarify=False)
    plan=q.plan([q.requirement(['넷플릭스'])])
    result=runtime.K.route(q,plan,scope,tmp_path,facts,repaired=True)
    assert result['route']=='U' and result['scope']==['coffee'] and result['db_attempted']
    (tmp_path/'structured.sqlite').unlink()
    with pytest.raises(sqlite3.OperationalError):runtime.K.route(q,plan,scope,tmp_path,facts,repaired=True)


def test_predicate_versions_are_not_global_mutations(runtime,tmp_path):
    facts=toy(tmp_path);q=runtime.Q;old=q.evaluate_fact;plan=q.plan([q.requirement(['커피'])])
    a=runtime.K.query(q,plan,None,tmp_path,facts,repaired=False)
    b=runtime.K.query(q,plan,None,tmp_path,facts,repaired=True)
    c=runtime.K.query(q,plan,None,tmp_path,facts,repaired=False)
    assert q.evaluate_fact is old and a['eligible_cards']==b['eligible_cards']==c['eligible_cards']


def test_free_use_any_does_not_become_discount_or_accrual(runtime,tmp_path):
    facts=toy(tmp_path);fact=facts['a'];fact['fields']['target']='라운지'
    fact['derived'].update(action_kind=None,action_status='true')
    assert runtime.K.evaluate_fact(runtime.Q,fact,runtime.Q.requirement(['라운지'],action='any'))
    for kind in ['discount','earn']:
        assert not runtime.K.evaluate_fact(runtime.Q,fact,runtime.Q.requirement(['라운지'],action=kind))


def test_inventory_rejects_mutation_and_new_source_members(runtime,tmp_path):
    directory=tmp_path/'source';directory.mkdir();original=directory/'index';original.write_text('original')
    inventory=dict(files_sha256={'source/index':runtime.P.sha(original.read_bytes())},
                   directory_members={'source':['source/index']})
    runtime.guard_inventory(inventory,root=tmp_path)
    original.write_text('tampered')
    with pytest.raises(RuntimeError):runtime.guard_inventory(inventory,root=tmp_path)
    original.write_text('original');(directory/'new-cache').write_text('unfrozen')
    with pytest.raises(RuntimeError):runtime.guard_inventory(inventory,root=tmp_path)


def test_score_replay_rejects_tampered_inherited_cache(runtime,tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,'ROOT',tmp_path)
    old=tmp_path/'old';old.mkdir();current=tmp_path/'current';current.mkdir()
    monkeypatch.setattr(runtime,'CACHE_SOURCES',[old])
    key=runtime.P.sha(dict(mode='bge',q='q',doc='document',kwargs={},contract={}))
    path=old/(key+'.json')
    path.write_text(json.dumps(dict(query_sha256=runtime.P.sha('q'),document_sha256=runtime.P.sha('document'),score=1.0)))
    instance=runtime.Runtime61.__new__(runtime.Runtime61)
    instance.path=current;instance.reranker_contract={};instance.scorer_calls=[]
    instance.delegate=SimpleNamespace(artifact_contract=lambda mode:{})
    instance.guard=SimpleNamespace(lengths=lambda q,d:dict(pair_tokens=4,document_tokens=2),max_length=8192)
    instance.inventory=dict(files_sha256={str(path.relative_to(tmp_path)):runtime.P.sha(path.read_bytes())})
    assert instance.score('bge','q',['document'])[0]==[1.0]
    value=json.loads(path.read_text());value['score']=9;path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError):instance.score('bge','q',['document'])
    instance.inventory['files_sha256'][str(path.relative_to(tmp_path))]=runtime.P.sha(path.read_bytes())
    value['document_sha256']='wrong';path.write_text(json.dumps(value))
    instance.inventory['files_sha256'][str(path.relative_to(tmp_path))]=runtime.P.sha(path.read_bytes())
    with pytest.raises(RuntimeError):instance.score('bge','q',['document'])
