"""Requirement-sized audit of the completed follow-up experiments."""
if not __debug__:
    raise RuntimeError('Verification requires assertions enabled; do not use python -O or PYTHONOPTIMIZE')

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

spec=importlib.util.spec_from_file_location('final_audit_common',Path(__file__).with_name('34_followup_common.py'))
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)


def require_inspection_contract(inspection):
    if inspection['old_scores_changed'] is not False or inspection['new_truth_or_judge_scores'] is not False:
        raise RuntimeError('Quality inspection contract forbids changed scores and new truth/judge scores')


def verify():
    import nbformat
    C=F.load_module('final_audit_comparison',F.ROOT/'notebooks/scripts/36_comparison.py')
    B,A,E,scoring=C.tools()
    F.initialize(36)
    freeze,plans,retrievals,answers,protocol=C.primary_inputs()
    base=F.STAGES[36];cases={c['query_id']:c for c in freeze['queries']}
    primary=F.read_json(base/'primary_results.json');unknown=F.read_json(base/'unknown_results.json')
    assert primary['retrievals']==retrievals and primary['answers']==answers and primary['new_primary_api_calls']==0
    expected={(q,p,m) for q in cases for p in E.PROFILES for m in E.MODES}
    for name in ['retrievals','answers']:
        keys=[(r['query_id'],r['profile'],r['mode']) for r in primary[name]]
        assert len(keys)==len(set(keys))==198 and set(keys)==expected
        assert all(r['status']=='ok' for r in primary[name])
    rb={(r['query_id'],r['profile'],r['mode']):r for r in retrievals}
    for row in answers:
        retrieval=rb[(row['query_id'],row['profile'],row['mode'])]
        assert row['retrieval_sha256']==F.digest(retrieval)
        assert row['scores']==scoring.score_row(cases[row['query_id']],row,retrieval)
        assert row['model']==F.MODEL and row['reasoning']==F.REASONING
        assert row['budget_policy']==protocol['selected_answer_budget_policy']=='2400'
    assert protocol['selected_bundle_policy']=='budget5'
    chunks={p:C.unique_chunks([__import__('json').loads(line) for line in
        (F.OLD[31]/f'{p}_chunks.jsonl').read_text().splitlines()]) for p in E.PROFILES}
    index_identity=F.read_json(F.STAGES[34]/'component_freeze.json')['index_identity']
    experiment=SimpleNamespace(components={},base=SimpleNamespace(index_identity=index_identity,
        data={p:([SimpleNamespace(chunk_id=k,card_key=r['document_id']) for k,r in rows.items()],)
              for p,rows in chunks.items()}),component_key=B.FollowupExperiment.component_key)
    def no_capture(*args,**kwargs):raise AssertionError('Audit must never recapture a component')
    experiment.capture_component=no_capture
    embeddings=B.ReadOnlyEmbeddingAPI()
    strict=E.DB
    facts=E.DB.load_facts(F.OLD[30])
    assert facts==E.C.read_jsonl(F.OLD[30]/'facts.jsonl')
    expected_unknown=set()
    for query,case in cases.items():
        selection=strict.select_cards(facts,plans[query])
        if C.unknown_policy(plans[query],selection)['applicable']:
            expected_unknown.update((query,p) for p in E.PROFILES)
    keys=[(r['query_id'],r['profile']) for r in unknown['records']]
    assert len(keys)==len(set(keys))==54 and set(keys)==expected_unknown
    references=[]
    for row in unknown['records']:
        case=cases[row['query_id']];profile=row['profile']
        selection=strict.select_cards(facts,plans[row['query_id']]);policy=C.unknown_policy(plans[row['query_id']],selection)
        assert row['candidate_policy']==policy and row['protocol_sha256']==protocol['sha256']
        assert row['excluded_from_primary_winner_selection'] and row['status']=='ok'
        assert row['retrieval']['status']==row['answer']['status']=='ok'
        assert row['answer']['retrieval_sha256']==F.digest(row['retrieval'])
        assert row['answer']['scores']==scoring.score_row(case,row['answer'],row['retrieval'])
        if row['component_reference']:
            saved=F.ROOT/row['component_reference']['path'];before=F.file_record(saved)
            reference=C.freeze_unknown_component(experiment,B,case['question'],embeddings.embeddings([case['question']])[0],
                profile,policy['card_keys'],protocol)
            assert reference==row['component_reference'] and F.file_record(saved)==before
            references.append(reference)
        else:
            assert row['retrieval']==rb[(row['query_id'],profile,'S_THEN_U')]
        for evidence in row['retrieval']['result']['evidence']:
            chunk=chunks[profile][evidence['chunk_id']]
            assert evidence['card_key']==chunk['document_id'] and evidence['text'].endswith(chunk['text'])
    assert references==unknown['component_manifest'] and len(references)==52
    provider={p.stem:F.read_json(p) for p in (base/'api_cache').glob('*.json')}
    new_keys={e['key'] for r in unknown['records'] for e in r['answer']['events'] if not e['cache_hit']}
    assert len(provider)==len(new_keys)==44 and set(provider)==new_keys
    for key,record in provider.items():
        assert key==F.digest(record['request']) and record['status']=='ok'
        assert record['request']['model']==F.MODEL and record['request']['reasoning']=={'effort':F.REASONING}
        assert record['request']['max_output_tokens']==2400 and record['request']['store'] is False
        assert record['raw_response']['usage']==record['usage']
    diagnostics=F.read_json(base/'primary_diagnostics.json')
    assert len(diagnostics['records'])==198
    for row in diagnostics['records']:
        retrieval=rb[(row['query_id'],row['profile'],row['mode'])]
        assert row['same_card_union_witness']==C.union_witness(cases[row['query_id']],retrieval,E)
        if row['single_witness'] is not None:assert row['same_card_union_witness']>=row['single_witness']
    assert {r['query_id'] for r in diagnostics['s_then_complete_disagreements']}=={'Q10','Q12','Q29','Q30'}
    inspection=F.read_json(base/'quality_inspection.json')
    assert inspection['primary_results_sha256']==F.digest(primary) and inspection['primary_diagnostics_sha256']==F.digest(diagnostics)
    require_inspection_contract(inspection)
    assert len(inspection['notes'])==7
    for sample in inspection['pdf_spot_checks']:assert F.digest((F.ROOT/sample['pdf']).read_bytes())==sample['pdf_sha256']
    hierarchy=C.hierarchy_audit()
    assert hierarchy['reference_integrity_passed'] and hierarchy['chunks']==1834 and hierarchy['documents']==106
    assert hierarchy['semantic_heading_accuracy']=='미검증'
    F.write_json(base/'evaluation_summary.json',{'protocol_sha256':protocol['sha256'],'production_adopted':False,
        'records':[{k:r[k] for k in ['query_id','profile','mode','split','subgroup','status','scores','metrics']} for r in answers],
        'retrieval_records':[{k:r[k] for k in ['query_id','profile','mode','status','metrics']}|
            {'split':cases[r['query_id']]['split'],'subgroup':cases[r['query_id']]['subgroup']} for r in retrievals],
        'unknown_records':[{k:r[k] for k in ['query_id','profile','status','strict_candidate_count','union_candidate_count',
        'strict_known_retained','union_known_retained','known_gold_count','baseline_metrics','component_reference']}|
        {'metrics':r['retrieval']['metrics'],'scores':r['answer']['scores']} for r in unknown['records']]})
    R=F.load_module('final_audit_report',F.ROOT/'notebooks/scripts/36_render_comparison_report.py')
    R.execute_notebook()
    summary=F.read_json(base/'report_summary.json')
    assert len(summary['summaries'])==len({(r['split'],r['profile'],r['mode']) for r in summary['summaries']})==18
    assert len(summary['subgroups'])==60 and len(summary['wins'])==28
    assert summary['resources']['new_unknown_provider_requests']==44
    assert summary['resources']['new_bge_pairs']==sum(r['new_pairs'] for r in unknown['scorer_calls'])==274
    assert summary['resources']['new_provider_tokens']==R.A.token_totals(list(provider.values()))
    for row in summary['wins']:assert row['개선']+row['하락']+row['동률']==row['n']
    L=F.load_module('final_audit_links',F.ROOT/'notebooks/scripts/34_verify_bundle_results.py')
    document=F.ROOT/'docs/report/36_profile_search_comparison_report.html';parser=L.Links();parser.feed(document.read_text())
    broken=[link for link in parser.links if not link.startswith(('https://','#')) and not (document.parent/link).resolve().exists()]
    assert not broken,broken
    for number,name in [(34,'34_bundle_comparison.ipynb'),(35,'35_answer_budget.ipynb'),(36,'36_profile_search_comparison.ipynb')]:
        notebook=nbformat.read(F.ROOT/'notebooks'/name,as_version=4);nbformat.validate(notebook)
        cells=[c for c in notebook.cells if c.cell_type=='code']
        assert len(cells)==4 and all(c.execution_count and all(o.output_type!='error' for o in c.outputs) for c in cells)
    preservation=F.verify_preservation(36)
    assert preservation['passed'] and preservation['checked_files']==16861
    audit={'passed':True,'primary_grid_rows':198,'unknown_grid_rows':54,'component_resume_records_verified_without_capture':52,
        'provider_requests':44,'provider_failures':0,'new_primary_requests':0,'new_bge_pairs':274,
        'all_original_scores_recomputed':True,'same_card_union_verified':True,'hierarchy_reference_integrity':True,
        'semantic_hierarchy_accuracy':'미검증','detailed_disagreements_inspected':['Q10','Q12','Q29','Q30'],
        'edge_diagnostics':['Q18','Q26','Q31','Q32','Q33'],'notebook_cells_executed':12,'broken_links':broken,
        'protected_files_unchanged':16861,'full_runner_rerun_during_audit':False,'production_adopted':False,
        'html_sha256':F.digest(document.read_bytes()),'protocol_sha256':protocol['sha256'],
        'checked_code':{name:F.digest((F.ROOT/'notebooks/scripts'/name).read_bytes()) for name in
            ['36_comparison.py','36_inspect_quality.py','36_render_comparison_report.py','36_verify_followups.py']},
        'limitations':['No independent unseen questions','OCR pass assumed','Flat heading levels; meaning not verified',
            'Historical/new frozen candidates and scoped/global backend confound','Single LLM response variability',
            'Lexical partial relevance labels','Browser visual render unavailable; HTML and links checked'],
        'requirements':{'operational_builders_and_sources_read_only':'protected hash/mtime audit and34 protocol',
            'bundle_count_token_diagnosis_and_four_policies':'34 completed396-row grid, original99-row parity, selection audit',
            'answer_retry_and_three_budget_policies':'35 completed594-row grid, exact166 requests, original scoring audit',
            'benefit_parent_and_three_search_modes':'36 exact selected198 retrieval/answer bindings',
            'failures_and_common_success_denominators':'All selected198 search/answer rows successful;27/24 relevance denominators',
            'normal_success_score_disagreements':'Four query-level claims/citations inspected, seven note groups, PDF spot checks',
            'unknown_candidate_filter_loss':'54 independent diagnostic rows;52 frozen scoped components;not primary selection',
            'reports_and_reproducibility':'34/35/36 prefix matched notebooks and HTML;all12 code cells executed;links valid',
            'model_api_cost_controls':'gpt-6-luna/medium/store false/max2400 current44 keys;prior35 request166;cost unmeasured',
            'query_expansion':'Deferred as explicitly requested;not part of this branch',
            'independent_review':'Existing reviewer reviewed34/35/36 source changes;final report review pending'},
        'commit_status':'34 ed41a97 and35 c1fe030 preserved;36 final checkpoint pending'}
    F.write_json(base/'completion_audit.json',audit)
    print(F.canonical({k:v for k,v in audit.items() if k not in ['checked_code','requirements','limitations']}))
    return audit


if __name__=='__main__':
    F.initialize(36)
    verify()
