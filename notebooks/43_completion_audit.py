"""Offline source, verdict, scope, notebook and report completion evidence."""
import importlib.util
from pathlib import Path
import sys
import json
import re

spec=importlib.util.spec_from_file_location('audit43_io',Path(__file__).with_name('43_offline_validation.py'))
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)

def run():
    from bs4 import BeautifulSoup
    import nbformat
    runtime=C.initialize()
    preservation=runtime.verify()
    receipt=C.R.load('audit43_binding','43_review_binding.py').verify_reviews(C.PATH)
    old=C.R.DATA/'42_expanded_query_evaluation'
    C.R.load('audit43_old_binding','42_input_binding.py').verify_inputs(old)
    protocol=C.R.read(C.PATH/'protocol.json')
    for name,sha in protocol['sources'].items():
        assert C.R.digest((C.R.ROOT/name).read_bytes())==sha,name
    previous=C.R.read(C.PATH/'answer_review_summary.json')
    current=C.R.load('audit43_summary','43_answer_review_summary.py').summarize()
    assert current==previous
    assert len(current['conditions'])==144
    expected={(q,m) for q in [f'E{i:02}' for i in range(1,49)] for m in ['U','S_THEN_U','S_PLUS_U']}
    assert {(r['query_id'],r['mode']) for r in current['conditions']}==expected
    assert [r['PASS'] for r in current['metrics'] if r['group']=='all']==[41,44,44]
    assert all(r['wrong_core_recommendation']==0 for r in current['metrics'])
    assert all(r['pass']==4 for r in current['diagnostics'])
    assert current['posthoc'] is True and current['independent_holdout'] is False and current['production_adopted'] is False
    packets=C.R.read(C.PATH/'review_packets.json')['records']
    assert len(packets)==97
    documents={r['document_id']:r for r in map(json.loads,(C.R.DATA/'30_structured_benefit_db/documents.jsonl').read_text().splitlines())}
    source_checks={}
    for packet in packets:
        owners={e['chunk_id']:e['card_key'] for e in packet['evidence']}
        for claim in packet['answer']['claims']+packet['answer']['recommendations']:
            assert all(owners[ref]==claim['card_key'] for ref in claim['citations'])
        for e in packet['evidence']:
            page=next(p for p in documents[e['card_key']]['pages'] if p['page']==e['page_num'])
            assert ''.join(e['text'].split()) in ''.join(page['text'].split())
            source_checks[e['card_key'],e['page_num'],e['chunk_id']]=True
    assert len(source_checks)==319
    routing=C.R.read(C.PATH/'routing_results.json')
    assert len(routing['actual_records'])==routing['actual_replay_matches']==48
    assert len(routing['controlled_records'])==routing['controlled_expectation_matches']==28
    R=C.R.load('audit43_replay','43_routing_replay.py')
    fixtures=C.R.read(C.PATH/'routing_fixtures.json')
    for case in fixtures['cases']:
        result=R.replay(case['plan'],fixtures['facts'],fixtures['catalog'],parser_failed=case['parser_failed'])
        assert all(result[k]==v for k,v in case['expected'].items())
    old_scope=C.R.read(C.PATH/'scope_audit.json')
    C.R.load('audit43_scope','43_scope_audit.py').run()
    assert C.R.read(C.PATH/'scope_audit.json')==old_scope
    assert old_scope['checked_search_rows']==192 and old_scope['actual_scoped_rows']==114 and old_scope['passed']
    for name,sha in C.R.read(C.PATH/'routing_protocol.json')['sources'].items():
        assert C.R.digest((C.R.ROOT/name).read_bytes())==sha,name
    assert C.R.read(C.PATH/'readonly_db_identity.json')['identity_matches']
    assert not list((C.PATH/'api_cache').glob('*.json'))
    notebook=nbformat.read(C.R.ROOT/'notebooks/43_offline_validation.ipynb',as_version=4)
    cells=[c for c in notebook.cells if c.cell_type=='code']
    assert len(cells)==4 and all(c.execution_count is not None and all(o.output_type!='error' for o in c.outputs) for c in cells)
    report=C.R.ROOT/'docs/report/43_offline_validation_report.html'
    soup=BeautifulSoup(report.read_text(),'html.parser')
    assert soup.find_all('h2')[-1].get_text()=='최종 요약'
    assert not re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]',soup.get_text())
    for table in soup.find_all('table'):
        assert table.find_previous('p',class_='guide')
    links=[a['href'] for a in soup.find_all('a',href=True) if not a['href'].startswith(('http://','https://','#'))]
    # The audit file is the sole permitted link target created by this run.
    assert all((report.parent/href).resolve().is_file() or (report.parent/href).resolve()==C.PATH/'completion_audit.json' for href in links)
    for file in C.PATH.glob('*.json'):
        assert not re.search(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}',file.read_text()),str(file)
    result={'passed':True,'provider_requests':0,'new_usage_cost_usd':'0','old_scores_changed':False,
        'operating_code_changed':False,'preservation':preservation,'answer_conditions':144,'unique_packets':97,
        'unique_evidence_page_contiguity_checks':319,'source_review_binding':receipt['sha256'],
        'actual_plan_selector_fallback_matches':48,'controlled_matches':28,'actual_search_scope_rows':192,'scoped_rows':114,
        'sqlite_json_facts':3403,'source_card_count':106,'notebook_code_cells':4,'html_tables':len(soup.find_all('table')),
        'local_links':len(links),'browser_rendering':'미검증','report_sha256':C.R.digest(report.read_bytes()),
        'posthoc':True,'independent_holdout':False,'production_adopted':False}
    C.R.write(C.PATH/'completion_audit.json',result)
    return result

if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
