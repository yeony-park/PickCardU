"""Behavioral regressions for source-bound mapping and fixed-card packaging."""
import importlib.util
from pathlib import Path
import pytest
import subprocess
import sys

spec = importlib.util.spec_from_file_location('evidence45', Path(__file__).with_name('45_evidence_supplement.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def fixture():
    lines = ['커피 10% 할인', '전월 실적 30만원 이상']
    refs = {'target': [{'line_id': 'P0001-L0001', 'char_start': 0, 'char_end': 2, 'fragment': '커피'}],
            'value': [{'line_id': 'P0001-L0001', 'char_start': 3, 'char_end': 5, 'fragment': '10'}],
            'action': [{'line_id': 'P0001-L0001', 'char_start': 7, 'char_end': 9, 'fragment': '할인'}],
            'condition': [{'line_id': 'P0001-L0002', 'char_start': 0, 'char_end': len(lines[1]), 'fragment': lines[1]}]}
    fact = {'fact_id':'f1','document_id':'A','ordinal':0,'field_evidence':refs,
            'relation_scope':{'line_ids':['P0001-L0001','P0001-L0002'],'scope_type':'bounded_block'}}
    pages = [{'page':1,'text':'\n'.join(lines)}]
    chunks = [{'chunk_id':'a','document_id':'A','text':lines[0],'metadata':{'source_pages':[1]}},
              {'chunk_id':'b','document_id':'A','text':lines[1],'metadata':{'source_pages':[1]}}]
    return fact,pages,chunks


def test_split_same_fact_keeps_both_source_lines():
    f,p,c=fixture(); r=M.map_fact(f,p,c)
    assert r['status']=='verified' and set(r['chunk_ids'])=={'a','b'}


@pytest.mark.parametrize('mutation',['owner','page','amount','coordinate','scope','empty'])
def test_mapping_rejects_wrong_owner_page_value_or_unbound_source(mutation):
    f,p,c=fixture()
    if mutation=='owner':c[0]['document_id']='B'
    if mutation=='page':c[0]['metadata']['source_pages']=[2]
    if mutation=='amount':c[0]['text']='커피 20% 할인'
    if mutation=='coordinate':f['field_evidence']['value'][0]['char_start']=4
    if mutation=='scope':f['relation_scope']['line_ids']=['P0001-L0002']
    if mutation=='empty':f['field_evidence']={}
    assert M.map_fact(f,p,c)['status']!='verified'


def evidence(key,card='A',text=None):
    return {'chunk_id':key,'card_key':card,'rank':1,'text':text or key,
            'card_name':card,'issuer':'issuer','page_num':1,'section':'section','level':'benefit','score':0.1}


def test_supplement_keeps_cards_rank_and_first_evidence_without_duplicates():
    a=[evidence('a'),evidence('old'),evidence('b','B')]
    cards=[{'card_key':'A','rank':1},{'card_key':'B','rank':2}]
    r=M.pack('질문',cards,a,[[evidence('new'),evidence('a')],[evidence('new2')]],lambda e:len(str(e)))
    assert r['cards']==cards and [x['chunk_id'] for x in r['evidence']]==['a','b','new','new2','old']
    assert a[0]['rank']==1


def test_pack_enforces_per_card_limit_and_condition_round_robin():
    a=[evidence('a')]; groups=[[evidence('x'+str(i)) for i in range(8)],[evidence('second')]]
    r=M.pack('q',[{'card_key':'A','rank':1}],a,groups,lambda e:1)
    ids=[x['chunk_id'] for x in r['evidence']]
    assert len(ids)==5 and 'second' in ids and ids[0]=='a'


def test_pack_skips_whole_oversized_chunk_and_never_adds_other_card():
    a=[evidence('a')]
    r=M.pack('q',[{'card_key':'A','rank':1}],a,[[evidence('big',text='x'*100),evidence('foreign','B'),evidence('small')]],
             lambda e:sum(len(x['text']) for x in e),limit=10)
    assert [x['chunk_id'] for x in r['evidence']]==['a','small']
    assert {'big','foreign'}<=set(x['chunk_id'] for x in r['excluded'])


def test_no_supplement_returns_exact_original_order_and_serialization():
    a=[evidence('a'),evidence('b','B'),evidence('other')]
    assert M.pack('q',[{'card_key':'A','rank':1},{'card_key':'B','rank':2}],a,[],lambda e:1)['evidence']==a


def test_evidence_counts_follow_changed_package_without_changing_card_rank():
    cards=[{'card_key':'A','rank':1,'evidence_count':1}]
    r=M.pack('q',cards,[evidence('a')],[[evidence('new')]],lambda e:1)
    assert r['cards']==[{'card_key':'A','rank':1,'evidence_count':2}]
    assert cards[0]['evidence_count']==1


def test_optimized_interpreter_cannot_bypass_experiment_invariants():
    p=subprocess.run([sys.executable,'-O','-B',str(Path(__file__).with_name('45_experiment_io.py'))],capture_output=True,text=True)
    assert p.returncode!=0 and 'Optimized Python' in p.stderr


@pytest.mark.parametrize('mutation',['rank','foreign','first','payload','text','card_name','issuer'])
def test_paid_boundary_rejects_mutated_packages(mutation):
    import json
    from copy import deepcopy
    spec=importlib.util.spec_from_file_location('prepare_test45',Path(__file__).with_name('45_prepare_packages.py'))
    P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
    data=Path(__file__).parents[1]/'data';prepared=json.loads((data/'45_evidence_supplement_comparison/prepared_packages.json').read_text())
    row=deepcopy(next(x for x in prepared['records'] if x['treatment_status']=='changed'))
    original=json.loads((data/'42_expanded_query_evaluation/combined_results.json').read_text())
    a=next(x for x in original['retrievals'] if x['query_id']==row['query_id'] and x['profile']==row['profile'] and x['mode']=='S_THEN_U')
    answer=next(x for x in original['answers'] if x['query_id']==row['query_id'] and x['profile']==row['profile'] and x['mode']=='S_THEN_U')
    chunks={r['chunk_id']:r for r in map(json.loads,(data/'31_operational_chunking_comparison'/f"{row['profile']}_chunks.jsonl").read_text().splitlines())}
    P.validate_package(deepcopy(row),a,answer,chunks)
    if mutation=='rank':row['B']['cards'][0]['rank']=99
    if mutation=='foreign':row['B']['evidence'][0]['card_key']='unknown card'
    if mutation=='first':row['B']['evidence']=row['B']['evidence'][1:]
    if mutation=='payload':row['B']['payload_sha256']='wrong'
    if mutation=='text':row['B']['evidence'][0]['text']='원문에 없는 99% 할인'
    if mutation in ['card_name','issuer']:
        old_ids={e['chunk_id'] for e in row['A']['evidence']}
        next(e for e in row['B']['evidence'] if e['chunk_id'] not in old_ids)[mutation]='다른 카드'
        row['B']['payload_sha256']=P.I.digest(P.payload(row['question'],row['B']['evidence']).encode())
    with pytest.raises(RuntimeError):P.validate_package(row,a,answer,chunks)


def test_effect_audit_rejects_hash_valid_but_wrong_treatment_classification():
    import json
    from copy import deepcopy
    spec=importlib.util.spec_from_file_location('audit_test45',Path(__file__).with_name('45_prepare_packages.py'))
    P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
    data=Path(__file__).parents[1]/'data/45_evidence_supplement_comparison'
    prepared=json.loads((data/'prepared_packages.json').read_text())
    audit=json.loads((data/'package_change_audit.json').read_text())
    P.validate_change_audit(prepared,audit)
    broken=deepcopy(audit)
    row=next(r for r in broken['records'] if r['effect_type']=='replacement');row['effect_type']='append_only'
    broken['sha256']=P.I.digest({k:v for k,v in broken.items() if k!='sha256'})
    with pytest.raises(RuntimeError):P.validate_change_audit(prepared,broken)


def test_paid_boundary_recomputes_source_mapping_instead_of_trusting_verified_flag():
    import json
    spec=importlib.util.spec_from_file_location('mapping_test45',Path(__file__).with_name('45_prepare_packages.py'))
    P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
    prepared=json.loads((Path(__file__).parents[1]/'data/45_evidence_supplement_comparison/prepared_packages.json').read_text())
    link=next(m for m in prepared['mapping'].values() if m['status']=='verified')
    link['anchors'][0]['char_start']+=1
    with pytest.raises(RuntimeError):P.validate_prepared(prepared)
