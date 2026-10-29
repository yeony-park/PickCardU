"""Development ablation: same 106 cards, global ANN versus scoped exact L2.

The operational vector adapter switches backend when card_keys is provided.
Holding all cards in scope separates that switch from structured filtering.
Only the first frozen example of each selected query type is inspected.
"""
from pathlib import Path
import importlib.util
import sys
import time

s=importlib.util.spec_from_file_location('scope_backend_experiment',Path(__file__).with_name('32_search_experiment.py'))
E=importlib.util.module_from_spec(s);sys.modules[s.name]=E;s.loader.exec_module(E);C=E.C
QUERY_IDS=['Q01','Q10','Q13','Q18','Q20','Q23']

def validate_primary(primary,freeze,identity):
    expected={(q['query_id'],p,m) for q in freeze['queries'] for p in E.PROFILES for m in E.MODES}
    rows=primary['records'];keys={(r['query_id'],r['profile'],r['mode']) for r in rows}
    if len(rows)!=len(expected) or keys!=expected or primary['dataset_sha256']!=freeze['sha256']:
        raise RuntimeError('Backend probe primary grid/dataset mismatch')
    if any(r.get('input_fingerprint')!=identity for r in rows):raise RuntimeError('Backend probe requires current validated replay fingerprint')

def protocol():
    contract={'version':'backend-scope-probe-v1','query_ids':QUERY_IDS,'cards':106,
      'post_hoc_development_only':True,'trigger':'Source inspection confirms global ANN and card-scoped exact L2 use different operational paths.',
      'selection':'First frozen numeric, spend, travel/category, negative, OR, and general-advice representatives; not selected by score.',
      'fixed':'Corpus, chunks, query vector, component50/D20/Top3, weights and profile reranking unchanged.',
      'changed':'Scope explicitly contains every card instead of None; includes normal operational lexical WHERE path.',
      'limit':'Six questions are diagnostic only; this is not an independent confirmation or production candidate.',
      'new_api_requests':0,'new_embedding_vectors':0,'dataset_sha256':E.freeze_dataset()['sha256']}
    p=C.STAGES[32]/'backend_probe_protocol.json'
    if p.exists() and C.read_json(p)!=contract:raise RuntimeError('Backend probe protocol changed')
    C.write_json(p,contract);return contract

def run():
    contract=protocol();freeze=E.freeze_dataset();cases=[q for q in freeze['queries'] if q['query_id'] in QUERY_IDS]
    experiment=E.Experiment();api=experiment.api
    cache=api.cache/'embeddings'
    keys=[C.digest({'text':q['question'],'model':C.EMBEDDING_MODEL,'dimension':1536}) for q in cases]
    if not all((cache/f'{k}.npy').exists() for k in keys):raise RuntimeError('Probe requires cached query vectors; no new API allowed')
    vectors=api.embeddings([q['question'] for q in cases]);primary=C.read_json(C.STAGES[32]/'retrieval_results.json')
    lookup={(r['query_id'],r['profile'],r['mode']):r for r in primary['records']};rows=[]
    identity=E.run_fingerprint('oracle',experiment)
    validate_primary(primary,freeze,identity)
    for case,vector in zip(cases,vectors,strict=True):
        selection={'eligible_cards':list(freeze['catalog']),'unknown_cards':[]}
        for profile in E.PROFILES:
            path=C.STAGES[32]/'backend_probe'/f"{case['query_id']}_{profile}.json"
            binding=C.digest({'contract':contract,'primary':C.digest(primary),'source':C.digest(Path(__file__).read_bytes()),'index':identity})
            if path.exists():
                row=C.read_json(path)
                if row.get('input_fingerprint')!=binding:raise RuntimeError('Stale backend probe')
                rows.append(row);continue
            started=time.perf_counter();baseline=lookup[(case['query_id'],profile,'U')]
            try:
                result=experiment.search(case['question'],vector,profile,list(freeze['catalog']))
                row={'query_id':case['query_id'],'profile':profile,'status':'ok',
                     'metrics':E.evaluate_retrieval(case,result,selection),'card_keys':[c['card_key'] for c in result['cards']],
                     'same_card_ranking_as_global':baseline['status']=='ok' and [c['card_key'] for c in baseline['result']['cards']]==[c['card_key'] for c in result['cards']]}
                row['metrics'].pop('strict_prefilter_anchor_retention',None)
            except Exception as exc:
                row={'query_id':case['query_id'],'profile':profile,'status':'failed','error':f'{type(exc).__name__}: {exc}'}
            row.update(input_fingerprint=binding,wall_seconds=time.perf_counter()-started,
                global_status=baseline['status'],global_metrics=baseline.get('metrics'),global_error=baseline.get('error'))
            if row['global_metrics'] is not None:
                row['global_metrics']=dict(row['global_metrics']);row['global_metrics'].pop('strict_prefilter_anchor_retention',None)
            C.write_json(path,row);rows.append(row);print('backend probe',case['query_id'],profile,row['status'],flush=True)
    result={'protocol':contract,'records':rows,'new_scorer_pairs':sum(c['new_pairs'] for c in experiment.scorer.calls),
            'new_api_requests':0,'new_embedding_vectors':0}
    C.write_json(C.STAGES[32]/'backend_probe_results.json',result);return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--freeze-only',action='store_true');a=p.parse_args()
    protocol() if a.freeze_only else run()
