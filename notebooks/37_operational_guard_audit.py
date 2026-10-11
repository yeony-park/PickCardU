"""Measure actual operational guards using deliberately fixed provider outputs."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

s=importlib.util.spec_from_file_location('guard_common',Path(__file__).with_name('37_followup_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)

EVIDENCE=[{'card_key':'audit/card-a','card_name':'진단 카드 A','issuer':'진단','chunk_id':'a1',
           'text':'스타벅스 10% 할인. 전월 실적 30만원 이상. 월 할인한도 5천원.'},
          {'card_key':'audit/card-b','card_name':'진단 카드 B','issuer':'진단','chunk_id':'b1','text':'편의점 5% 적립.'}]
BASE={'answer_status':'answered','answer_text':'스타벅스 10% 할인을 제공합니다.',
      'recommendations':[{'card_key':'audit/card-a','reason':'스타벅스 할인','citations':['a1']}],
      'claims':[{'card_key':'audit/card-a','text':'스타벅스 10% 할인','value':'10','unit':'%',
                 'conditions':['전월 실적 30만원 이상','월 할인한도 5천원'],'citations':['a1']}]}

class FixedResponses:
    def __init__(self,outputs):self.outputs=iter(outputs);self.calls=[];self.responses=self
    def parse(self,**kwargs):
        self.calls.append({'schema':kwargs['text_format'].model_json_schema(),'instructions':kwargs['instructions']})
        output=next(self.outputs)
        if isinstance(output,Exception):raise output
        return SimpleNamespace(output_parsed=output,usage={})

def run():
    C.initialize(37);C.protection()
    from pickcardu_rag.answering import AnswerOutput,validate_grounding,OpenAIService
    from pickcardu_rag_api.main import create_app
    from pickcardu_rag_api.config import Settings
    from pickcardu_rag_api.models import QueryRequest
    rows=[]
    changes=[('정상 출력',True,lambda a:None),
      ('혜택률10→99',False,lambda a:a['claims'][0].update(text='스타벅스 99% 할인',value='99')),
      ('할인→적립',False,lambda a:a['claims'][0].update(text='스타벅스 10% 적립')),
      ('월→일 한도',False,lambda a:a['claims'][0].update(conditions=['전월 실적 30만원 이상','일 할인한도 5천원'])),
      ('실적30→10만원',False,lambda a:a['claims'][0].update(conditions=['전월 실적 10만원 이상','월 할인한도 5천원'])),
      ('다른 카드 인용',False,lambda a:a['claims'][0].update(citations=['b1'])),
      ('없는 인용',False,lambda a:a['claims'][0].update(citations=['missing'])),
      ('없는 카드',False,lambda a:a['claims'][0].update(card_key='audit/unknown')),
      ('빈 주장',False,lambda a:a.update(claims=[])),
      ('인용 없음',False,lambda a:a['claims'][0].update(citations=[])),
      ('답변 본문만 잘못된 수치',False,lambda a:a.update(answer_text='스타벅스 99% 할인을 제공합니다.'))]
    for name,correct,change in changes:
        payload=copy.deepcopy(BASE);change(payload);phase='output_schema'
        try:
            parsed=AnswerOutput.model_validate(payload);phase='citation_ownership';validate_grounding(parsed,EVIDENCE)
            accepted=True;error=None
        except Exception as e:accepted=False;error=f'{type(e).__name__}: {e}'
        rows.append({'case':name,'source_correct':correct,'accepted':accepted,'decision_stage':phase,
                     'false_accept':accepted and not correct,'false_reject':not accepted and correct,'input':payload,'error':error})
    generated={'answer_status':'answered','answer_text':'스타벅스 10% 할인',
      'recommendations':[{'reason':'스타벅스 할인','citations':['e1']}],
      'claims':[{'text':'스타벅스 10% 할인','value':'10','unit':'%','conditions':[],'citations':['e1']}]}
    retries=[]
    for name,first in [('형식 오류 후 정상',{'bad':'output'}),('잘못된 혜택률',None),('혼합 카드 인용',None)]:
        first=copy.deepcopy(generated) if first is None else first
        if name=='잘못된 혜택률':first['claims'][0].update(text='스타벅스 99% 할인',value='99')
        if name=='혼합 카드 인용':first['claims'][0]['citations']=['e1','e2']
        fixture=FixedResponses([first,generated]);service=OpenAIService(api_key=None,client=fixture)
        try:answer,usage=service.answer('스타벅스 할인 안내',EVIDENCE);result=answer.model_dump(mode='json');error=None
        except Exception as e:result=None;error=f'{type(e).__name__}: {e}'
        retries.append({'case':name,'provider_attempts':len(fixture.calls),'answer':result,'error':error})
    class EmptyHandle:
        release_id='audit-empty';chunks=();catalog=()
        manifest={'strategy':'card_page_section_benefit','document_ids':[],'embedding_model':'text-embedding-3-small'}
        def search(self,*args):return {'query_type':'semantic','cards':[],'evidence':[],'trace':{}}
    class Provider:
        embedding_model='text-embedding-3-small';calls=0
        def embed(self,q):return [0.0],{}
        def answer(self,*args,**kwargs):self.calls+=1;raise AssertionError('Empty evidence called answer provider')
    p=Provider();settings=Settings('test',C.STAGES[37],('http://localhost',),None,p.embedding_model,'gpt-6-luna',C.STAGES[37],None)
    app=create_app(settings,provider=p,index_loader=SimpleNamespace(load=lambda:EmptyHandle()),reranker=object())
    endpoint=next(route.endpoint for route in app.routes if getattr(route,'path',None)=='/v1/answer')
    response=endpoint(QueryRequest(query='스타벅스 할인 안내'))
    assert response.answer_status=='insufficient_evidence' and p.calls==0
    result={'records':rows,'retry_records':retries,'pre_generation':{'invocation':'Actual FastAPI route endpoint directly; HTTP transport not exercised',
        'answer_status':response.answer_status,'answer_provider_calls':p.calls},'new_api_requests':0,
        'confusion':{'true_accept':sum(r['accepted'] and r['source_correct'] for r in rows),
          'false_accept':sum(r['false_accept'] for r in rows),'true_reject':sum(not r['accepted'] and not r['source_correct'] for r in rows),
          'false_reject':sum(r['false_reject'] for r in rows)},
        'limits':'Fixed output injection measures guard behavior, not actual LLM error frequency. False acceptance is an observed defense gap, not a broken audit.'}
    C.write(C.STAGES[37]/'guard_results.json',result);print(result['confusion'],retries);return result

if __name__=='__main__':run()
