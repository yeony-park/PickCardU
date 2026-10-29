"""Frozen six-condition experiment using the unmodified operational pipeline."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import sys
import re
import time
import json
from collections import defaultdict
from functools import lru_cache
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

spec=importlib.util.spec_from_file_location('structured_common',Path(__file__).with_name('30_structured_search_common.py'))
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)
DB=C.load_module('structured_db',C.ROOT/'notebooks/scripts/30_build_structured_db.py')
PROFILES=['card_page_section_benefit','parent_child_bundle']
MODES=['U','S_THEN_U','S_PLUS_U']

class Requirement(BaseModel):
    model_config=ConfigDict(extra='forbid')
    targets:list[str]=Field(description='OR targets within ONE benefit; use explicit merchants/categories, empty means any')
    action:Literal['discount','earn','any']
    min_percent:float|None=Field(description='Minimum benefit percent, e.g. 10 means 10%; null if not explicitly requested')
    max_spend_krw:int|None=Field(description='Maximum required previous-month spending threshold in KRW; personal budget is not automatically a threshold')
    min_cap_krw:int|None=Field(description='Minimum stated monetary benefit cap in KRW, null if absent; unresolved period must be recorded')
    cap_period:Literal['month','day','any']=Field(description='Period of requested cap: month for monthly, day for daily, any when no period is requested')
    exclude_targets:list[str]

class QueryPlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    route:Literal['unstructured','structured_then_unstructured']
    groups:list[list[Requirement]]=Field(description='OR between groups; AND requirements within each group. target/action/rate within a requirement belong to one benefit.')
    card_keys:list[str]=Field(description='Only exact keys from the supplied catalog; empty for global search')
    unresolved:list[str]=Field(description='Unsupported/ambiguous intent; do not invent numeric conditions')

def req(targets,action='any',rate=None,spend=None,cap=None,exclude=None,cap_period=None):
    return dict(targets=targets,action=action,min_percent=rate,max_spend_krw=spend,min_cap_krw=cap,
                cap_period=cap_period or ('month' if cap is not None else 'any'),exclude_targets=exclude or [])

def normalized_plan(plan):
    groups=[]
    for group in plan['groups']:
        normalized=[]
        for requirement in group:
            row=dict(requirement)
            row['min_percent']=float(row['min_percent']) if row['min_percent'] is not None else None
            for key in ['max_spend_krw','min_cap_krw']:
                row[key]=int(row[key]) if row[key] is not None else None
            for key in ['targets','exclude_targets']:row[key]=DB._expanded_targets(row[key])
            normalized.append(row)
        groups.append(sorted(normalized,key=C.canonical))
    return {'groups':sorted(groups,key=C.canonical),'card_keys':sorted(plan['card_keys'])}

def citation_ownership(answer,evidence):
    owners={e['chunk_id']:e['card_key'] for e in evidence}
    return all(isinstance(c,str) and owners.get(c)==row['card_key']
               for row in answer['claims']+answer['recommendations'] for c in row['citations'])

def chroma_config_identity(config):
    config=dict(config);function=config.pop('embedding_function',None)
    config['embedding_function']={'class':type(function).__module__+'.'+type(function).__qualname__,
                                  'used_for_queries':False}
    return config

def fact_witnessed(fact,requirement,texts):
    import unicodedata
    norm=lambda s:''.join(unicodedata.normalize('NFKC',str(s)).casefold().split())
    fields=fact['fields'];required=['target','action']
    if requirement.get('min_percent') is not None:required+=['value','unit']
    if requirement.get('max_spend_krw') is not None:required+=['condition']
    if requirement.get('min_cap_krw') is not None:required+=['cap']
    # Strict same-source-fact components, in one retrieved same-card evidence row.
    # This diagnostic can undercount paraphrases and split table cells.
    period=requirement.get('cap_period','any') if requirement.get('min_cap_krw') is not None else 'any'
    return any(all(fields.get(field) and norm(fields[field]) in norm(text) for field in required)
               and (period=='any' or explicit_cap_period(text)==period) for text in texts)

def explicit_cap_period(text):
    """Independent lexical gold/witness period predicate; mixed or absent is unknown."""
    compact=''.join(str(text).split())
    month=bool(re.search(r'매월|월간|월별|당월|해당월',compact) or
               re.search(r'(?<![가-힣])월\s*(?:(?:할인|적립|통합)\s*)?(?:한도|최대|\d)',str(text)))
    day=bool(re.search(r'하루|매일|일일',compact) or
             re.search(r'(?:1\s*일\s*(?:최대|한도|1회)|(?<![가-힣])일\s*(?:최대|한도|\d))',str(text)))
    return 'month' if month and not day else 'day' if day and not month else None

def dataset_inputs():
    return {name:C.digest((C.STAGES[30]/name).read_bytes()) for name in ['facts.jsonl','documents.jsonl']}

def run_fingerprint(source,experiment):
    return C.digest({'dataset':C.read_json(C.STAGES[32]/'dataset_freeze.json')['sha256'],
       'structured_db_sha256':experiment.structured_db_sha256,
       'chunks':{p:C.digest((C.STAGES[31]/f'{p}_chunks.jsonl').read_bytes()) for p in PROFILES},
       'code':{p:C.digest((C.ROOT/'notebooks/scripts'/p).read_bytes()) for p in
               ['30_build_structured_db.py','30_structured_search_common.py','31_operational_chunking.py','32_search_experiment.py','33_query_routing.py']},
       'operational_code':{str(p.relative_to(C.ROOT)):C.digest(p.read_bytes()) for p in
          sorted((C.ROOT/'packages/rag-core/src/pickcardu_rag').rglob('*.py'))+[C.ROOT/'services/rag-api/src/pickcardu_rag_api/index.py']},
       'indexes':experiment.index_identity,'reranker':experiment.scorer.contract,'embedding_model':C.EMBEDDING_MODEL,
       'plans':C.digest((C.STAGES[33]/'parser_results.json').read_bytes()) if source=='llm' else 'oracle',
       'model':C.MODEL,'reasoning':C.REASONING})

def raw_matches(f,requirement):
    raw=f['fields'];norm=lambda s:''.join(s.casefold().split())
    aliases={'여행':['여행','항공','호텔','숙박','공항','라운지','면세점'],
             '커피':['커피','카페','스타벅스','투썸','이디야','폴바셋','커피빈']}
    wanted=[a for t in requirement['targets'] for a in aliases.get(t,[t])]
    if wanted and not any(norm(t) in norm(raw['target']) for t in wanted):return False
    action=requirement['action'];action_text=raw['action']+' '+raw['benefit_type']
    if action=='discount' and '할인' not in action_text:return False
    if action=='earn' and not any(t in action_text for t in ['적립','캐시백']):return False
    if any(t in raw['action'] for t in ['제외','불가','되지 않','않습니다']):return False
    if requirement['min_percent'] is not None:
        nums=re.findall(r'(\d+(?:\.\d+)?)\s*%',raw['value']+' '+raw['unit'])
        expression=raw['value']+' '+raw['unit']
        if len(nums)!=1 or re.search(r'최대|최소|최고|까지|이하|이상|초과|미만|부터|\d\s*(?:~|∼|～|–|—|-)\s*\d',expression):return False
        if re.search(r'\d[\d,]*\s*(?:만|천)?\s*원',expression):return False
        if float(nums[0])<requirement['min_percent']:return False
    if requirement['max_spend_krw'] is not None:
        m=re.search(r'전월\s*(?:이용\s*)?(?:실적|이용금액)\s*(\d+)\s*(만|천)?\s*원\s*이상',raw['condition'])
        if not m:return False
        amount=int(m[1])*{'만':10000,'천':1000,None:1}[m[2]]
        if amount>requirement['max_spend_krw']:return False
    if requirement['min_cap_krw'] is not None:
        if re.search(r'이용금액|이용액|결제금액|결제액|승인금액|매출금액|매출액|매출건당',''.join(raw['cap'].split())):return False
        amounts=re.findall(r'(\d[\d,]*(?:\.\d+)?)\s*(만|천)?\s*원',raw['cap'])
        if len(amounts)!=1:return False
        amount=float(amounts[0][0].replace(',',''))*{'만':10000,'천':1000,'':1}[amounts[0][1]]
        if amount<requirement['min_cap_krw']:return False
        period=requirement.get('cap_period','any')
        if period!='any' and explicit_cap_period(raw['cap']+' '+raw.get('period',''))!=period:return False
    return True

def freeze_dataset():
    path=C.STAGES[32]/'dataset_freeze.json'
    if path.exists():
        frozen=C.read_json(path)
        if frozen.get('source_hashes')!=dataset_inputs():raise RuntimeError('Dataset source changed; explicit new freeze required')
        return frozen
    facts=DB.load_facts(C.STAGES[30]);docs=C.read_jsonl(C.STAGES[30]/'documents.jsonl')
    catalog={d['document_id']:d['identity']['card_name'] for d in docs}
    cases=[
      ('스타벅스 할인율이 10% 이상인 카드를 알려줘',[req(['스타벅스'],'discount',10)],'numeric'),
      ('편의점에서 5% 이상 할인 혜택이 있는 카드는?',[req(['편의점'],'discount',5)],'numeric'),
      ('배달의민족에서 7% 이상 할인되는 카드를 찾아줘',[req(['배달의민족','배달의 민족'],'discount',7)],'numeric'),
      ('올리브영에서 10% 이상 할인되는 카드가 있어?',[req(['올리브영'],'discount',10)],'numeric'),
      ('통신요금 5% 이상 적립 혜택을 찾아줘',[req(['통신'],'earn',5)],'numeric'),
      ('스타벅스와 배달의민족에서 각각 10% 이상 할인되는 카드',[req(['스타벅스'],'discount',10),req(['배달의민족','배달의 민족'],'discount',10)],'and'),
      ('커피 적립과 배달 적립을 모두 제공하는 카드',[req(['커피'],'earn'),req(['배달'],'earn')],'and'),
      ('편의점 할인과 영화 할인이 모두 있는 카드',[req(['편의점'],'discount'),req(['영화','CGV','롯데시네마'],'discount')],'and'),
      ('주유 할인과 대중교통 할인을 함께 제공하는 카드',[req(['주유'],'discount'),req(['대중교통','버스','지하철'],'discount')],'and'),
      ('전월 실적 기준이 30만 원 이하인 스타벅스 할인 카드',[req(['스타벅스'],'discount',spend=300000)],'spend'),
      ('전월 실적 기준 20만 원 이하로 통신 적립이 가능한 카드',[req(['통신'],'earn',spend=200000)],'spend'),
      ('월 할인한도가 5천 원 이상인 올리브영 할인 카드',[req(['올리브영'],'discount',cap=5000)],'cap'),
      ('여행 혜택이 있는 카드를 추천해줘',[req(['여행'])],'category'),
      ('공항 라운지를 이용할 수 있는 카드를 찾아줘',[req(['라운지'])],'category'),
      ('호텔 할인 혜택이 있는 카드는?',[req(['호텔'],'discount')],'category'),
      ('스타벅스에서 50% 이상 할인되는 카드를 알려줘',[req(['스타벅스'],'discount',50)],'boundary'),
      ('스타벅스에서 50.1% 이상 할인되는 카드를 알려줘',[req(['스타벅스'],'discount',50.1)],'boundary'),
      ('편의점 1000% 할인 카드가 있어?',[req(['편의점'],'discount',1000)],'negative'),
      ('존재하지않는실험가맹점XYZ 할인 카드',[req(['존재하지않는실험가맹점XYZ'],'discount')],'negative'),
      ('배달 또는 편의점에서 10% 이상 할인되는 카드',None,'or'),
      ('카페를 자주 가는데 어떤 카드 혜택을 살펴보면 좋을까?',[],'semantic'),
      ('여행할 때 유용한 카드를 추천해줘',[req(['여행'])],'category'),
      ('소비 패턴이 아직 없는데 카드 선택에서 무엇을 비교해야 해?',[],'semantic'),
      ('온라인 쇼핑을 많이 할 때 유용한 카드를 추천해줘',[],'semantic'),
      ('영화 할인과 커피 할인 혜택이 함께 있는 카드',[req(['영화','CGV','롯데시네마'],'discount'),req(['커피'],'discount')],'and'),
      ('전월 실적 기준 15만 원 이하의 배달 할인 카드',[req(['배달'],'discount',spend=150000)],'spend'),
      ('올리브영에서 7% 이상 적립되는 카드',[req(['올리브영'],'earn',7)],'numeric'),
      ('통신요금에서 10% 이상 할인되는 카드',[req(['통신'],'discount',10)],'numeric'),
      ('국내 호텔 예약 할인 혜택을 알려줘',[req(['호텔'],'discount')],'category'),
      ('편의점과 통신요금에서 모두 적립되는 카드',[req(['편의점'],'earn'),req(['통신'],'earn')],'and'),
    ]
    # Independent source-relative annotations: do NOT call select_cards to build gold.
    # Explicit raw field predicates are applied to the frozen OCR facts, not to derived DB fields.
    dataset=[]
    for i,(question,requirements,subgroup) in enumerate(cases):
        groups=([ [req(['배달'],'discount',10)], [req(['편의점'],'discount',10)] ] if requirements is None else [requirements] if requirements else [])
        plan={'route':'structured_then_unstructured' if groups else 'unstructured','groups':groups,'card_keys':[],'unresolved':[]}
        gold={};witness={}
        if groups:
            for card in catalog:
                card_facts=[f for f in facts if f['document_id']==card]
                for group in groups:
                    matches=[[f['fact_id'] for f in card_facts if raw_matches(f,r)] for r in group]
                    if matches and all(matches):
                        gold[card]=True;witness[card]=matches;break
        split='confirmatory' if i%3==2 else 'development'
        dataset.append({'query_id':f'Q{i+1:02}','question':question,'subgroup':subgroup,'split':split,
                        'plan':plan,'gold_cards':sorted(gold),'witness_groups':witness,
                        'gold_type':'source_relative_raw_field_annotation' if groups else 'open_semantic_no_exhaustive_gold',
                        'negative_control':subgroup=='negative'})
    # Proper-name questions have independently explicit card ownership labels.
    for key in ['NH/NH_Olbareun_FLEX','BC/BC_Baro_Clear_Plus','BC/BC_Green_v2']:
        if key not in catalog:continue
        dataset.append({'query_id':f'Q{len(dataset)+1:02}','question':catalog[key]+'의 주요 혜택과 조건을 알려줘',
                        'subgroup':'proper_noun','split':'confirmatory','plan':{'route':'unstructured','groups':[], 'card_keys':[], 'unresolved':[]},
                        'gold_cards':[key],'witness_groups':{},'gold_type':'explicit_named_card','negative_control':False})
        dataset[-1]['plan']['card_keys']=[key]
    freeze={'schema':'structured_search_dataset_v4','created_before_retrieval':False,
            'questions_and_splits_frozen_before_provisional_runs':True,'created_before_answer_results':True,
            'revision_note':'Independent review corrected range/cap semantics, comma and trailing-period gold parser. Provisional v2/v3 results archived; no answer results seen. Same 33 questions and splits; primary rerun is source-relative development/confirmatory diagnostics, not independent holdout.', 'queries':dataset,
            'catalog':catalog,'config':{'model':C.MODEL,'reasoning':C.REASONING,'component_depth':50,'candidate_depth':20,'top_k':3,
              'vector_weight':0.4,'fallback':'if structured set empty or intent unresolved, full U; retain strict prefilter diagnostic',
              'fusion':'equal-weight card RRF k60 of operational U and structured-scoped hydrated result; union evidence by original ownership, max5/card,64KB',
              'scope':'all106 copied Upstage documents; no historical sealed holdout used'},
            'gold_limit':'Source-relative raw OCR fact predicates, not independent PDF validation; positive labels conservative and not guaranteed exhaustive. Generic questions have no retrieval gold. Confirmatory cohort tests a frozen configuration but labels share source extraction.'}
    freeze['source_hashes']=dataset_inputs()
    freeze['sha256']=C.digest(freeze);C.write_json(path,freeze);print('frozen queries',len(dataset));return freeze

class CachedReranker:
    def __init__(self):
        from pickcardu_rag.retrieval import LocalReranker
        import torch
        torch.set_num_threads(4)
        self.delegate=LocalReranker(str(C.ROOT/'.cache/reranker/bge-reranker-v2-m3'))
        self.cache=C.STAGES[32]/'bge_cache';self.cache.mkdir(exist_ok=True);self.calls=[]
        self.contract=self.delegate.artifact_contract('bge')
    def score(self,mode,query,documents,**kwargs):
        scores=[];miss=[];paths=[];t=time.perf_counter()
        for i,doc in enumerate(documents):
            path=self.cache/(C.digest({'mode':mode,'q':query,'doc':doc,'kwargs':kwargs,'contract':self.contract})+'.json')
            paths.append(path)
            if path.exists():scores.append(C.read_json(path)['score'])
            else:scores.append(None);miss.append(i)
        trace={'cache_hits':len(documents)-len(miss),'new_pairs':len(miss),'device':'cpu','dtype':'float32','contract':self.contract}
        if miss:
            new,details=self.delegate.score(mode,query,[documents[i] for i in miss],**kwargs)
            trace['measured_scorer']=details
            for i,score in zip(miss,new,strict=True):
                scores[i]=score;C.write_json(paths[i],{'score':score,'query_sha256':C.digest(query),'document_sha256':C.digest(documents[i])})
        trace['wall_seconds']=time.perf_counter()-t;self.calls.append(trace);return scores,trace

class ScopedSearcher:
    def __init__(self,delegate,keys):self.delegate=delegate;self.keys=keys
    def search(self,value,*,limit,card_keys=None):
        keys=self.keys if card_keys is None else tuple(k for k in card_keys if k in self.keys)
        return self.delegate.search(value,limit=limit,card_keys=keys)

class Experiment:
    def __init__(self):
        import numpy as np,chromadb
        from chromadb.config import Settings
        from pickcardu_rag.retrieval import Chunk,RagPipeline,CHUNKING_PROFILES
        from pickcardu_rag_api.index import SQLiteFTSSearcher,ChromaVectorSearcher
        self.structured_db_sha256=C.digest((C.STAGES[30]/'structured.sqlite').read_bytes())
        if self.structured_db_sha256!=C.read_json(C.STAGES[30]/'db_build_summary.json')['artifact_sha256']['structured.sqlite']:
            raise RuntimeError('Structured database differs from its verified build')
        self.api=C.CachedAPI();self.scorer=CachedReranker();self.facts=DB.load_facts(C.STAGES[30]);self.data={};self.index_identity={}
        if self.facts!=C.read_jsonl(C.STAGES[30]/'facts.jsonl'):raise RuntimeError('SQLite facts differ from frozen audit export')
        for profile in PROFILES:
            path=C.STAGES[31]/profile;records=C.read_jsonl(C.STAGES[31]/f'{profile}_chunks.jsonl')
            docs={r['document_id']:r['metadata'] for r in records}
            chunks=[]
            for r in records:
                m=r['metadata'];chunks.append(Chunk(r['chunk_id'],r['text'],r['document_id'],m['card_name'],m['issuer_name'],r['level'],
                   min(m['source_pages']),m.get('section'),m['reranker_text'],m.get('parent_id'),tuple(m.get('child_ids',[])),
                   m.get('node_id'),tuple(m.get('heading_path',[])),m.get('part_index',1),tuple(m.get('related_chunk_ids',[])),m.get('optional_parent_heading')))
            client=chromadb.PersistentClient(path=str(path/'chroma'),settings=Settings(anonymized_telemetry=False))
            collection=client.get_collection(profile)
            lexical=SQLiteFTSSearcher(path/'corpus.sqlite')
            matrix=np.load(path/'embeddings.npy',allow_pickle=False)
            ids=[c.chunk_id for c in chunks]
            stored=collection.get(ids=ids,include=['embeddings','metadatas'])
            by_id={key:(vec,meta) for key,vec,meta in zip(stored['ids'],stored['embeddings'],stored['metadatas'],strict=True)}
            if set(by_id)!=set(ids) or collection.count()!=len(ids):raise RuntimeError('Chroma IDs mismatch')
            if any(not np.array_equal(matrix[i],np.asarray(by_id[key][0],dtype=np.float32)) or by_id[key][1]['document_id']!=chunks[i].card_key for i,key in enumerate(ids)):
                raise RuntimeError('Chroma vectors/card owners mismatch')
            self.index_identity[profile]={'fts_sha256':C.digest((path/'corpus.sqlite').read_bytes()),
                 'embeddings_sha256':C.digest((path/'embeddings.npy').read_bytes()),'chroma_ids_sha256':C.digest(ids),
                 'chroma_content_sha256':C.digest({k:{'embedding':np.asarray(v[0],dtype=np.float32).tolist(),'metadata':v[1]} for k,v in by_id.items()}),
                 'chroma_metadata':collection.metadata,'chroma_configuration':chroma_config_identity(collection.configuration)}
            vector=ChromaVectorSearcher(collection,C.EMBEDDING_MODEL,chunks=chunks,embeddings=matrix)
            self.data[profile]=(chunks,lexical,vector,client)
    def search(self,question,embedding,profile,keys=None):
        from pickcardu_rag.retrieval import RagPipeline,SearchConfig,CHUNKING_PROFILES
        chunks,lexical,vector,_=self.data[profile]
        if keys is not None:lexical=ScopedSearcher(lexical,tuple(keys));vector=ScopedSearcher(vector,tuple(keys))
        pipeline=RagPipeline(chunks,lexical,vector,self.scorer,profile=CHUNKING_PROFILES[profile])
        return pipeline.search(question,embedding,SearchConfig(profile=profile,vector_weight=.4,component_depth=50,candidate_depth=20,
                    top_k=3,reranker='bge',reranker_route='all' if profile=='parent_child_bundle' else 'selective',answer_payload_bytes=64000))

def merge_results(u,s,question):
    from pickcardu_rag.answering import measure_answer_payload
    scores=defaultdict(float);cards={}
    for result in [u,s]:
        for rank,card in enumerate(result['cards'],1):
            key=card['card_key'];scores[key]+=.5/(60+rank);cards[key]=card
    ranked=sorted(scores,key=lambda k:(-scores[k],k))[:3];evidence=[]
    for key in ranked:
        rows={}
        for result in [s,u]:
            for row in result['evidence']:
                if row['card_key']==key:rows.setdefault(row['chunk_id'],row)
        for row in list(rows.values())[:5]:
            if measure_answer_payload(question,evidence+[row])[0]>64000:break
            evidence.append(row)
    return {'query_type':u['query_type'],'cards':[{**cards[k],'score':scores[k],'rank':i,'evidence_count':sum(e['card_key']==k for e in evidence)} for i,k in enumerate(ranked,1)],
            'evidence':evidence,'trace':{'fusion':'equal-weight card RRF, hydrated S and U','rrf':u.get('trace',{}).get('rrf',[])}}

@lru_cache(maxsize=1)
def metric_facts(facts_digest):
    return {f['fact_id']:f for f in DB.load_facts(C.STAGES[30])}

def evaluate_retrieval(case,result,selection):
    returned=[c['card_key'] for c in result.get('cards',[])];gold=set(case['gold_cards']);n=len(gold)
    first=next((i for i,k in enumerate(returned,1) if k in gold),None)
    # Raw-text witness evaluation is independent of select_cards; explicit ownership required.
    facts=metric_facts(C.digest((C.STAGES[30]/'structured.sqlite').read_bytes()))
    supported=0
    for card,groups in case.get('witness_groups',{}).items():
        if card not in returned:continue
        texts=[e['text'] for e in result.get('evidence',[]) if e['card_key']==card]
        # The gold annotation records the first satisfied OR group.
        requirement_group=next((g for g in case['plan']['groups'] if len(g)==len(groups)),[])
        if requirement_group and all(any(fact_witnessed(facts[fid],requirement_group[i],texts) for fid in choices)
                                     for i,choices in enumerate(groups)):supported+=1
    available=n>0
    return {'anchor_hit_at_3':int(first is not None) if available else None,'anchor_mrr':1/first if first else 0 if available else None,
            'anchor_recall_at_3':len(gold&set(returned))/n if available else None,
            'witness_recall_at_3':supported/n if available and case.get('witness_groups') else None,
            'strict_prefilter_anchor_retention':len(gold&set(selection['eligible_cards']))/n if available and case['plan']['groups'] else None,
            'returned_cards':len(returned),'gold_anchors':n,'negative_control':case['negative_control']}

def run_retrieval(source='oracle'):
    freeze=freeze_dataset();cases=freeze['queries'];experiment=Experiment();fingerprint=run_fingerprint(source,experiment)
    plans={r['query_id']:r['plan'] for r in cases}
    if source=='llm':plans={r['query_id']:r['plan'] for r in C.read_json(C.STAGES[33]/'parser_results.json')['records'] if r['status']=='ok'}
    embeddings=experiment.api.embeddings([r['question'] for r in cases]);outputs=[]
    directory=C.STAGES[32] if source=='oracle' else C.STAGES[33]
    for case,embedding in zip(cases,embeddings,strict=True):
        plan=plans.get(case['query_id'],{'route':'unstructured','groups':[],'card_keys':[],'unresolved':['parser failure']})
        selection=DB.select_cards(experiment.facts,plan)
        scope=selection['eligible_cards'];fallback=not scope or bool(plan.get('unresolved')) or plan['route']=='unstructured'
        C.write_json(directory/'selections'/f"{case['query_id']}.json",{'plan':plan,'selection':selection,'fallback':fallback})
        for profile in PROFILES:
            cache={}
            for mode in MODES:
                key=f"{case['query_id']}_{profile}_{mode}";path=directory/'retrieval'/f'{key}.json'
                if path.exists():
                    record=C.read_json(path)
                    if record.get('input_fingerprint')!=fingerprint:raise RuntimeError('Stale retrieval cache: '+key)
                    outputs.append(record);cache[mode]=record.get('result');continue
                t=time.perf_counter()
                try:
                    if mode=='U':result=experiment.search(case['question'],embedding,profile,plan.get('card_keys') or None)
                    elif mode=='S_THEN_U':result=cache['U'] if fallback else experiment.search(case['question'],embedding,profile,scope)
                    else:result=cache['U'] if fallback else merge_results(cache['U'],cache['S_THEN_U'],case['question'])
                    if result is None:raise RuntimeError('Required prior component failed')
                    cache[mode]=result
                    record={'query_id':case['query_id'],'profile':profile,'mode':mode,'source':source,'status':'ok','result':result,
                            'fallback':fallback if mode!='U' else False,'structured_card_count':len(scope),'unknown_card_count':len(selection['unknown_cards']),
                            'metrics':evaluate_retrieval(case,result,selection),'wall_seconds':time.perf_counter()-t}
                except Exception as exc:
                    cache[mode]=None;record={'query_id':case['query_id'],'profile':profile,'mode':mode,'source':source,'status':'failed',
                                             'error':f'{type(exc).__name__}: {exc}','wall_seconds':time.perf_counter()-t}
                record['input_fingerprint']=fingerprint;C.write_json(path,record);outputs.append(record)
                print(source,key,record['status'],round(record['wall_seconds'],2),flush=True)
        C.write_json(directory/'retrieval_progress.json',{'completed':len(outputs),'expected':len(cases)*6,'last_query':case['query_id']})
    C.write_json(directory/'retrieval_results.json',{'records':outputs,'scorer_calls':experiment.scorer.calls,'dataset_sha256':freeze['sha256']})
    return outputs

def run_answers(source='oracle'):
    from pickcardu_rag.answering import OpenAIService
    directory=C.STAGES[32] if source=='oracle' else C.STAGES[33]
    records=C.read_json(directory/'retrieval_results.json')['records'];cases={q['query_id']:q for q in freeze_dataset()['queries']}
    api=C.CachedAPI();provider=OpenAIService(api_key=None,client=api,llm_model=C.MODEL,answer_payload_bytes=64000)
    outputs=[]
    for row in records:
        key=f"{row['query_id']}_{row['profile']}_{row['mode']}";path=directory/'answers'/f'{key}.json'
        binding=C.digest({'retrieval':row,'model':C.MODEL,'reasoning':C.REASONING,
                          'answer_source':C.digest((C.ROOT/'packages/rag-core/src/pickcardu_rag/answering.py').read_bytes())})
        if path.exists():
            cached=C.read_json(path)
            if cached.get('input_fingerprint')!=binding:raise RuntimeError('Stale answer cache: '+key)
            outputs.append(cached);continue
        t=time.perf_counter();case=cases[row['query_id']]
        try:
            if row['status']!='ok':raise RuntimeError('Retrieval failed')
            result=row['result']
            if not result['evidence']:
                answer={'answer_status':'insufficient_evidence','answer_text':'검색된 근거가 없습니다.','recommendations':[],'claims':[]};usage={'provider_called':False}
            else:
                parsed,usage=provider.answer(case['question'],result['evidence']);answer=parsed.model_dump(mode='json')
            ownership=citation_ownership(answer,result['evidence'])
            recs={r['card_key'] for r in answer['recommendations']};gold=set(case['gold_cards'])
            metrics={'citation_ownership':int(ownership),'answer_anchor_hit':int(bool(recs&gold)) if gold else None,
                     'negative_abstention':int(answer['answer_status']=='insufficient_evidence') if case['negative_control'] else None,
                     'insufficient':int(answer['answer_status']=='insufficient_evidence')}
            out={'query_id':row['query_id'],'profile':row['profile'],'mode':row['mode'],'source':source,'status':'ok','answer':answer,
                 'metrics':metrics,'usage':usage,'wall_seconds':time.perf_counter()-t}
        except Exception as exc:
            out={'query_id':row['query_id'],'profile':row['profile'],'mode':row['mode'],'source':source,'status':'failed',
                 'error':f'{type(exc).__name__}: {exc}','wall_seconds':time.perf_counter()-t}
        out['input_fingerprint']=binding;C.write_json(path,out);outputs.append(out);print('answer',source,key,out['status'],flush=True)
        C.write_json(directory/'answer_progress.json',{'completed':len(outputs),'expected':len(records)})
    C.write_json(directory/'answer_results.json',{'records':outputs,'dataset_sha256':freeze_dataset()['sha256'],'model':C.MODEL,'reasoning':C.REASONING})
    return outputs

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['freeze','retrieval','answers']);p.add_argument('--source',default='oracle',choices=['oracle','llm']);args=p.parse_args()
    {'freeze':freeze_dataset,'retrieval':lambda:run_retrieval(args.source),'answers':lambda:run_answers(args.source)}[args.stage]()
