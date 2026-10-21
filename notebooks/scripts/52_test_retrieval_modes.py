"""Offline provenance, full union, packaging and evidence-ablation checks."""
from pathlib import Path
import importlib.util
import sys
import copy

spec = importlib.util.spec_from_file_location("modes52_test", Path(__file__).with_name("52_retrieval_modes.py"))
M = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = M
spec.loader.exec_module(M)


def unit(i, origin="structured", text="커피 할인"):
    return dict(unit_id=f"{origin}:{i}",chunk_id=f"id{i}",card_key=f"issuer/card{i}",card_name=f"카드{i}",
        issuer="발급사",text=text,origin=origin,page_num=1,section="커피",level="benefit")


def test_union_keeps_all_sql_cards_above_twenty():
    sql = [unit(i) for i in range(25)]
    unstructured = [unit(0,"unstructured"),unit(26,"unstructured")]
    pool = M.union_units(sql,unstructured)
    assert len(pool)==27 and len({u['card_key'] for u in pool})==26
    assert pool[0]['origin']=='structured' and pool[-2]['origin']=='unstructured'
    assert len(M.union_units(pool,pool))==len(pool)


def test_common_rank_uses_max_then_card_key_and_reports_opportunity_counts():
    a,b,c=unit(1),unit(2),unit(3)
    a2={**a,'unit_id':'structured:another','chunk_id':'another'}
    r=M.pack_ranked('커피 할인 카드',[a,a2,b,c],[.3,.9,.9,.1])
    assert [c['card_key'] for c in r['cards']]==[a['card_key'],b['card_key'],c['card_key']]
    assert r['trace']['unit_count_by_card'][a['card_key']]==2


def test_supplement_preserves_all_original_evidence_and_card_order():
    route=M.Q.query(M.Q.plan([M.Q.requirement(['커피'],action='discount')]),scope=['BC/BC_Green_v2'])
    units=M.structured_units(route)
    evidence=[{**units[0],'chunk_id':'unchanged_U','text':'기존 검색 근거 전체'}]
    result=dict(cards=[dict(card_key='BC/BC_Green_v2')],evidence=evidence,trace={})
    before=copy.deepcopy(result)
    got=M.supplement('커피 월 횟수와 한도',result,route)
    assert result==before and got['cards']==result['cards']
    assert got['evidence'][:1]==before['evidence'] and len(got['evidence'])>1


def test_scope_assertion_fails_instead_of_silently_dropping_leaked_evidence():
    import pytest
    with pytest.raises(RuntimeError,match='escaped'):
        M.assert_scope(dict(cards=[],evidence=[dict(card_key='ibk/card')]),['BC/card'])


def test_payload_bound_skips_whole_db_units_without_evicting_U():
    route=M.Q.query(M.Q.plan([M.Q.requirement(['커피'],action='discount')]),scope=['BC/BC_Green_v2'])
    result=dict(cards=[dict(card_key='BC/BC_Green_v2')],evidence=[dict(card_key='BC/BC_Green_v2',card_name='그린카드',issuer='비씨카드',text='a'*63500)],trace={})
    got=M.supplement('커피',result,route)
    assert got['evidence']==result['evidence']
    assert got['supplement']['eligible_fact_count']>0 and got['supplement']['appended_fact_ids']==[]


def setup_contract(monkeypatch,tmp_path):
    monkeypatch.setattr(M.P.F,'ROOT',tmp_path)
    monkeypatch.setitem(M.P.F.STAGES,52,tmp_path/'stage52')
    manifest=dict(model='gpt-6-luna',reasoning='medium')
    M.P.F.write_json(tmp_path/'stage52/execution_manifest.json',manifest)
    q=dict(query_id='T01',question='커피 할인')
    parser=dict(parser={'plan':'fixed'},original_scope=dict(card_keys=['issuer/card1']),
        routing=dict(scope=['issuer/card1'],eligible_cards=['issuer/card1'],matching_fact_ids={'issuer/card1':['fact1']}))
    row=dict(identity='T01:common:U:r1',query_id='T01',profile='common',mode='U',repeat=1,
        manifest_sha256=M.P.sha(manifest),original_scope=parser['original_scope'],route=None,
        result=dict(cards=[dict(card_key='issuer/card1')],evidence=[unit(1)]))
    return q,parser,manifest,row


def test_modified_saved_retrieval_cannot_become_a_new_paid_payload(monkeypatch,tmp_path):
    import pytest
    q,parser,manifest,row=setup_contract(monkeypatch,tmp_path)
    M.bind_retrieval(52,row,question=q,profile='common',mode='U',parser_record=parser,manifest=manifest,new=True)
    row['result']['evidence'][0]['text']='수정된 근거'
    with pytest.raises(RuntimeError,match='first-write contract'):
        M.bind_retrieval(52,row,question=q,profile='common',mode='U',parser_record=parser,manifest=manifest)


