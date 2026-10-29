"""Predeclared repeated parser probes; metadata separates real API requests.

The main plan is trial zero. Two further requests use identical instructions,
input, schema, model and reasoning. Only provider metadata changes the cache
identity; it does not add a hint to the user question.
"""
from pathlib import Path
import importlib.util
import sys
import time

s=importlib.util.spec_from_file_location('query_routing_repeat',Path(__file__).with_name('33_query_routing.py'))
R=importlib.util.module_from_spec(s);sys.modules[s.name]=R;s.loader.exec_module(R);E=R.E;C=E.C
QUERY_IDS=['Q12','Q16','Q17','Q20','Q22']

def protocol():
    record={'version':'parser-repeat-v1','query_ids':QUERY_IDS,'trials_per_question':3,
      'repeats_after_primary_plan':2,'model':C.MODEL,'reasoning':C.REASONING,
      'selection_reason':'Monthly cap, 50/50.1 percent boundaries, OR, broad travel recommendation; chosen before primary LLM results.',
      'differences':'Only request metadata trial ID changes; instructions/question/schema remain identical.',
      'interpretation':'Three trials are exploratory stability diagnostics, not population error estimates.'}
    record['dataset_sha256']=E.freeze_dataset()['sha256']
    record['instructions_sha256']=C.digest(R.INSTRUCTIONS)
    record['schema_sha256']=C.digest(E.QueryPlan.model_json_schema())
    p=C.STAGES[33]/'repeat_protocol.json'
    if p.exists() and C.read_json(p)!=record:raise RuntimeError('Repeat protocol changed')
    C.write_json(p,record);return record

def run():
    contract=protocol();freeze=E.freeze_dataset();base=C.read_json(C.STAGES[33]/'parser_results.json')
    by_id={r['query_id']:r for r in base['records']};api=C.CachedAPI();outputs=[]
    instructions=R.INSTRUCTIONS+'\n카드 catalog:\n'+C.canonical(freeze['catalog'])
    for case in freeze['queries']:
        if case['query_id'] not in QUERY_IDS:continue
        base_record=by_id[case['query_id']]
        outputs.append({'query_id':case['query_id'],'trial':0,'status':base_record['status'],
                        'plan':base_record.get('plan'),'origin':'primary parser'})
        for trial in [1,2]:
            p=C.STAGES[33]/'repeats'/f"{case['query_id']}_{trial}.json";t=time.perf_counter()
            binding=C.digest({'protocol':contract,'question':case['question'],'trial':trial})
            if p.exists():
                record=C.read_json(p)
                if record.get('input_fingerprint')!=binding:raise RuntimeError('Stale repeat cache')
                outputs.append(record);continue
            try:
                response=api.parse(model=C.MODEL,instructions=instructions,
                    input=[{'role':'user','content':case['question']}],text_format=E.QueryPlan,
                    tools=[],store=False,max_output_tokens=3000,
                    metadata={'experiment':'33-parser-repeat-v1','trial':str(trial)})
                plan=response.output_parsed.model_dump(mode='json')
                if not set(plan['card_keys'])<=set(freeze['catalog']):raise ValueError('Unknown card scope')
                record={'query_id':case['query_id'],'trial':trial,'status':'ok','plan':plan,
                        'wall_seconds':time.perf_counter()-t,'usage':C.usage_dict(response)}
            except Exception as exc:
                record={'query_id':case['query_id'],'trial':trial,'status':'failed',
                        'error':f'{type(exc).__name__}: {exc}','wall_seconds':time.perf_counter()-t}
            record['input_fingerprint']=binding
            C.write_json(p,record);outputs.append(record);print('repeat',case['query_id'],trial,record['status'],flush=True)
    summary=[]
    for q in QUERY_IDS:
        rows=[r for r in outputs if r['query_id']==q]
        plans=[E.normalized_plan(r['plan']) for r in rows if r['status']=='ok']
        summary.append({'query_id':q,'attempts':len(rows),'ok':len(plans),
          'unique_conditions':len({C.canonical(p) for p in plans}),
          'unique_routes':len({r['plan']['route'] for r in rows if r['status']=='ok'})})
    result={'protocol':protocol(),'records':outputs,'summary':summary}
    C.write_json(C.STAGES[33]/'repeat_results.json',result);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--freeze-only',action='store_true');a=p.parse_args()
    protocol() if a.freeze_only else run()
