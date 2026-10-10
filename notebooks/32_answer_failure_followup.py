"""Post-hoc answer failures: cache-only retry replay, then output budget probe.

Primary answers remain intact. Cached EOF retains the operational validation
exception; no repeated paid request is allowed in the offline replay.
The budget probe changes only max_output_tokens, leaving operational code intact.
"""
from pathlib import Path
import importlib.util
import sys
import time
from pydantic import ValidationError

s=importlib.util.spec_from_file_location('answer_failure_search',Path(__file__).with_name('32_search_experiment.py'))
E=importlib.util.module_from_spec(s);sys.modules[s.name]=E;s.loader.exec_module(E);C=E.C

def request_key(kwargs):
    request={k:v for k,v in kwargs.items() if k not in {'text_format','timeout'}}
    request.update(model=C.MODEL,reasoning={'effort':C.REASONING},schema=kwargs['text_format'].model_json_schema())
    return C.digest(request)

class OfflineRetryAPI(C.CachedAPI):
    def _client(self):raise RuntimeError('Offline retry forbids provider calls')
    def parse(self,**kwargs):
        path=self.cache/f'{request_key(kwargs)}.json'
        if not path.exists():raise RuntimeError('Offline retry requires an existing exact request cache')
        stored=C.read_json(path)
        if stored['status']=='failed' and stored.get('error','').startswith('ValidationError:') and 'EOF while parsing' in stored['error']:
            raise ValidationError.from_exception_data('CachedJSONFailure',[
                {'type':'json_invalid','loc':(),'input':None,'ctx':{'error':'EOF while parsing a value: preserved cached provider validation failure'}}])
        return super().parse(**kwargs)

class LargerBudgetAPI(C.CachedAPI):
    def __init__(self):super().__init__();self.provider_attempts=0
    def _client(self):
        self.provider_attempts+=1
        return super()._client()
    def parse(self,**kwargs):
        kwargs['max_output_tokens']=4800
        return super().parse(**kwargs)

class InspectFirstAPI(OfflineRetryAPI):
    def __init__(self):super().__init__();self.first_key=None
    def parse(self,**kwargs):
        self.first_key=request_key(kwargs)
        raise RuntimeError('First request inspection only; no provider/cache writes')

def first_failure(question,evidence):
    from pickcardu_rag.answering import OpenAIService
    api=InspectFirstAPI();provider=OpenAIService(api_key=None,client=api,llm_model=C.MODEL,answer_payload_bytes=64000)
    try:provider.answer(question,evidence)
    except Exception:pass
    if api.first_key is None:return None
    path=api.cache/f'{api.first_key}.json'
    if not path.exists():return None
    stored=C.read_json(path);error=stored.get('error','')
    allowed=(error.startswith('ValidationError:') and 'EOF while parsing' in error) or error=='RuntimeError: Refused/incomplete/empty structured response'
    return {'request_key':api.first_key,'original_error':error} if stored['status']=='failed' and allowed else None

def protocol():
    record={'version':'answer-failure-followup-v3','post_hoc_development_only':True,
      'operational_answering_sha256':C.digest((C.ROOT/'packages/rag-core/src/pickcardu_rag/answering.py').read_bytes()),
      'trigger':'Observed EOF/empty output and cached ValidationError becoming RuntimeError, bypassing the operational second attempt.',
      'offline':'Only failed answer rows whose first exact request cache records EOF ValidationError or empty/incomplete output; restore cached EOF ValidationError so unchanged operational retry can reuse its exact response.0 new API calls.',
      'budget':'Only failures remaining after cache-only retry; original prompt/evidence/schema/model/medium, max_output_tokens2400→4800. Exact new payload caching, no primary overwrite.',
      'model':C.MODEL,'reasoning':C.REASONING,'budget':4800,
      'limit':'Exploratory adapter/budget diagnostics. Failed-response usage was not retained; token-budget cause is a hypothesis, not confirmed from provider status.'}
    path=C.STAGES[32]/'answer_failure_followup_protocol.json'
    if path.exists() and C.read_json(path)!=record:raise RuntimeError('Answer failure protocol changed')
    C.write_json(path,record);return record

