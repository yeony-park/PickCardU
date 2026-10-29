"""Focused checks for conditions, operational adapters and cache invariants."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

def load():
    p=Path(__file__).with_name('32_search_experiment.py');s=importlib.util.spec_from_file_location('experiment_test',p)
    m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);return m
E=load()

def test_schema_preserves_boolean_benefit_groups():
    plan=E.QueryPlan(route='structured_then_unstructured',groups=[[E.Requirement(**E.req(['카페'],'discount',10)),
       E.Requirement(**E.req(['편의점'],'earn',5))]],card_keys=[],unresolved=[])
    assert len(plan.groups[0])==2 and plan.groups[0][1].action=='earn'

def test_query_plan_rejects_free_sql():
    with pytest.raises(Exception):E.QueryPlan(route='unstructured',groups=[],card_keys=[],unresolved=[],sql='DELETE FROM facts')

def test_scoped_adapter_passes_more_than_five_cards_without_changing_search_config():
    class Searcher:
        def search(self,v,*,limit,card_keys):return card_keys
    keys=tuple(str(i) for i in range(12))
    assert E.ScopedSearcher(Searcher(),keys).search('q',limit=50)==keys

def test_merge_keeps_card_citation_ownership_and_top3():
    def result(keys):
        return {'query_type':'semantic','cards':[{'card_key':k,'rank':i+1,'score':1,'card_name':k,'issuer':'issuer'} for i,k in enumerate(keys)],
                'evidence':[{'card_key':k,'chunk_id':k+':1','text':'근거','rank':i+1,'score':1,'card_name':k,'issuer':'issuer','page_num':1,'section':None,'level':'benefit'} for i,k in enumerate(keys)],'trace':{}}
    merged=E.merge_results(result(['a','b','c']),result(['d','b','e']),'q')
    assert len(merged['cards'])==3 and merged['cards'][0]['card_key']=='b'
    assert all(e['card_key']==e['chunk_id'].split(':')[0] for e in merged['evidence'])

def test_frozen_dataset_does_not_use_selector_as_gold():
    source=Path(E.__file__).read_text();block=source.split('def freeze_dataset():')[1].split('class CachedReranker')[0]
    assert 'DB.select_cards(' not in block
    dataset=E.freeze_dataset()
    assert len(dataset['queries'])==33 and {'development','confirmatory'}=={q['split'] for q in dataset['queries']}

def test_api_proxy_enforces_requested_model_and_medium_without_network(tmp_path,monkeypatch):
    class Response:
        def parse(self,**kwargs):
            assert kwargs['model']=='gpt-6-luna' and kwargs['reasoning']=={'effort':'medium'}
            raise ValueError('test stops before network')
    monkeypatch.setitem(E.C.STAGES,30,tmp_path)
    api=E.C.CachedAPI();api.client=SimpleNamespace(responses=Response())
    api.cache=E.C.STAGES[30]/'unit_test_api';api.cache.mkdir(exist_ok=True)
    assert api.cache.is_relative_to(tmp_path)
    # Unique local fake-provider payload; zero external calls.
    with pytest.raises(ValueError):api.parse(model='wrong',reasoning={'effort':'high'},text_format=E.QueryPlan,
         input='adapter-unit-test-unique-v1',instructions='test')

def test_string_citations_validate_claim_and_recommendation_ownership():
    evidence=[{'chunk_id':'c1','card_key':'a'},{'chunk_id':'c2','card_key':'b'}]
    answer={'claims':[{'card_key':'a','citations':['c1']}],
            'recommendations':[{'card_key':'a','citations':['c1']}]}
    assert E.citation_ownership(answer,evidence)
    answer['recommendations'][0]['citations']=['c2']
    assert not E.citation_ownership(answer,evidence)

def test_witness_requires_spend_and_cap_when_requested():
    fact={'fields':{'target':'편의점','action':'할인','value':'10','unit':'%',
                    'condition':'전월 실적 30만원 이상','cap':'월 5000원 한도'}}
    requirement=E.req(['편의점'],'discount',10,300000,5000)
    assert not E.fact_witnessed(fact,requirement,['편의점 10% 할인'])
    assert E.fact_witnessed(fact,requirement,['편의점 10% 할인 전월 실적 30만원 이상 월 5000원 한도'])

def test_plan_normalization_treats_coffee_aliases_equally():
    left={'groups':[[E.req(['커피'],'discount',10)]],'card_keys':[]}
    right={'groups':[[E.req(['카페'],'discount',10)]],'card_keys':[]}
    assert E.normalized_plan(left)==E.normalized_plan(right)

def test_plan_numeric_normalization_accepts_int_and_float_percent():
    left={'groups':[[E.req(['커피'],'discount',10)]],'card_keys':[]}
    right={'groups':[[E.req(['커피'],'discount',10.0)]],'card_keys':[]}
    assert E.C.canonical(E.normalized_plan(left))==E.C.canonical(E.normalized_plan(right))

def test_month_and_day_cap_are_explicit_in_schema():
    assert E.Requirement(**E.req(['올리브영'],cap=5000)).cap_period=='month'
    assert E.Requirement(**E.req(['올리브영'],cap=5000,cap_period='day')).cap_period=='day'

def test_chroma_fingerprint_keeps_hnsw_and_serializes_unused_embedding_function():
    class DefaultEmbeddingFunction:pass
    config={'hnsw':{'space':'l2','ef_search':100},'spann':None,'embedding_function':DefaultEmbeddingFunction()}
    safe=E.chroma_config_identity(config)
    assert safe['hnsw']==config['hnsw'] and safe['embedding_function']['used_for_queries'] is False
    assert E.C.digest(safe)==E.C.digest(E.chroma_config_identity(config))

def test_cached_usage_is_read_without_sdk_object():
    assert E.C.usage_dict(SimpleNamespace(usage={'input_tokens':7}))=={'input_tokens':7}

def test_operational_answer_provider_accepts_injected_client_without_loading_key():
    from pickcardu_rag.answering import OpenAIService
    client=E.C.CachedAPI()
    provider=OpenAIService(api_key=None,client=client,llm_model=E.C.MODEL,answer_payload_bytes=64000)
    assert provider._get_client() is client and client.client is None

def test_metric_fact_cache_reloads_on_changed_input_digest(monkeypatch):
    loads=[]
    def fake_load(path):loads.append(path);return [{'fact_id':'f'}]
    monkeypatch.setattr(E.DB,'load_facts',fake_load);E.metric_facts.cache_clear()
    assert E.metric_facts('old')==E.metric_facts('old')=={'f':{'fact_id':'f'}}
    assert len(loads)==1
    E.metric_facts('new');assert len(loads)==2
    E.metric_facts.cache_clear()

def test_backend_probe_rejects_stale_or_duplicate_primary_rows():
    probe=E.C.load_module('backend_probe_test',Path(__file__).with_name('32_backend_scope_probe.py'))
    freeze={'queries':[{'query_id':'Q01'}],'sha256':'data'}
    rows=[{'query_id':'Q01','profile':p,'mode':m,'input_fingerprint':'current'} for p in E.PROFILES for m in E.MODES]
    primary={'dataset_sha256':'data','records':rows}
    probe.validate_primary(primary,freeze,'current')
    rows[0]['input_fingerprint']='stale'
    with pytest.raises(RuntimeError):probe.validate_primary(primary,freeze,'current')
    rows[0]['input_fingerprint']='current';rows[-1]=dict(rows[0])
    with pytest.raises(RuntimeError):probe.validate_primary(primary,freeze,'current')

def test_answer_constraint_rejects_foreign_or_absent_citation_and_unsupported_money():
    scorer=E.C.load_module('answer_constraint_tests',Path(__file__).with_name('32_score_answers.py'))
    requirement=E.req(['편의점'],'discount',10,300000,5000)
    claim={'card_key':'a','text':'편의점 10% 할인 전월 실적 30만원 이상 월 한도 5000원',
           'value':'10','unit':'%','conditions':[],'citations':['e']}
    evidence={'e':{'card_key':'b','text':claim['text']}}
    assert scorer.component_support(claim,requirement,evidence)==0
    evidence['e']={'card_key':'a','text':'편의점 10% 할인 전월 실적 50만원 이상 월 한도 3000원'}
    assert scorer.component_support(claim,requirement,evidence)<1
    evidence['e']['text']=claim['text']
    assert scorer.component_support(claim,requirement,evidence)==1
    claim['text']=claim['text'].replace('월 한도','일 한도')
    assert scorer.component_support(claim,requirement,evidence)<1

def test_raw_gold_cap_accepts_comma_and_amount_before_month():
    def fact(cap):return {'fields':{'target':'올리브영','action':'할인','benefit_type':'할인',
                    'value':'10','unit':'%','condition':'','cap':cap,'period':''}}
    r=E.req(['올리브영'],'discount',cap=5000)
    assert E.raw_matches(fact('통합 월 할인한도는 5,000원입니다.'),r)
    assert E.raw_matches(fact('5천원 매월별(1일~말일) 이용순서 기준'),r)
    assert not E.raw_matches(fact('일 한도 5000원'),r)
    assert not E.raw_matches(fact('월100만원 이용금액까지'),r)
    assert E.raw_matches(fact('일 한도 5000원'),E.req(['올리브영'],'discount',cap=5000,cap_period='day'))

def test_witness_needs_month_period_and_raw_percent_rejects_unit_bounds():
    fact={'fields':{'target':'올리브영','action':'할인','benefit_type':'할인','value':'10',
                   'unit':'%','condition':'','cap':'5천원','period':'매월'}}
    r=E.req(['올리브영'],'discount',cap=5000)
    assert not E.fact_witnessed(fact,r,['올리브영 할인 5천원'])
    assert E.fact_witnessed(fact,r,['올리브영 할인 매월 5천원'])
    fact['fields']['unit']='% 미만'
    assert not E.raw_matches(fact,E.req(['올리브영'],'discount',10))

def test_daily_cap_does_not_match_installment_and_complete_uses_same_gold_card():
    scorer=E.C.load_module('answer_cross_card_test',Path(__file__).with_name('32_score_answers.py'))
    claim={'card_key':'b','text':'올리브영 할인 일시불 5,000원','conditions':[],'citations':['e'],'value':None,'unit':None}
    evidence={'e':{'card_key':'b','text':claim['text']}}
    assert scorer.component_support(claim,E.req(['올리브영'],'discount',cap=5000,cap_period='day'),evidence)<1
    claim['text']='올리브영 할인 매월 100만원 이용금액까지';evidence['e']['text']=claim['text']
    assert scorer.component_support(claim,E.req(['올리브영'],'discount',cap=5000),evidence)<1
    r=E.req(['올리브영'],'discount',10)
    claim['text']='올리브영 10% 할인';evidence['e']['text']=claim['text']
    case={'plan':{'groups':[[r]]},'gold_cards':['a']}
    row={'status':'ok','metrics':{'citation_ownership':1},'answer':{'claims':[claim],
         'recommendations':[{'card_key':'a'},{'card_key':'b'}]}}
    retrieval={'result':{'evidence':[{'chunk_id':'e',**evidence['e']}]}}
    assert scorer.score_row(case,row,retrieval)['constraint_coverage']==1
    assert scorer.score_row(case,row,retrieval)['known_positive_complete']==0
