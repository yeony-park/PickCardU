"""Read-only provider replay and artifact audit; writes only its 45 receipt."""
from collections import Counter
import importlib.util
from pathlib import Path
import re
import sys
from urllib.parse import unquote

spec=importlib.util.spec_from_file_location('io45_audit',Path(__file__).with_name('45_experiment_io.py'))
I=importlib.util.module_from_spec(spec);sys.modules[spec.name]=I;spec.loader.exec_module(I)


def require(ok,message):
    if not ok:raise RuntimeError('Completion audit: '+message)


def parse_saved_response(raw,evidence_count):
    from openai.types.responses import Response
    from pickcardu_rag.answering import _generated_answer_model
    adapter=I.load('adapter45_saved_response','35_answer_experiment.py')
    # The immutable cache stores SDK model_dump field names (schema_), not wire aliases.
    return adapter.ResponseCache.parse_raw(Response.model_validate(raw,by_name=True),
        _generated_answer_model(evidence_count)).output_parsed


def run():
    A=I.load('run45_audit','45_run_answers.py');contract,prepared=A.validate()
    P=I.load('packages45_audit','45_prepare_packages.py')
    P.validate_prepared(prepared);P.validate_change_audit(prepared,I.read(I.OUT/'package_change_audit.json'))
    from pickcardu_rag.answering import _restore_generated_answer
    answers=I.read(I.OUT/'answers.json');records=answers['records']
    lookup={(r['query_id'],r['profile']):r for r in records}
    require(len(records)==len(lookup)==96,'incomplete answer matrix')
    require(answers['contract_sha256']==contract['sha256'],'answer contract binding')
    used=[];counts=Counter()
    for p in prepared['records']:
        key=p['query_id'],p['profile'];row=lookup[key]
        require(row==I.read(I.OUT/'answers'/f'{key[0]}_{key[1]}.json'),'answer aggregate changed')
        require(row['input_binding']==I.digest({'contract':contract['sha256'],'package':p}),'answer input binding')
        counts[row['origin']]+=1
        if p['treatment_status']!='changed':
            require(row['origin']=='reused_A' and row['answer']==p['A']['answer_row'].get('answer') and
                    row['status']==p['A']['answer_row']['status'] and row['events']==[],'unchanged answer reuse')
            continue
        require(row['origin']=='changed_B','changed package origin')
        require(1<=len(row['events'])<=contract['max_attempts'],'physical attempts')
        for event in row['events']:
            used.append(event['key']);cache=I.read(I.OUT/'api_cache'/f"{event['key']}.json")
            request=cache['request'];raw=cache['raw_response']
            require(request['model']==contract['model'] and request['reasoning']=={'effort':'medium'} and
                    request['max_output_tokens']==2400 and request['tools']==[],'provider model/budget')
            require(request['input']==[{'role':'user','content':P.payload(p['question'],p['B']['evidence'])}],'provider payload')
            require(cache['usage']==raw['usage']==event['usage'],'physical usage binding')
            if cache['status']=='ok':
                parsed=parse_saved_response(raw,len(p['B']['evidence']))
                require(parsed.model_dump(mode='json')==cache['parsed'],'raw response/parsed binding')
        if row['status']=='ok':
            require(_restore_generated_answer(parsed,p['B']['evidence']).model_dump(mode='json')==row['answer'],'raw response/restored answer')
    files=sorted((I.OUT/'api_cache').glob('*.json'))
    require(len(used)==len(set(used))==len(files)==18,'physical records missing/duplicated')
    require(counts=={'reused_A':78,'changed_B':18},'reuse scope')
    pricing=I.load('price45_audit','36_cost_accounting.py')
    priced=pricing.price_records([('45',str(p.relative_to(I.ROOT)),I.read(p)) for p in files])
    ledger=I.read(I.OUT/'cost_accounting.json')
    require(priced==ledger['records'] and pricing.aggregate(priced)==ledger['summary'],'cost reconciliation')
    E=I.load('evaluation45_audit','45_evaluate_results.py')
    automatic=I.read(I.OUT/'automatic_evaluation.json')
    require(automatic==E.evaluate(),'automatic evaluation changed')
    reviews=I.read(I.OUT/'source_review_summary.json')
    require(reviews==E.summarize_reviews(),'manual aggregation changed')
    report=I.ROOT/'docs/report/45_evidence_supplement_comparison_report.html'
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(report.read_text(),'html.parser')
    require(soup.find_all('h2')[-1].get_text()=='최종 요약','missing final summary')
    require(not re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]',soup.get_text()),'Chinese characters in report')
    links=[a['href'] for a in soup.find_all('a',href=True) if not a['href'].startswith(('http://','https://','#'))]
    require(all((report.parent/unquote(link)).resolve().is_file() for link in links),'broken local report link')
    require(all(t.find_previous('p') for t in soup.find_all('table')),'missing metric guides')
    import nbformat
    notebook=I.ROOT/'notebooks/45_evidence_supplement_comparison.ipynb'
    nb=nbformat.read(notebook,as_version=4);cells=[c for c in nb.cells if c.cell_type=='code']
    require(len(cells)==4 and all(c.execution_count and not any(o.output_type=='error' for o in c.outputs) for c in cells),'notebook not fully executed')
    preservation=I.preserve()
    bound=[Path(__file__),I.ROOT/'notebooks/scripts/45_evaluate_results.py',I.ROOT/'notebooks/scripts/45_render_report.py',report,notebook,
           *[I.OUT/name for name in ['answers.json','automatic_evaluation.json','source_reviews.json','source_review_summary.json','cost_accounting.json']]]
    receipt={'passed':True,'logical_rows':192,'baseline_scores_reproduced':96,'physical_requests':18,
             'answer_status':dict(Counter(r['status'] for r in records)),'answer_origins':dict(counts),
             'raw_responses_reparsed':18,'source_review_pairs':18,'preservation':preservation,
             'notebook_code_cells_executed':4,'html_tables':len(soup.find_all('table')),'local_links':len(links),
             'api_cache_bytes':sum(f.stat().st_size for f in files),'cost_summary':ledger['summary'],
             'artifact_hashes':{str(f.relative_to(I.ROOT)):I.digest(f.read_bytes()) for f in bound},
             'browser_render':'미측정; HTML structure and links checked',
             'scope':'45 only; no new search/embedding/parser/BGE; development paired comparison'}
    I.write(I.OUT/'completion_audit.json',receipt)
    print({k:receipt[k] for k in ['passed','logical_rows','physical_requests','raw_responses_reparsed','source_review_pairs','preservation']})
    return receipt


if __name__=='__main__':run()
