"""Post-hoc failure-path and unknown-selection diagnostics on saved results.

No source, primary result, search call or answer is altered. A degraded fusion
projection keeps the surviving component when the other component failed.
It is a development follow-up and is never substituted for the primary grid.
"""
from pathlib import Path
import importlib.util
import sys
from collections import Counter

s=importlib.util.spec_from_file_location('failure_experiment',Path(__file__).with_name('32_search_experiment.py'))
E=importlib.util.module_from_spec(s);sys.modules[s.name]=E;s.loader.exec_module(E);C=E.C

def run(source):
    d=C.STAGES[32] if source=='oracle' else C.STAGES[33]
    primary=C.read_json(d/'retrieval_results.json');freeze=E.freeze_dataset()
    cases={q['query_id']:q for q in freeze['queries']};rows=primary['records']
    lookup={(r['query_id'],r['profile'],r['mode']):r for r in rows};degraded=[];selection=[]
    errors=Counter(r['error'] for r in rows if r['status']!='ok')
    for q in cases.values():
        saved=C.read_json(d/'selections'/f"{q['query_id']}.json");chosen=saved['selection']
        strict=set(chosen['eligible_cards']);union=strict|set(chosen['unknown_cards']);gold=set(q['gold_cards'])
        selection.append({'query_id':q['query_id'],'gold_anchors':len(gold),'strict_cards':len(strict),
          'unknown_union_cards':len(union),'strict_retained_anchors':len(gold&strict),
          'union_retained_anchors':len(gold&union),'fallback':saved['fallback']})
        for profile in E.PROFILES:
            fusion=lookup[(q['query_id'],profile,'S_PLUS_U')]
            if fusion['status']=='ok':continue
            u=lookup[(q['query_id'],profile,'U')];scoped=lookup[(q['query_id'],profile,'S_THEN_U')]
            survivors=[r for r in [u,scoped] if r['status']=='ok']
            if len(survivors)!=1:continue
            winner=survivors[0]
            degraded.append({'query_id':q['query_id'],'profile':profile,'surviving_mode':winner['mode'],
              'primary_fusion_status':'failed','projected_metrics':winner['metrics'],
              'card_keys':[r['card_key'] for r in winner['result']['cards']],
              'interpretation':'Counterfactual reuse of saved component output; no new retrieval/answer; not an independently confirmed gain.'})
    result={'version':'failure-followup-v1','source':source,'primary_sha256':C.digest(primary),
      'post_hoc_development_only':True,'trigger':'Observed parent bundle failures and conservative unknown fields.',
      'error_counts':dict(errors),'degraded_fusion_projection':degraded,'selection_diagnostics':selection,
      'new_api_requests':0,'new_embedding_vectors':0,'new_scorer_pairs':0,
      'limitations':'Unknown union measures prospective candidate retention only, not ranking or answer quality. Degraded projection is not the primary fusion rule. No original output is changed.'}
    C.write_json(d/'failure_diagnostics.json',result);print(source,'failure diagnostics',len(degraded));return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source',choices=['oracle','llm'],default='oracle');a=p.parse_args();run(a.source)
