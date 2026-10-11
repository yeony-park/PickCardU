"""Single-run new-question comparison; faithful operational retrieval and answers."""
if not __debug__:
    raise RuntimeError('Optimized Python is forbidden: preflight assertions must run')
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace

s=importlib.util.spec_from_file_location('new_query_common',Path(__file__).with_name('37_followup_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)
A=C.load('new_answer_cache','35_answer_experiment.py');A.F.STAGES.update(C.STAGES)
PRICE=C.load('new_query_prices','36_cost_accounting.py')
M=C.load('new_query_rubric','39_answer_rubric.py')

class BudgetLimit(BaseException):
    """Fatal control-flow stop; operational Exception handlers must not swallow it."""


def cost():
    records=[('40',str(p.relative_to(C.ROOT)),C.read(p)) for p in sorted((C.STAGES[40]/'api_cache').glob('*.json'))]
    rows=PRICE.price_records(records);return {'records':rows,'summary':PRICE.aggregate(rows),'rates':PRICE.RATES}

def check_budget(request,output=2400):
    ledger=cost();record_count=ledger['summary']['record_count']
    if record_count>=235:raise BudgetLimit('Registered provider request cap reached')
    # Conservative pre-call reservation: serialized UTF8 byte length plus schema/control overhead;
    # this is a budget estimate only; final costs always use provider usage.
    estimated=(Decimal(len(C.F.canonical(request).encode())+4096)*Decimal('0.125')+Decimal(output)*Decimal('0.50'))/1000000
    if Decimal(ledger['summary']['known_cost_usd'])+estimated>1:raise BudgetLimit('Registered USD1 known-cost budget would be exceeded')

class RunCache(A.ResponseCache):
    def __init__(self,kind='answer'):
        super().__init__(cache=C.STAGES[40]/'api_cache',old_cache=C.STAGES[40]/'api_cache',policy='2400',allow_network=True,revalidate_failed=False)
        self.kind=kind
    def normalized(self,kwargs):
        value=super().normalized(kwargs)
        if self.kind=='parser':value['max_output_tokens']=3000
        return value
    def parse(self,**kwargs):
        # Stop before ResponseCache can create a started request record.
        if not (self.cache/(self.key(kwargs)+'.json')).exists():
            request={k:v for k,v in self.normalized(kwargs).items() if k not in ['text_format','timeout']}
            request['schema']=kwargs['text_format'].model_json_schema();check_budget(request,request['max_output_tokens'])
        return super().parse(**kwargs)

class Embeddings:
    def embeddings(self,texts):
        import numpy as np
        directory=C.STAGES[40]/'api_cache/embeddings';directory.mkdir(parents=True,exist_ok=True)
        keys=[C.digest({'text':t,'model':'text-embedding-3-small','dimension':1536}) for t in texts]
        missing=[i for i,k in enumerate(keys) if not (directory/(k+'.npy')).exists()]
        if missing:
            request={'model':'text-embedding-3-small','input':[texts[i] for i in missing],'dimensions':1536}
            path=directory.parent/('embedding_batch_'+C.digest(request)+'.json')
            if path.exists():raise RuntimeError('Interrupted or failed embedding request needs explicit recovery')
            check_budget(request,0);C.write(path,{'status':'started','request':request,'started_at':time.time()})
            try:
                response=A.F.load_module('new_query_client_common',C.ROOT/'notebooks/30_structured_search_common.py').api_client().embeddings.create(**request)
                C.initialize(40)
                raw=response.model_dump(mode='json');data=sorted(response.data,key=lambda r:r.index)
                if len(data)!=len(missing):raise ValueError('Embedding count mismatch')
                for i,r in zip(missing,data,strict=True):
                    vector=np.asarray(r.embedding,dtype=np.float32)
                    if vector.shape!=(1536,) or not np.isfinite(vector).all():raise ValueError('Invalid query embedding')
                    np.save(directory/(keys[i]+'.npy'),vector)
                C.write(path,{'status':'ok','request':request,'usage':raw['usage'],'raw_response':raw,
                    'raw_response_sha256':C.digest(raw),'finished_at':time.time()})
            except Exception as e:
                C.write(path,{'status':'failed','request':request,'error':f'{type(e).__name__}: {e}'});raise
        return np.asarray([np.load(directory/(k+'.npy'),allow_pickle=False) for k in keys])

def validate_freeze():
    dataset=C.read(C.STAGES[39]/'dataset.json');contract=C.read(C.STAGES[39]/'scorer_contract.json')
    for value in [dataset,contract]:assert C.digest({k:v for k,v in value.items() if k!='sha256'})==value['sha256']
    assert contract['scorer_sha256']==C.digest((C.ROOT/'notebooks/39_answer_rubric.py').read_bytes())
    assert contract['source_builder_sha256']==C.digest((C.ROOT/'notebooks/39_freeze_new_questions.py').read_bytes())
    assert dataset['scorer_contract_sha256']==contract['sha256']
    for kind,name in [('facts','facts.jsonl'),('documents','documents.jsonl')]:assert dataset['source_sha256'][kind]==C.digest((C.F.OLD[30]/name).read_bytes())
    return dataset

def retrieval_metrics(case,result,E,facts):
    cards=[r['card_key'] for r in result['cards']];gold=case['gold_cards']
    rank=next((i for i,k in enumerate(cards,1) if k in gold),None)
    witnessed=0;count=0
    for card,groups in case['witness_groups'].items():
        count+=1
        if card not in cards:continue
        texts=[e['text'] for e in result['evidence'] if e['card_key']==card]
        # Preserve original single-span diagnostic, not an unregistered semantic judge.
        if any(all(any(all(M.norm(facts[fid]['fields'][field]) in M.norm(text) for field in ['target','action','value'] if facts[fid]['fields'][field])
                       for text in texts for fid in ids) for ids in group) for group in groups):witnessed+=1
    return {'anchor_hit_at_3':int(rank is not None) if gold else None,'anchor_mrr':1/rank if rank else 0 if gold else None,
            'witness_recall_at_3':witnessed/count if count else None,'gold_cards':len(gold),'returned_cards':len(cards)}

def run():
    C.initialize(40)
    C.verify()
    assert C.read(C.STAGES[39]/'notebook_execution.json')['all_code_cells_executed']
    dataset=validate_freeze();E=C.load('new_query_prior_schema','32_search_experiment.py')
    P=C.load('new_query_prior_prompt','33_query_routing.py');C.initialize(40)
    instructions=P.INSTRUCTIONS+'\n카드 catalog:\n'+E.C.canonical(dataset['catalog'])
    contract=C.freeze(C.STAGES[40]/'protocol.json',{'dataset_sha256':dataset['sha256'],'scorer_contract_sha256':dataset['scorer_contract_sha256'],
        'parser_instructions':instructions,'parser_schema':E.QueryPlan.model_json_schema(),'model':'gpt-6-luna','reasoning':'medium',
        'parser_output_tokens':3000,'answer_output_tokens':2400,'bundle_policy':'budget5','profiles':E.PROFILES,'modes':E.MODES,
        'component_depth':50,'candidate_depth':20,'top_k':3,'vector_weight':0.4,'cost_cap_usd':'1','physical_request_cap':235,
        'repeated_runs':0,'original_answer_retry_max':2,'baseline_scope':'U shares inferred explicit card keys; not parser-independent global baseline',
        'source_code':{str(p.relative_to(C.ROOT)):C.digest(p.read_bytes()) for p in [C.ROOT/'notebooks/39_answer_rubric.py',C.ROOT/'notebooks/40_new_query_comparison.py',C.ROOT/'packages/rag-core/src/pickcardu_rag/answering.py',C.ROOT/'packages/rag-core/src/pickcardu_rag/retrieval.py']}})
    started=time.perf_counter();plans={}
    for q in dataset['queries']:
        path=C.STAGES[40]/'plans'/f"{q['query_id']}.json"
        if path.exists():r=C.read(path)
        else:
            api=RunCache('parser')
            try:
                response=api.parse(model='gpt-6-luna',instructions=instructions,input=[{'role':'user','content':q['question']}],
                                   text_format=E.QueryPlan,tools=[],store=False,max_output_tokens=3000)
                plan=response.output_parsed.model_dump(mode='json')
                if not set(plan['card_keys'])<=set(dataset['catalog']):raise ValueError('Unknown parser card key')
                r={'status':'ok','plan':plan,'events':api.events}
            except Exception as e:r={'status':'failed','error':f'{type(e).__name__}: {e}','events':api.events,
                'plan':{'route':'unstructured','groups':[],'card_keys':[],'unresolved':['parser failure']}}
            r.update(query_id=q['query_id'],protocol_sha256=contract['sha256']);C.write(path,r)
        assert r['protocol_sha256']==contract['sha256'];plans[q['query_id']]=r
        print('parser',q['query_id'],r['status'],flush=True)
    C.write(C.STAGES[40]/'parser_results.json',{'records':list(plans.values())})
    embeddings=Embeddings().embeddings([q['question'] for q in dataset['queries']]);B,x=C.experiment(40)
    facts={f['fact_id']:f for f in x.facts};retrievals=[];answers=[]
    from pickcardu_rag.answering import OpenAIService
    for q,embedding in zip(dataset['queries'],embeddings,strict=True):
        plan=plans[q['query_id']]['plan'];selection=E.DB.select_cards(x.facts,plan)
        fallback=not selection['eligible_cards'] or bool(plan['unresolved']) or plan['route']=='unstructured'
        for profile in E.PROFILES:
            cache={}
            for mode in E.MODES:
                filename=f"{q['query_id']}_{profile}_{mode}.json";path=C.STAGES[40]/'retrieval'/filename
                if path.exists():r=C.read(path)
                else:
                    tick=time.perf_counter()
                    try:
                        if mode=='S_PLUS_U':result=cache['U'] if fallback else E.merge_results(cache['U'],cache['S_THEN_U'],q['question'])
                        elif mode=='S_THEN_U' and fallback:result=cache['U']
                        else:
                            keys=(plan['card_keys'] or None) if mode=='U' else selection['eligible_cards']
                            C.component(B,x,40,q['question'],embedding,profile,keys)
                            result=x.search(q['question'],embedding,profile,keys,'budget5')
                        if result is None:raise RuntimeError('Required component failed')
                        r={'status':'ok','result':result,'metrics':retrieval_metrics(q,result,E,facts)}
                    except Exception as e:r={'status':'failed','error':f'{type(e).__name__}: {e}'}
                    r.update(query_id=q['query_id'],profile=profile,mode=mode,protocol_sha256=contract['sha256'],fallback=fallback if mode!='U' else False,
                        selection=selection,wall_seconds=time.perf_counter()-tick,subgroup=q['subgroup']);C.write(path,r)
                assert r['protocol_sha256']==contract['sha256'];cache[mode]=r.get('result');retrievals.append(r)
                path=C.STAGES[40]/'answers'/filename
                if path.exists():a=C.read(path)
                else:
                    tick=time.perf_counter();api=RunCache()
                    try:
                        if r['status']!='ok':raise RuntimeError('Retrieval failed')
                        evidence=r['result']['evidence']
                        if evidence:
                            answer,usage=OpenAIService(api_key=None,client=api,llm_model='gpt-6-luna',answer_payload_bytes=64000).answer(q['question'],evidence)
                            answer=answer.model_dump(mode='json')
                        else:answer={'answer_status':'insufficient_evidence','answer_text':'검색 근거가 없습니다.','claims':[],'recommendations':[]};usage={}
                        a={'status':'ok','answer':answer,'usage':usage,'scores':M.score(q,answer,evidence)}
                    except Exception as e:a={'status':'failed','error':f'{type(e).__name__}: {e}',
                        'scores':{'known_positive_complete':0 if q['gold_cards'] else None,'citation_ownership':0,'explicit_scope_preserved':0}}
                    a.update(query_id=q['query_id'],profile=profile,mode=mode,protocol_sha256=contract['sha256'],retrieval_sha256=C.digest(r),
                        events=api.events,subgroup=q['subgroup'],wall_seconds=time.perf_counter()-tick);C.write(path,a)
                assert a['protocol_sha256']==contract['sha256'] and a['retrieval_sha256']==C.digest(r)
                answers.append(a);C.write(C.STAGES[40]/'progress.json',{'retrieval_rows':len(retrievals),'answer_rows':len(answers),'expected':108})
                C.write(C.STAGES[40]/'cost_accounting.json',cost());print('condition',filename,r['status'],a['status'],flush=True)
    C.write(C.STAGES[40]/'retrieval_results.json',{'records':retrievals});C.write(C.STAGES[40]/'answer_results.json',{'records':answers})
    result={'records':answers,'retrieval_records':len(retrievals),'expected':108,'protocol_sha256':contract['sha256'],
       'retrieval_success_rows':sum(r['status']=='ok' for r in retrievals),'answer_success_rows':sum(r['status']=='ok' for r in answers),
       'execution_complete_not_quality_pass':True,
       'new_provider_cost':cost()['summary'],'new_bge_pairs':sum(r['new_pairs'] for r in x.scorer.calls),
       'runner_wall_seconds':time.perf_counter()-started,'repeated_runs':0,'profile_index_identity':x.base.index_identity}
    C.write(C.STAGES[40]/'run_summary.json',result);C.verify();return result
if __name__=='__main__':
    try:run()
    except BudgetLimit as e:
        C.write(C.STAGES[40]/'interrupted.json',{'status':'budget_stopped','reason':str(e),'completed_summary_written':False,'cost':cost()['summary']})
        C.verify()
        raise