def test_changed_parser_or_mode_blocks_saved_retrieval(monkeypatch,tmp_path):
    import pytest
    q,parser,manifest,row=setup_contract(monkeypatch,tmp_path)
    M.bind_retrieval(52,row,question=q,profile='common',mode='U',parser_record=parser,manifest=manifest,new=True)
    changed=copy.deepcopy(parser);changed['parser']['plan']='different'
    with pytest.raises(RuntimeError,match='first-write contract'):
        M.bind_retrieval(52,row,question=q,profile='common',mode='U',parser_record=changed,manifest=manifest)
    row['mode']='SEQ'
    with pytest.raises(RuntimeError,match='identity/mode/profile'):
        M.bind_retrieval(52,row,question=q,profile='common',mode='U',parser_record=parser,manifest=manifest)


def test_parallel_checkpoint_requires_same_independent_U(monkeypatch,tmp_path):
    import pytest
    q,parser,manifest,row=setup_contract(monkeypatch,tmp_path)
    row.update(identity='T01:common:PAR:r1',mode='PAR',route=parser['routing'])
    u=dict(evidence=[unit(1)])
    M.bind_retrieval(52,row,question=q,profile='common',mode='PAR',parser_record=parser,manifest=manifest,independent_u=u,new=True)
    with pytest.raises(RuntimeError,match='first-write contract'):
        M.bind_retrieval(52,row,question=q,profile='common',mode='PAR',parser_record=parser,manifest=manifest,independent_u={'evidence':[]})


def test_empty_evidence_and_clarification_never_call_provider(monkeypatch,tmp_path):
    setup_contract(monkeypatch,tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('Provider called for empty evidence')
    monkeypatch.setattr(M.X.Cache,'parse',forbidden)
    for clarify in [False,True]:
        identity='T01:common:U:r1' if not clarify else 'T02:common:U:r1'
        row=M.answer(52,identity,'카드명 확인',dict(cards=[],evidence=[],clarify=clarify),allow_network=False)
        assert row['events']==[] and row['answer']['answer_status']=='insufficient_evidence'


def test_identical_ab_reuse_is_limited_to_same_repeat(monkeypatch,tmp_path):
    import pytest
    setup_contract(monkeypatch,tmp_path)
    result=dict(cards=[],evidence=[])
    a=M.answer(52,'T01:common:A:r1','카드',result,allow_network=False)
    b=M.answer(52,'T01:common:B:r1','카드',result,allow_network=False,reused=a)
    assert b['answer']==a['answer'] and b['reuse_source_sha256']==M.P.sha(a) and not b['events']
    with pytest.raises(RuntimeError,match='same-repeat'):
        M.answer(52,'T01:common:B:r2','카드',result,allow_network=False,reused=a)


def test_notebook_embedding_resume_reuses_bound_raw_vectors_without_api(tmp_path):
    import ast,json,time
    import numpy as np
    import nbformat
    from types import SimpleNamespace
    import pytest
    notebook=nbformat.read(Path(__file__).parents[1]/'52_four_retrieval_modes.ipynb',4)
    tree=ast.parse(notebook.cells[2].source)
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='BudgetRuntime')
    calls=[]
    class Base:
        def embeddings(self,texts,**kwargs):
            calls.append(texts)
            record=tmp_path/'api_cache/embedding_batch_test.json'
            if record.exists():raise AssertionError('Duplicate paid embedding request')
            rows=[]
            for i,t in enumerate(texts):
                vector=np.full(1536,float(i+1),dtype=np.float32)
                key=M.P.sha(dict(text=t,model='text-embedding-3-small',dimension=1536))
                target=tmp_path/'api_cache/embeddings';target.mkdir(parents=True,exist_ok=True)
                np.save(target/(key+'.npy'),vector)
                rows.append(dict(index=i,embedding=vector.tolist()))
            record.write_text(json.dumps(dict(status='ok',request=dict(input=texts,model='text-embedding-3-small',dimensions=1536),raw_response=dict(data=rows))))
            return np.asarray([r['embedding'] for r in rows],dtype=np.float32)
    def freeze(path,value):
        if path.exists():assert json.loads(path.read_text())==value
        else:path.write_text(json.dumps(value))
    original_ledger=lambda:dict(unknown_cost_records=1)
    p=SimpleNamespace(sha=M.P.sha,ledger=original_ledger,F=SimpleNamespace(read_json=lambda p:json.loads(p.read_text())))
    x=SimpleNamespace(Runtime=Base,guard_manifest=lambda m:None,frozen=freeze,budget_upper_ledger=lambda:dict(unknown_cost_records=0))
    namespace=dict(X=x,P=p,time=time)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<notebook-budget-runtime>','exec'),namespace)
    runtime=namespace['BudgetRuntime']();runtime.path=tmp_path;runtime.manifest={};runtime.np=np
    a=runtime.embeddings(['커피','넷플릭스'],allow_network=True)
    b=runtime.embeddings(['커피','넷플릭스'],allow_network=True)
    assert np.array_equal(a,b) and calls==[['커피','넷플릭스']] and p.ledger is original_ledger
    key=M.P.sha(dict(text='커피',model='text-embedding-3-small',dimension=1536))
    np.save(tmp_path/'api_cache/embeddings'/(key+'.npy'),np.zeros(1536,dtype=np.float32))
    with pytest.raises(RuntimeError,match='raw provider proof'):
        runtime.embeddings(['커피','넷플릭스'],allow_network=True)
    assert len(calls)==1
