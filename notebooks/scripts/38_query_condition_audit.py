"""Offline controlled parser faults: gold selector, faulty selector, final retrieval."""
import copy
import importlib.util
from pathlib import Path
import sys
s=importlib.util.spec_from_file_location('condition_common',Path(__file__).with_name('37_followup_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)

def run():
    assert (C.STAGES[37]/'notebook_execution.json').exists()
    C.initialize(38)
    E=C.load('condition_prior_search','32_search_experiment.py');C.initialize(38)
    frozen=E.freeze_dataset();cases={r['query_id']:r for r in frozen['queries']};facts=E.DB.load_facts(E.C.STAGES[30])
    variants=[]
    def add(name,q,change=None,failed=False):
        plan=copy.deepcopy(cases[q]['plan']);change(plan) if change else None
        variants.append({'case':name,'query_id':q,'plan':plan,'parser_failed':failed})
    add('정상 추출','Q01')
    add('추출 실패','Q01',failed=True)
    add('미해결 조건','Q01',lambda p:p.update(unresolved=['지원하지 않는 조건']))
    add('후보0','Q01',lambda p:p['groups'][0][0].update(min_percent=1000))
    add('일반 조언','Q23')
    add('할인→적립','Q01',lambda p:p['groups'][0][0].update(action='earn'))
    add('10→50%','Q01',lambda p:p['groups'][0][0].update(min_percent=50))
    add('스타벅스→넷플릭스','Q01',lambda p:p['groups'][0][0].update(targets=['넷플릭스']))
    add('AND→OR','Q06',lambda p:p.update(groups=[[r] for r in p['groups'][0]]))
    add('월→일','Q12',lambda p:p['groups'][0][0].update(cap_period='day'))
    add('잘못된 카드 범위','Q01',lambda p:p.update(card_keys=['BC/BC_Baro_KaPick']))
    contract=C.freeze(C.STAGES[38]/'protocol.json',{'variants':variants,'source_dataset_sha256':frozen['sha256'],
        'expected_fallback':['추출 실패','미해결 조건','후보0','일반 조언','월→일','잘못된 카드 범위'],
        'limits':'Injected faults are sensitivity diagnostics, not actual error frequency. Source-relative partial gold.',
        'new_api_requests':0})
    B,x=C.experiment(38);rows=[]
    for variant in variants:
        case=cases[variant['query_id']];plan=variant['plan']
        if variant['parser_failed']:plan={'route':'unstructured','groups':[],'card_keys':[],'unresolved':['parser failure']}
        E.QueryPlan.model_validate(plan)
        oracle=E.DB.select_cards(facts,case['plan']);selected=E.DB.select_cards(facts,plan)
        fallback=not selected['eligible_cards'] or bool(plan['unresolved']) or plan['route']=='unstructured'
        keys=(plan['card_keys'] or None) if fallback else selected['eligible_cards']
        embedding=x.api.embeddings([case['question']])[0]
        C.component(B,x,38,case['question'],embedding,'card_page_section_benefit',keys)
        try:
            result=x.search(case['question'],embedding,'card_page_section_benefit',keys,'budget5')
            metrics=E.evaluate_retrieval(case,result,selected);retrieval={'status':'ok','result':result,'metrics':metrics}
        except Exception as e:retrieval={'status':'failed','error':f'{type(e).__name__}: {e}'}
        gold=set(case['gold_cards']);oracle_set=set(oracle['eligible_cards']);faulty=set(selected['eligible_cards'])
        row={**variant,'schema_valid':True,'fallback':fallback,'gold_cards':sorted(gold),'gold_count':len(gold),
          'gold_selector_cards':sorted(oracle_set),'faulty_selector_cards':sorted(faulty),'effective_search_scope':keys,
          'source_or_selector_gold_loss':sorted(gold-oracle_set),'additional_parser_gold_loss':sorted((gold&oracle_set)-faulty),
          'effective_scope_gold_loss':sorted(gold-set(keys)) if keys is not None else [],'retrieval':retrieval}
        rows.append(row);C.write(C.STAGES[38]/'condition_results.json',{'records':rows,'completed':len(rows),'expected':len(variants),
            'new_api_requests':0,'protocol_sha256':contract['sha256']});print(variant['case'],'fallback',fallback,'scope_loss',len(row['effective_scope_gold_loss']),flush=True)
    actual=[]
    records=C.read(C.F.OLD[33]/'parser_results.json')['records']
    for r in records:
        case=cases[r['query_id']];plan=r.get('plan')
        selector=E.DB.select_cards(facts,plan) if plan else None
        actual.append({'query_id':r['query_id'],'status':r['status'],'metrics':r.get('metrics'),
           'selector_gold_retained':len(set(case['gold_cards'])&set(selector['eligible_cards'])) if selector else 0,
           'gold_count':len(case['gold_cards'])})
    result={'records':rows,'completed':len(rows),'expected':len(variants),'new_api_requests':0,'protocol_sha256':contract['sha256'],
        'actual_prior33':actual,'actual_prior33_source_sha256':C.digest((C.F.OLD[33]/'parser_results.json').read_bytes()),
        'bge_new_pairs':sum(r['new_pairs'] for r in x.scorer.calls),'note':'Candidate losses do not by themselves establish an incorrect generated answer; no answer provider called.'}
    C.write(C.STAGES[38]/'condition_results.json',result)
    return result
if __name__=='__main__':run()
