"""Compact, commit-ready summaries; raw provider/evidence caches stay local."""
from pathlib import Path
import importlib.util
import sys
from collections import Counter

s=importlib.util.spec_from_file_location('report_summary',Path(__file__).with_name('30_render_experiment_reports.py'))
R=importlib.util.module_from_spec(s);sys.modules[s.name]=R;s.loader.exec_module(R);C=R.C

def compact_followups(stage):
    path=C.STAGES[32]/'answer_failure_followup_results.json'
    if not path.exists():return None
    data=C.read_json(path)
    fields=['stage','query_id','profile','mode','kind','status','error','metrics','condition_scores',
            'max_output_tokens','new_provider_attempts','new_successful_api_requests','input_fingerprint']
    return {'protocol':data['protocol'],'records':[{k:r[k] for k in fields if k in r} for r in data['records'] if r['stage']==stage],
            'excluded':[r for r in data['excluded'] if r['stage']==stage]}

def run(stage):
    d=C.STAGES[stage];freeze=C.read_json(C.STAGES[32]/'dataset_freeze.json')
    retrieval=C.read_json(d/'retrieval_results.json');answers=C.read_json(d/'answer_results.json')
    scores=C.read_json(d/'answer_constraint_scores.json');cases={q['query_id']:q for q in freeze['queries']}
    answer_lookup={(r['query_id'],r['profile'],r['mode']):r for r in answers['records']}
    score_lookup={(r['query_id'],r['profile'],r['mode']):r for r in scores['records']}
    records=[]
    for r in retrieval['records']:
        key=(r['query_id'],r['profile'],r['mode']);a=answer_lookup[key]
        records.append({'query_id':r['query_id'],'profile':r['profile'],'mode':r['mode'],
          'retrieval_status':r['status'],'retrieval_error':r.get('error'),'metrics':r.get('metrics'),
          'fallback':r.get('fallback'),'structured_card_count':r.get('structured_card_count'),
          'unknown_card_count':r.get('unknown_card_count'),
          'card_keys':[c['card_key'] for c in r.get('result',{}).get('cards',[])],
          'evidence_ids':[e['chunk_id'] for e in r.get('result',{}).get('evidence',[])],
          'answer_status':a['status'],'answer_error':a.get('error'),'answer_metrics':a.get('metrics'),
          'answer_condition_diagnostics':score_lookup[key],
          'recommended_card_keys':[c['card_key'] for c in a.get('answer',{}).get('recommendations',[])],
          'retrieval_input_fingerprint':r['input_fingerprint'],'answer_input_fingerprint':a['input_fingerprint']})
    result={'schema':'structured-search-summary-v1','stage':stage,'source':'oracle' if stage==32 else 'llm',
      'summarizer_sha256':C.digest(Path(__file__).read_bytes()),
      'dataset_sha256':freeze['sha256'],'input_sha256':{'retrieval':C.digest(retrieval),'answers':C.digest(answers),'answer_scores':C.digest(scores)},
      'model':C.MODEL,'reasoning':C.REASONING,'single_run':True,'production_adopted':False,
      'retrieval_aggregates':R.aggregate(retrieval['records'],cases),
      'status_counts':{'retrieval':dict(Counter(r['status'] for r in retrieval['records'])),
                       'answers':dict(Counter(r['status'] for r in answers['records']))},
      'limit':freeze['gold_limit'],'records':records}
    result['selections']=[{'query_id':q['query_id'],**C.read_json(d/'selections'/f"{q['query_id']}.json")} for q in freeze['queries']]
    diagnostic=d/'failure_diagnostics.json'
    if diagnostic.exists():result['failure_diagnostics']=C.read_json(diagnostic)
    result['answer_failure_followup']=compact_followups(stage)
    if stage==33:
        parser=C.read_json(d/'parser_results.json')['records']
        result['parser_records']=parser
        result['parser_aggregates']={name:{'denominator':len(parser),'matched':sum(r.get('metrics',{}).get(name,0) for r in parser)}
             for name in ['route_match','condition_exact_match','card_scope_match','candidate_set_match','boolean_group_match']}
    C.write_json(d/'evaluation_summary.json',result);print(stage,'summary',len(records));return result

if __name__=='__main__':
    for stage in [32,33]:run(stage)
