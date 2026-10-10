"""Read-only result audit; write only a compact experiment audit JSON.

Paid calls, original database writes and inference are never performed here.
Index identity uses the same validation as retrieval so stale cached rows fail.
"""
from pathlib import Path
import importlib.util
import sys
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

s=importlib.util.spec_from_file_location('audit_search',Path(__file__).with_name('32_search_experiment.py'))
E=importlib.util.module_from_spec(s);sys.modules[s.name]=E;s.loader.exec_module(E);C=E.C

def require(condition, message):
    if not condition:raise RuntimeError(message)

def validate_grid(rows, expected, label):
    keys=[(r['query_id'],r['profile'],r['mode']) for r in rows]
    require(len(keys)==len(expected) and set(keys)==expected,label+' incomplete/duplicate grid')

class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.meta={}
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.links.extend(v for k,v in attrs if k=='href' and v)
        if tag=='meta':
            fields=dict(attrs)
            if 'name' in fields:self.meta[fields['name']]=fields.get('content')

def read_only_call(module,function):
    """Use the runner's exact contract/verification without writing inputs."""
    original=module.C.write_json;module.C.write_json=lambda *args,**kwargs:None
    try:return function()
    finally:module.C.write_json=original

def run():
    freeze=E.freeze_dataset();content={k:v for k,v in freeze.items() if k!='sha256'}
    require(C.digest(content)==freeze['sha256'],'Dataset self hash mismatch')
    expected={(q['query_id'],p,m) for q in freeze['queries'] for p in E.PROFILES for m in E.MODES}
    experiment=E.Experiment();answering_sha=C.digest((C.ROOT/'packages/rag-core/src/pickcardu_rag/answering.py').read_bytes())
    scoring=C.load_module('audit_scoring',Path(__file__).with_name('32_score_answers.py'))
    score_contract=read_only_call(scoring,scoring.freeze_protocol)
    summaries=C.load_module('audit_summaries',Path(__file__).with_name('30_summarize_experiments.py'))
    stages={}
    for stage,source in [(32,'oracle'),(33,'llm')]:
        directory=C.STAGES[stage];retrieval=C.read_json(directory/'retrieval_results.json')
        answers=C.read_json(directory/'answer_results.json');scores=C.read_json(directory/'answer_constraint_scores.json')
        summary=C.read_json(directory/'evaluation_summary.json')
        for name,data in [('retrieval',retrieval),('answers',answers),('scores',scores),('summary',summary)]:
            validate_grid(data['records'],expected,f'{stage} {name}')
        require(retrieval['dataset_sha256']==answers['dataset_sha256']==summary['dataset_sha256']==freeze['sha256'],'Stage dataset mismatch')
        require(answers['model']==C.MODEL and answers['reasoning']==C.REASONING,'Answer model mismatch')
        identity=E.run_fingerprint(source,experiment)
        require(all(r['input_fingerprint']==identity for r in retrieval['records']),'Stale retrieval fingerprint')
        lookup={(r['query_id'],r['profile'],r['mode']):r for r in retrieval['records']}
        for row in answers['records']:
            binding=C.digest({'retrieval':lookup[(row['query_id'],row['profile'],row['mode'])],
                             'model':C.MODEL,'reasoning':C.REASONING,'answer_source':answering_sha})
            require(row['input_fingerprint']==binding,'Stale answer binding')
        require(scores['answer_results_sha256']==C.digest(answers) and scores['retrieval_results_sha256']==C.digest(retrieval),'Stale answer scores')
        require(scores['version']==scoring.VERSION and scores['protocol_sha256']==C.digest(score_contract),'Stale score contract')
        require(summary['input_sha256']=={'retrieval':C.digest(retrieval),'answers':C.digest(answers),'answer_scores':C.digest(scores)},'Stale compact summary')
        require(summary['summarizer_sha256']==C.digest(Path(__file__).with_name('30_summarize_experiments.py').read_bytes()),'Stale summary implementation')
        selections=[{'query_id':q['query_id'],**C.read_json(directory/'selections'/f"{q['query_id']}.json")} for q in freeze['queries']]
        diagnostic=C.read_json(directory/'failure_diagnostics.json')
        require(summary['selections']==selections and summary['failure_diagnostics']==diagnostic,'Stale copied selection/diagnostic summary')
        require(summary['answer_failure_followup']==summaries.compact_followups(stage),'Stale copied answer followup summary')
        if stage==33:require(summary['parser_records']==C.read_json(directory/'parser_results.json')['records'],'Stale copied parser summary')
        require(diagnostic['primary_sha256']==C.digest(retrieval),'Stale failure diagnostics')
        stages[str(stage)]={'rows':len(expected),'retrieval':dict(Counter(r['status'] for r in retrieval['records'])),
                            'answers':dict(Counter(r['status'] for r in answers['records'])),'input_fingerprint':identity}
    routing=C.load_module('audit_routing',Path(__file__).with_name('33_query_routing.py'))
    parser_data=C.read_json(C.STAGES[33]/'parser_results.json');parser=parser_data['records']
    ids={q['query_id'] for q in freeze['queries']}
    require(len(parser)==len(ids) and {r['query_id'] for r in parser}==ids,'Parser grid mismatch')
    parser_contract={'instructions':routing.INSTRUCTIONS+'\n카드 catalog:\n'+C.canonical(freeze['catalog']),
                     'schema':E.QueryPlan.model_json_schema(),'model':C.MODEL,'reasoning':C.REASONING,'dataset_sha256':freeze['sha256']}
    parser_contract['sha256']=C.digest(parser_contract)
    require(C.read_json(C.STAGES[33]/'parser_contract.json')==parser_contract,'Stale parser contract')
    require(parser_data['model']==C.MODEL and parser_data['reasoning']==C.REASONING,'Parser model mismatch')
    cases={q['query_id']:q for q in freeze['queries']};by_id={r['query_id']:r for r in parser}
    require(all(r['input_fingerprint']==C.digest({'contract':parser_contract['sha256'],'question':cases[r['query_id']]['question']}) for r in parser),'Stale parser row')
    for row in parser:
        if row['status']!='ok':continue
        plan=E.QueryPlan.model_validate(row['plan']).model_dump(mode='json');gold_plan=cases[row['query_id']]['plan']
        require(set(plan['card_keys'])<=set(freeze['catalog']),'Unknown parser card')
        actual=E.normalized_plan(plan);gold=E.normalized_plan(gold_plan)
        field_errors={field:int(sorted(C.canonical(r[field]) for g in actual['groups'] for r in g)!=
                               sorted(C.canonical(r[field]) for g in gold['groups'] for r in g))
                      for field in ['targets','action','min_percent','max_spend_krw','min_cap_krw','cap_period','exclude_targets']}
        metrics={'route_match':int(plan['route']==gold_plan['route']),
          'condition_exact_match':int(actual['groups']==gold['groups']),
          'card_scope_match':int(set(plan['card_keys'])==set(gold_plan['card_keys'])),
          'unresolved':int(bool(plan['unresolved'])),
          'candidate_set_match':int(set(E.DB.select_cards(experiment.facts,plan)['eligible_cards'])==set(E.DB.select_cards(experiment.facts,gold_plan)['eligible_cards'])),
          'boolean_group_match':int(sorted(map(len,plan['groups']))==sorted(map(len,gold_plan['groups']))),
          'field_errors':field_errors}
        require(row['metrics']==metrics,'Parser diagnostic metrics differ from current source predicates')
    backend=C.load_module('audit_backend',Path(__file__).with_name('32_backend_scope_probe.py'))
    backend_contract=read_only_call(backend,backend.protocol)
    probe=C.read_json(C.STAGES[32]/'backend_probe_results.json');probe_rows=probe['records']
    probe_keys=[(r['query_id'],r['profile']) for r in probe_rows]
    require(len(probe_keys)==12 and set(probe_keys)=={(q,p) for q in backend.QUERY_IDS for p in E.PROFILES},'Backend probe grid mismatch')
    primary=C.read_json(C.STAGES[32]/'retrieval_results.json')
    probe_binding=C.digest({'contract':backend_contract,'primary':C.digest(primary),
                          'source':C.digest(Path(backend.__file__).read_bytes()),'index':stages['32']['input_fingerprint']})
    require(probe['protocol']==backend_contract and all(r['input_fingerprint']==probe_binding for r in probe_rows),'Stale backend probe')
    repeat_runner=C.load_module('audit_repeat',Path(__file__).with_name('33_repeat_query_plans.py'))
    repeat_contract=read_only_call(repeat_runner,repeat_runner.protocol)
    repeat=C.read_json(C.STAGES[33]/'repeat_results.json')
    trial_keys=[(r['query_id'],r['trial']) for r in repeat['records']]
    require(len(trial_keys)==15 and set(trial_keys)=={(q,t) for q in repeat_runner.QUERY_IDS for t in range(3)},'Repeat grid mismatch')
    require(repeat['protocol']==repeat_contract,'Stale repeat protocol')
    expected_summary=[]
    for q in repeat_runner.QUERY_IDS:
        rows=[r for r in repeat['records'] if r['query_id']==q]
        for row in rows:
            if row['trial']==0:
                require(row['status']==by_id[q]['status'] and row.get('plan')==by_id[q].get('plan'),'Stale primary repeat')
            else:require(row['input_fingerprint']==C.digest({'protocol':repeat_contract,'question':cases[q]['question'],'trial':row['trial']}),'Stale repeat row')
        plans=[E.normalized_plan(r['plan']) for r in rows if r['status']=='ok']
        expected_summary.append({'query_id':q,'attempts':3,'ok':len(plans),
          'unique_conditions':len({C.canonical(p) for p in plans}),
          'unique_routes':len({r['plan']['route'] for r in rows if r['status']=='ok'})})
    require(repeat['summary']==expected_summary,'Repeat summary differs from records')
    followup=C.load_module('audit_answer_followup',Path(__file__).with_name('32_answer_failure_followup.py'))
    followup_contract=read_only_call(followup,followup.protocol)
    follow=C.read_json(C.STAGES[32]/'answer_failure_followup_results.json')
    require(follow['protocol']==followup_contract and follow['primary_preserved'],'Answer followup contract mismatch')
    follow_keys=[(r['stage'],r['query_id'],r['profile'],r['mode'],r['kind']) for r in follow['records']]
    require(len(follow_keys)==len(set(follow_keys)),'Duplicate answer followup rows')
    expected_offline=set();expected_budget=set()
    for stage in [32,33]:
        primary=C.read_json(C.STAGES[stage]/'answer_results.json')['records']
        retrieved=C.read_json(C.STAGES[stage]/'retrieval_results.json')['records']
        lookup={(r['query_id'],r['profile'],r['mode']):r for r in retrieved}
        for row in primary:
            key=(row['query_id'],row['profile'],row['mode']);retrieval=lookup[key]
            if row['status']=='ok' or retrieval['status']!='ok':continue
            if followup.first_failure(cases[key[0]]['question'],retrieval['result']['evidence']) is None:continue
            expected_offline.add((stage,*key,'offline_retry'))
            offline=next(r for r in follow['records'] if (r['stage'],r['query_id'],r['profile'],r['mode'],r['kind'])==(stage,*key,'offline_retry'))
            if offline['status']!='ok':expected_budget.add((stage,*key,'larger_budget'))
            for r in [r for r in follow['records'] if (r['stage'],r['query_id'],r['profile'],r['mode'])==(stage,*key)]:
                binding=C.digest({'contract':followup_contract,'primary_answer':row,'retrieval':retrieval,'kind':r['kind'],
                                 'helper_sha256':C.digest(Path(followup.__file__).read_bytes())})
                require(r['input_fingerprint']==binding,'Stale answer followup row')
                require(r['max_output_tokens']==(2400 if r['kind']=='offline_retry' else 4800),'Answer followup budget mismatch')
                if r['kind']=='offline_retry':require(r['new_provider_attempts']==r['new_successful_api_requests']==0,'Offline retry used provider')
    require(set(follow_keys)==expected_offline|expected_budget,'Answer followup selection/grid mismatch')
    reports=C.load_module('audit_reports',Path(__file__).with_name('30_render_experiment_reports.py'))
    notebooks={};report_links={}
    for stage,name in [(30,'structured_benefit_db'),(31,'operational_chunking_comparison'),(32,'structured_search_comparison'),(33,'query_routing')]:
        notebook=C.read_json(C.ROOT/'notebooks'/f'{stage}_{name}.ipynb')
        cells=[c for c in notebook['cells'] if c['cell_type']=='code']
        require(bool(cells) and all(c.get('execution_count') is not None and not any(o.get('output_type')=='error' for o in c.get('outputs',[])) for c in cells),'Notebook has unexecuted/error cells')
        require(notebook['metadata'].get('experiment_input_fingerprint')==C.digest(reports.artifact_inputs(stage)),'Stale notebook inspection')
        notebooks[str(stage)]={'code_cells':len(cells),'all_executed_without_error':True}
        path=C.ROOT/'docs/report'/f'{stage}_{name}_report.html';reader=Links();reader.feed(path.read_text())
        require(reader.meta.get('experiment-input-fingerprint')==C.digest(reports.report_inputs(stage)),'Stale rendered report')
        for link in reader.links:
            part=urlsplit(link)
            if part.scheme or part.netloc or not part.path:continue
            require((path.parent/unquote(part.path)).resolve().exists(),'Broken report link: '+link)
        report_links[str(stage)]=len(reader.links)
    cache=C.STAGES[30]/'api_cache';models=Counter();reasoning=Counter();statuses=Counter()
    for path in cache.glob('*.json'):
        row=C.read_json(path);request=row.get('request',{});statuses[row['status']]+=1
        if 'schema' in request:
            require(request['model']==C.MODEL and request.get('reasoning',{}).get('effort')==C.REASONING,'Unexpected paid LLM setting')
            models[request['model']]+=1;reasoning[request['reasoning']['effort']]+=1
        elif 'model' in request:require(request['model']==C.EMBEDDING_MODEL,'Unexpected embedding model')
    require(statuses['started']==0,'API request remains started')
    verifier=C.load_module('audit_preservation',Path(__file__).with_name('30_verify_preservation.py'))
    preservation=read_only_call(verifier,verifier.verify)
    require(preservation['content_size_mtime_preserved'],'Original preservation failed')
    require(not (C.ROOT/'.pytest_cache').exists(),'Pytest cache outside notebooks')
    baseline={r['path'] for r in C.read_json(C.STAGES[30]/'original_files_manifest.json')['files']}
    new_outside=[]
    def walk(directory):
        for p in directory.iterdir():
            if p.name in {'.git','.cache','__pycache__','node_modules'}:continue
            if p.is_dir():yield from walk(p)
            elif p.is_file():yield p
    for path in walk(C.ROOT):
        relative=path.relative_to(C.ROOT)
        if str(relative) not in baseline and relative.parts[0]!='notebooks' and relative.parts[:2]!=('docs','report'):
            new_outside.append(str(relative))
    require(not new_outside,'New files outside authorized scope: '+str(new_outside))
    result={'schema':'structured-search-final-audit-v1','model':C.MODEL,'reasoning':C.REASONING,
      'dataset_sha256':freeze['sha256'],'stages':stages,'parser_rows':len(parser),
      'answer_failure_followup_rows':len(follow_keys),
      'notebooks':notebooks,'report_local_links_checked':report_links,
      'api_cache_current_statuses':dict(statuses),'llm_request_models':dict(models),'llm_request_reasoning':dict(reasoning),
      'new_files_outside_scope':new_outside,'original_preservation':preservation,
      'limit':'Checks consistency and provenance, not PDF truth, semantic answer correctness or production readiness.'}
    C.write_json(C.STAGES[30]/'final_execution_audit.json',result);print(C.canonical(result));return result

if __name__=='__main__':run()
