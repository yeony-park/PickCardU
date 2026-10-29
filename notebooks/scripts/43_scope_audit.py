"""Verify the actual scoped components, beyond the inner pipeline scope trace.

The experiment's outer ScopedSearcher supplies scope while the inner
SearchConfig trace can say null. Check immutable component identity, owners,
search trace candidates and final evidence instead of interpreting null alone.
"""
import importlib.util
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('scope43_io',Path(__file__).with_name('43_offline_validation.py'))
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)

def check_scope(scope,component,owners,result):
    if component['identity']['scope']!=scope:
        raise RuntimeError('Component scope identity differs')
    if C.R.digest({k:v for k,v in component.items() if k!='sha256'})!=component['sha256']:
        raise RuntimeError('Component digest mismatch')
    for name in ['bm25','vector']:
        rows=component[name]
        if [r['chunk_id'] for r in rows]!=[r['chunk_id'] for r in result['trace'][name]]:
            raise RuntimeError('Saved search trace differs from frozen input component')
        if scope is not None and any(owners[r['chunk_id']] not in scope for r in rows):
            raise RuntimeError('Component contains an out-of-scope chunk')
    if scope is not None:
        if any(r['card_key'] not in scope for r in result['cards']+result['evidence']):
            raise RuntimeError('Saved search output escaped its scope')
    if any(owners.get(e['chunk_id'])!=e['card_key'] for e in result['evidence']):
        raise RuntimeError('Final evidence card label differs from the original chunk owner')
    return True

def run():
    import json
    previous=C.R.DATA/'42_expanded_query_evaluation'
    source=C.R.read(previous/'combined_results.json')
    public={q['query_id']:q['question'] for q in C.R.read(previous/'questions.json')['queries']}
    replay={r['query_id']:r for r in C.R.read(C.PATH/'routing_results.json')['actual_records']}
    owners={p:{r['chunk_id']:r['document_id'] for r in [json.loads(line) for line in
        (C.R.DATA/'31_operational_chunking_comparison'/f'{p}_chunks.jsonl').read_text().splitlines()]}
        for p in ['card_page_section_benefit','parent_child_bundle']}
    rows=[]
    for r in source['retrievals']:
        if r['mode'] not in ['U','S_THEN_U']:
            continue
        plan=replay[r['query_id']]
        scope=plan['normalized_plan']['card_keys'] or None if r['mode']=='U' else plan['effective_scope']
        key=C.R.digest({'question':public[r['query_id']],'profile':r['profile'],'keys':scope})
        component=C.R.read(previous/'components'/f'{key}.json')
        if component['identity']['question_sha256']!=C.R.digest(public[r['query_id']]) or component['identity']['profile']!=r['profile']:
            raise RuntimeError('Component question/profile binding differs')
        check_scope(scope,component,owners[r['profile']],r['result'])
        rows.append({'query_id':r['query_id'],'profile':r['profile'],'mode':r['mode'],'expected_scope':scope,
            'component_scope':component['identity']['scope'],'inner_config_trace_scope':r['result']['trace']['scope']['target_card_keys'],
            'passed':True,'component_sha256':component['sha256'],'component_file':f'{key}.json',
            'bm25_candidate_count':len(component['bm25']),'vector_candidate_count':len(component['vector'])})
    if len(rows)!=192:
        raise RuntimeError('Incomplete actual scope matrix')
    result={'records':rows,'passed':True,'checked_search_rows':192,'actual_scoped_rows':sum(r['expected_scope'] is not None for r in rows),
        'scoped_rows_with_null_inner_trace':sum(r['expected_scope'] is not None and r['inner_config_trace_scope'] is None for r in rows),
        'interpretation':'inner SearchConfig trace null does not describe the experiment outer ScopedSearcher; immutable component scope/owners/actual candidate trace/final output validated',
        'source_code_sha256':{name:C.R.digest((C.R.ROOT/'notebooks/scripts'/name).read_bytes()) for name in
            ['32_search_experiment.py','34_bundle_experiment.py','41_followup_runtime.py','43_scope_audit.py']},
        'input_sha256':{str(file.relative_to(C.R.ROOT)):C.R.digest(file.read_bytes()) for file in [previous/'combined_results.json',C.PATH/'routing_results.json',
            C.R.DATA/'31_operational_chunking_comparison/card_page_section_benefit_chunks.jsonl',
            C.R.DATA/'31_operational_chunking_comparison/parent_child_bundle_chunks.jsonl']},'new_api_requests':0}
    C.R.write(C.PATH/'scope_audit.json',result)
    return {k:v for k,v in result.items() if k not in ['records','source_code_sha256']}

def verify_scope_artifact(path,expected_bytes_sha256):
    if C.R.digest(path.read_bytes())!=expected_bytes_sha256:
        raise RuntimeError('Actual scope audit artifact changed')
    value=C.R.read(path)
    for name,sha in value['input_sha256'].items():
        if C.R.digest((C.R.ROOT/name).read_bytes())!=sha:
            raise RuntimeError('Actual scope input changed: '+name)
    for name,sha in value['source_code_sha256'].items():
        if C.R.digest((C.R.ROOT/'notebooks/scripts'/name).read_bytes())!=sha:
            raise RuntimeError('Actual scope code changed: '+name)
    return value

if __name__=='__main__':
    print(run())
