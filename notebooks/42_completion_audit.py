"""Offline completion audit of matrix, bindings, costs, review and report.

Recalculates saved metrics; it never creates a model, submits a request, retries
an answer, or modifies frozen inputs/scores. The audit belongs to this notebook
because its result preservation and numbered artifacts are experiment-specific.
"""
import importlib.util
from pathlib import Path
import sys
import re

spec = importlib.util.spec_from_file_location('audit42_runtime', Path(__file__).with_name('41_followup_runtime.py'))
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)

def run():
    from bs4 import BeautifulSoup
    import nbformat
    path = R.STAGES[42]
    I = R.load('audit42_inputs','42_input_binding.py')
    I.verify_inputs(path)
    M = R.load('audit42_scores','39_answer_rubric.py')
    T = R.load('audit42_retrieval','40_new_query_comparison.py')
    V = R.load('audit42_report','42_render_report.py')
    PRICE = R.load('audit42_price','36_cost_accounting.py')
    runtime = R.Runtime(42)
    preservation = runtime.verify()
    dataset = R.read(path/'dataset.json')
    cases = {q['query_id']:{**q,'plan':q['gold_plan']} for q in dataset['queries']}
    protocol = R.read(path/'protocol.json')
    execution = R.read(path/'execution_protocol.json')
    for source, digest in {**protocol['sources'],**execution['sources']}.items():
        assert R.digest((R.ROOT/source).read_bytes()) == digest, source
    assert R.digest((R.ROOT/'notebooks/39_answer_rubric.py').read_bytes())==protocol['scorer_sha256']
    assert len(cases)==48 and sum(bool(q['gold_cards']) for q in cases.values())==44
    assert sum(bool(q['expected_behavior']) for q in cases.values())==4
    combined = R.read(path/'combined_results.json')
    expected = {(q,p,m) for q in cases for p in protocol['profiles'] for m in protocol['modes']}
    for kind in ['retrievals','answers']:
        assert len(combined[kind])==384 and {(r['query_id'],r['profile'],r['mode']) for r in combined[kind]}==expected
    retrieved = {(r['query_id'],r['profile'],r['mode']):r for r in combined['retrievals']}
    answered = {(r['query_id'],r['profile'],r['mode']):r for r in combined['answers']}
    facts = {f['fact_id']:f for f in [__import__('json').loads(line) for line in (R.DATA/'30_structured_benefit_db/facts.jsonl').read_text().splitlines()]}
    for key in expected:
        qid,profile,mode=key
        name=f'{qid}_{profile}_{mode}.json'
        raw_r=R.read(path/'retrieval'/name)
        raw_a=R.read(path/'answers'/name)
        row=retrieved[key]
        assert raw_a['retrieval_sha256']==R.digest(raw_r)
        assert raw_a['original_answer_question']==cases[qid]['question']
        assert raw_r['execution_sha256']==raw_a['execution_sha256']==execution['sha256']
        assert {k:v for k,v in row.items() if k not in ['metrics','subgroup']}==raw_r
        assert {k:v for k,v in answered[key].items() if k not in ['scores','subgroup']}==raw_a
        metrics=T.retrieval_metrics(cases[qid],raw_r['result'],None,facts)
        assert metrics==row['metrics'],name
        score = M.score(cases[qid],raw_a['answer'],raw_r['result']['evidence']) if raw_a['status']=='ok' else {
            'known_positive_complete':0 if cases[qid]['gold_cards'] else None,'citation_ownership':0,'explicit_scope_preserved':0,'unsupported_percent_claims':0}
        assert score==answered[key]['scores'],name
    parser=R.read(path/'parser_results.json')['records']
    assert len(parser)==48 and all(r['status']=='ok' and r['execution_sha256']==execution['sha256'] for r in parser)
    summary=V.build_summary()
    review=R.read(path/'quality_review.json')
    decision=R.read(path/'decision.json')
    V.validate_decision(summary,review,decision,R.digest((path/'combined_results.json').read_bytes()))
    manifest=R.read(path/'quality_review_manifest.json')
    needed={(q,p,m) for q in manifest['changed_positive_queries']+manifest['diagnostic_queries'] for p in protocol['profiles'] for m in protocol['modes']}
    needed.update(tuple(k) for k in manifest['guard_violation_conditions'])
    review_keys={(r['query_id'],r['profile'],r['mode']) for r in review['records']}
    assert len(review_keys)==len(review['records']) and needed<=review_keys
    assert review['required_conditions']==len(needed)
    for r in review['records']:
        key=r['query_id'],r['profile'],r['mode']
        assert r['row_sha256']==R.digest(answered[key]) and r['frozen_score']==answered[key]['scores']['known_positive_complete']
    files=sorted((path/'api_cache').glob('*.json'))
    records=[]
    for file in files:
        record=R.read(file)
        assert record['status'] in ['ok','failed']
        assert record['usage']==record['raw_response']['usage']
        records.append(('42',str(file.relative_to(R.ROOT)),record))
    ledger=R.read(path/'cost_accounting.json')
    priced=PRICE.price_records(records)
    assert priced==ledger['records'] and PRICE.aggregate(priced)==ledger['summary']
    run_summary=R.read(path/'run_summary.json')
    assert run_summary['cost']==ledger['summary'] and ledger['summary']['unknown_cost_records']==0
    assert len(files)==307 and run_summary['retrieval_success_rows']==384 and run_summary['answer_success_rows']==383
    failed=[r for r in answered.values() if r['status']!='ok']
    assert len(failed)==1 and 'duplicate_card_recommendation' in failed[0]['error']
    report=R.ROOT/'docs/report/42_expanded_query_evaluation_report.html'
    soup=BeautifulSoup(report.read_text(),'html.parser')
    assert soup.find_all('h2')[-1].get_text()=='최종 요약'
    assert not re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]',soup.get_text())
    for table in soup.find_all('table'):
        assert table.find_previous('p',class_='guide')
    links=[link['href'] for link in soup.find_all('a',href=True) if not link['href'].startswith(('https://','http://','#'))]
    assert all((report.parent/link).resolve().is_file() for link in links)
    notebook=nbformat.read(R.ROOT/'notebooks/42_expanded_query_evaluation.ipynb',as_version=4)
    cells=[c for c in notebook.cells if c.cell_type=='code']
    assert len(cells)==4 and all(c.execution_count is not None and all(o.output_type!='error' for o in c.outputs) for c in cells)
    scanned=0
    for file in path.rglob('*.json'):
        if 'indexes' in file.parts or 'execution_cache' in file.parts:
            continue
        assert not re.search(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}',file.read_text()),str(file)
        scanned+=1
    result={'passed':True,'full_matrix_rows':384,'retrieval_success_rows':384,'answer_success_rows':383,
        'parser_success_rows':48,'frozen_receipt_and_code_verified':True,'all_scores_recalculated':True,
        'prior_and_operating_preservation':preservation,'source_review_conditions':len(review_keys),
        'required_source_review_conditions':len(needed),'decision_bound_and_recalculated':True,
        'physical_api_requests':307,'known_cost_usd':ledger['summary']['known_cost_usd'],
        'notebook_code_cells_executed':4,'paid_runner_rerun':False,
        'html_tables':len(soup.find_all('table')),'local_links_checked':len(links),
        'secret_scan_json_files':scanned,'browser_rendering':'미검증','report_sha256':R.digest(report.read_bytes())}
    R.write(path/'completion_audit.json',result)
    return result

if __name__=='__main__':
    print(__import__('json').dumps(run(),ensure_ascii=False,indent=2))