def run():
    from pickcardu_rag.answering import OpenAIService
    scoring=C.load_module('followup_scores',Path(__file__).with_name('32_score_answers.py'))
    contract=protocol();freeze=E.freeze_dataset();cases={q['query_id']:q for q in freeze['queries']}
    records=[];excluded=[]
    for stage in [32,33]:
        directory=C.STAGES[stage];primary=C.read_json(directory/'answer_results.json')
        search=C.read_json(directory/'retrieval_results.json');lookup={(r['query_id'],r['profile'],r['mode']):r for r in search['records']}
        for original in primary['records']:
            key=(original['query_id'],original['profile'],original['mode']);retrieval=lookup[key]
            if original['status']=='ok' or retrieval['status']!='ok':continue
            primary_binding=C.digest({'retrieval':retrieval,'model':C.MODEL,'reasoning':C.REASONING,
                                      'answer_source':contract['operational_answering_sha256']})
            if original['input_fingerprint']!=primary_binding:raise RuntimeError('Primary answer uses a different operational prompt/schema source')
            failure=first_failure(cases[key[0]]['question'],retrieval['result']['evidence'])
            if failure is None:
                excluded.append({'stage':stage,'query_id':key[0],'profile':key[1],'mode':key[2],'reason':'No allowed first-request provider failure cache'});continue
            for kind in ['offline_retry','larger_budget']:
                identity=C.digest({'contract':contract,'primary_answer':original,'retrieval':retrieval,'kind':kind,
                                  'helper_sha256':C.digest(Path(__file__).read_bytes())})
                path=directory/'answer_failure_followup'/f"{key[0]}_{key[1]}_{key[2]}_{kind}.json"
                if path.exists():
                    row=C.read_json(path)
                    if row['input_fingerprint']!=identity:raise RuntimeError('Stale failure followup')
                else:
                    api=OfflineRetryAPI() if kind=='offline_retry' else LargerBudgetAPI()
                    provider=OpenAIService(api_key=None,client=api,llm_model=C.MODEL,answer_payload_bytes=64000)
                    started=time.perf_counter();case=cases[key[0]]
                    try:
                        parsed,usage=provider.answer(case['question'],retrieval['result']['evidence']);answer=parsed.model_dump(mode='json')
                        if kind=='larger_budget':usage['max_output_tokens_per_attempt']=4800
                        recommended={r['card_key'] for r in answer['recommendations']};gold=set(case['gold_cards'])
                        metrics={'citation_ownership':int(E.citation_ownership(answer,retrieval['result']['evidence'])),
                          'answer_anchor_hit':int(bool(recommended&gold)) if gold else None,
                          'negative_abstention':int(answer['answer_status']=='insufficient_evidence') if case['negative_control'] else None,
                          'insufficient':int(answer['answer_status']=='insufficient_evidence')}
                        row={'status':'ok','answer':answer,'metrics':metrics,'usage':usage}
                        row['condition_scores']=scoring.score_row(case,row,retrieval)
                    except Exception as exc:row={'status':'failed','error':f'{type(exc).__name__}: {exc}'}
                    row.update(stage=stage,query_id=key[0],profile=key[1],mode=key[2],kind=kind,
                       input_fingerprint=identity,wall_seconds=time.perf_counter()-started,
                       new_successful_api_requests=sum(not e['cache_hit'] for e in api.events if e['kind']=='llm'),
                       new_provider_attempts=getattr(api,'provider_attempts',0),
                       failed_attempt_usage='미측정: failed response usage was not retained',
                       first_request_failure=failure,
                       max_output_tokens=2400 if kind=='offline_retry' else 4800)
                    C.write_json(path,row)
                records.append(row);print('answer followup',stage,*key,kind,row['status'],flush=True)
                if row['status']=='ok':break
    result={'protocol':contract,'records':records,'excluded':excluded,'primary_preserved':True,
            'scope':'Only provider failures with successful primary retrieval. Scores are not substituted into primary tables.'}
    C.write_json(C.STAGES[32]/'answer_failure_followup_results.json',result);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--freeze-only',action='store_true');args=p.parse_args()
    protocol() if args.freeze_only else run()
