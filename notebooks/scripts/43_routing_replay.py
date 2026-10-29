"""Offline reproduction of42 parse/schema/catalog/selector/fallback gates.

This calls the saved selector and QueryPlan, not an LLM or a search engine.
It is an experiment harness; no production integration is exercised here.
"""
import importlib.util
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('route43_io',Path(__file__).with_name('43_offline_validation.py'))
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)
E=C.R.load('route43_schema','32_search_experiment.py')

def replay(value,facts,catalog,*,parser_failed=False):
    failure=None
    try:
        if parser_failed:
            raise RuntimeError('simulated parser provider failure')
        normalized=E.QueryPlan.model_validate(value).model_dump(mode='json')
        if not set(normalized['card_keys'])<=set(catalog):
            raise ValueError('Unknown parser card key')
    except Exception as error:
        failure=f'{type(error).__name__}: {error}'
        normalized={'route':'unstructured','groups':[],'card_keys':[],'unresolved':['parser failure']}
    selection=E.DB.select_cards(facts,normalized)
    fallback=not selection['eligible_cards'] or bool(normalized['unresolved']) or normalized['route']=='unstructured'
    scope=(normalized['card_keys'] or None) if fallback else selection['eligible_cards']
    return {'parse_gate':'failed' if failure else 'ok','parse_failure':failure,'normalized_plan':normalized,
        'eligible_cards':selection['eligible_cards'],'unknown_cards':selection['unknown_cards'],
        'selection':selection,'fallback':fallback,'effective_scope':scope,
        'nonempty_selection_with_no_question_semantic_validation':bool(selection['eligible_cards']) and not fallback}

def run():
    import time, resource
    started=time.perf_counter()
    cpu_start=resource.getrusage(resource.RUSAGE_SELF)
    runtime=C.initialize()
    previous=C.R.DATA/'42_expanded_query_evaluation'
    contract=C.R.read(C.PATH/'protocol.json')
    for name,sha in contract['sources'].items():
        if C.R.digest((C.R.ROOT/name).read_bytes())!=sha:
            raise RuntimeError('Frozen source changed: '+name)
    fixture=C.R.read(C.PATH/'routing_fixtures.json')
    if C.R.digest({k:v for k,v in fixture.items() if k!='sha256'})!=fixture['sha256']:
        raise RuntimeError('Frozen fixture changed')
    C.R.freeze(C.PATH/'routing_protocol.json',{'fixture_sha256':fixture['sha256'],
        'schema_sha256':C.R.digest(E.QueryPlan.model_json_schema()),
        'gate_source':'42_expanded_query_evaluation.py: parser exception/catalog check and fallback expression',
        'orchestration':'offline reproduction, not full provider/production execution',
        'sources':{str(p.relative_to(C.R.ROOT)):C.R.digest(p.read_bytes()) for p in [Path(__file__),C.R.ROOT/'notebooks/scripts/43_routing_fixtures.py',
            C.R.ROOT/'notebooks/scripts/30_build_structured_db.py',C.R.ROOT/'notebooks/scripts/32_search_experiment.py']},'new_api_requests':0})
    # The existing immutable fact JSON is sufficient for selector replay.
    import json
    facts=[json.loads(line) for line in (C.R.DATA/'30_structured_benefit_db/facts.jsonl').read_text().splitlines()]
    catalog=C.R.read(previous/'catalog.json')['catalog']
    plans=C.R.read(previous/'parser_results.json')['records']
    saved=C.R.read(previous/'combined_results.json')['retrievals']
    actual=[]
    for plan in plans:
        row=replay(plan['plan'],facts,catalog,parser_failed=plan['status']!='ok')
        records=[r for r in saved if r['query_id']==plan['query_id'] and r['mode']=='S_THEN_U']
        matches=all(row['selection']==r['selection'] and row['fallback']==r['fallback'] for r in records)
        if len(records)!=2 or not matches:
            raise RuntimeError('Stored selector/routing mismatch: '+plan['query_id'])
        actual.append({'query_id':plan['query_id'],'stored_profiles':2,'matches_saved':matches,**row})
    controlled=[]
    for case in fixture['cases']:
        row=replay(case['plan'],fixture['facts'],fixture['catalog'],parser_failed=case['parser_failed'])
        expectation_matches=all(row[field]==wanted for field,wanted in case['expected'].items())
        oracle=case['oracle_eligible_cards']
        controlled.append({'case':case['name'],'risk_kind':case['risk_kind'],'matches_frozen_expectation':expectation_matches,
            'correct_cards_lost':sorted(set(oracle or [])-set(row['eligible_cards'])) if oracle is not None else None,
            'incorrect_cards_admitted':sorted(set(row['eligible_cards'])-set(oracle or [])) if oracle is not None else None,**row})
    result={'actual_records':actual,'controlled_records':controlled,'actual_replay_matches':sum(r['matches_saved'] for r in actual),
        'controlled_expectation_matches':sum(r['matches_frozen_expectation'] for r in controlled),
        'new_api_requests':0,'note':'검사 통과는 현재 동작과 고정 기대값의 일치이며 질문 의미 안전성 보장은 아님'}
    cpu_end=resource.getrusage(resource.RUSAGE_SELF)
    result['resources']={'wall_seconds':time.perf_counter()-started,
        'cpu_seconds':cpu_end.ru_utime+cpu_end.ru_stime-cpu_start.ru_utime-cpu_start.ru_stime,
        'peak_rss_kib':cpu_end.ru_maxrss,'gpu':'사용 안함','provider_requests':0,'new_embedding_vectors':0,
        'new_usage_cost_usd':'0','scope':'오프라인 재생 프로세스 측정. Codex 검토 시간/모델 사용료·실제 청구액은 별도이며 미측정'}
    C.R.write(C.PATH/'routing_results.json',result)
    runtime.verify()
    return {'actual_matches':result['actual_replay_matches'],'controlled_matches':result['controlled_expectation_matches'],'new_api_requests':0}

if __name__=='__main__':
    print(run())
