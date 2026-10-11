"""Independent completion evidence: frozen source, complete grid, costs and HTML."""
if not __debug__:raise RuntimeError('Optimized Python is forbidden')
from collections import Counter
from decimal import Decimal
from html.parser import HTMLParser
import importlib.util
from pathlib import Path
import sys
from urllib.parse import unquote,urlparse
s=importlib.util.spec_from_file_location('completion_common',Path(__file__).with_name('37_followup_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)

class Page(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.tables=0;self.guides=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='a':self.links.append(attrs.get('href',''))
        if tag=='table':self.tables+=1
        if attrs.get('class')=='guide':self.guides+=1
    def handle_data(self,data):self.parts.append(data)

def run():
    C.initialize(40);checks={}
    d=C.read(C.STAGES[39]/'dataset.json');scorer=C.read(C.STAGES[39]/'scorer_contract.json')
    protocol=C.read(C.STAGES[40]/'protocol.json');receipt=C.read(C.STAGES[39]/'freeze_receipt.json')
    for value in [d,scorer,protocol]:assert C.digest({k:v for k,v in value.items() if k!='sha256'})==value['sha256']
    assert protocol['dataset_sha256']==receipt['dataset_sha256']==d['sha256']
    assert protocol['scorer_contract_sha256']==receipt['scorer_contract_sha256']==scorer['sha256']
    assert receipt['stage40_api_records_at_freeze']==0
    for path,sha in protocol['source_code'].items():assert C.digest((C.ROOT/path).read_bytes())==sha
    assert scorer['source_builder_sha256']==C.digest((C.ROOT/'notebooks/39_freeze_new_questions.py').read_bytes())
    qs={q['query_id']:q for q in d['queries']};profiles=protocol['profiles'];modes=protocol['modes']
    expected={(q,p,m) for q in qs for p in profiles for m in modes}
    retrieval=C.read(C.STAGES[40]/'retrieval_results.json')['records'];answers=C.read(C.STAGES[40]/'answer_results.json')['records']
    assert len(expected)==len(retrieval)==len(answers)==108
    assert {(r['query_id'],r['profile'],r['mode']) for r in retrieval}==expected
    assert {(r['query_id'],r['profile'],r['mode']) for r in answers}==expected
    rb={(r['query_id'],r['profile'],r['mode']):r for r in retrieval}
    M=C.load('completion_rubric','39_answer_rubric.py')
    calls=[];failures=[]
    for a in answers:
        r=rb[(a['query_id'],a['profile'],a['mode'])];q=qs[a['query_id']]
        assert r['protocol_sha256']==a['protocol_sha256']==protocol['sha256']
        assert a['retrieval_sha256']==C.digest(r)
        if a['status']=='ok':assert M.score(q,a['answer'],r['result']['evidence'])==a['scores']
        else:
            failures.append({'query_id':a['query_id'],'profile':a['profile'],'mode':a['mode'],'error':a['error']})
            if q['gold_cards']:assert a['scores']['known_positive_complete']==0
        assert len(a['events'])<=2
        for event in a['events']:
            record=C.read(C.STAGES[40]/'api_cache'/(event['key']+'.json'))
            assert record['request']['max_output_tokens']==2400
            calls.append({'query_id':a['query_id'],'profile':a['profile'],'mode':a['mode'],'request_key':event['key'],
                          'cache_hit':event['cache_hit'],'physical_record_sha256':C.digest(record)})
    parsers=C.read(C.STAGES[40]/'parser_results.json')['records'];assert len(parsers)==18
    assert {r['query_id'] for r in parsers}==set(qs)
    physical=list((C.STAGES[40]/'api_cache').glob('*.json'));assert len(physical)<=235
    prices=C.read(C.STAGES[40]/'cost_accounting.json');by={r['path']:r for r in prices['records']}
    assert len(physical)==len(by)
    total=Decimal(0);unknown=[];response_ids=set();first=None
    for path in physical:
        record=C.read(path);row=by[str(path.relative_to(C.ROOT))]
        assert row['record_sha256']==C.digest(record)
        request=record['request'];usage=record.get('usage') or {};raw=record.get('raw_response') or {}
        if record.get('started_at') is not None:first=min(first or record['started_at'],record['started_at'])
        if request['model']=='text-embedding-3-small':
            assert raw and record['raw_response_sha256']==C.digest(raw)
            assert record['usage']==raw['usage'];price=Decimal(usage['prompt_tokens'])*Decimal('.02')/1000000
        elif usage:
            assert request['model']=='gpt-6-luna' and request['reasoning']=={'effort':'medium'}
            assert raw['service_tier']=='default' and raw['model']=='gpt-6-luna'
            assert usage['input_tokens']<272000
            details=usage['input_tokens_details'];cached=details['cached_tokens'];written=details['cache_write_tokens']
            ordinary=usage['input_tokens']-cached-written;assert ordinary>=0
            assert usage['total_tokens']==usage['input_tokens']+usage['output_tokens']
            price=(Decimal(ordinary)*Decimal('.10')+Decimal(cached)*Decimal('.01')+Decimal(written)*Decimal('.125')+Decimal(usage['output_tokens'])*Decimal('.50'))/1000000
            assert raw['id'] not in response_ids;response_ids.add(raw['id'])
        else:unknown.append(str(path.relative_to(C.ROOT)));assert row['cost_usd'] is None;continue
        assert Decimal(row['cost_usd'])==price;total+=price
    assert receipt['frozen_at']<first
    assert Decimal(prices['summary']['known_cost_usd'])==total
    assert prices['summary']['unknown_cost_records']==len(unknown)
    assert total<=1 and prices['summary']['billed_cost_usd'] is None
    recovery=C.read(C.STAGES[40]/'memory_recovery_contract.json')
    assert C.digest({k:v for k,v in recovery.items() if k!='sha256'})==recovery['sha256']
    assert recovery['source_protocol_sha256']==protocol['sha256']
    assert recovery['runner_sha256']==C.digest((C.ROOT/'notebooks/40_new_query_comparison.py').read_bytes())
    assert recovery['adapter_sha256']==C.digest((C.ROOT/'notebooks/40_memory_recovery.py').read_bytes())
    assert recovery['parity_sha256']==C.digest((C.STAGES[40]/'memory_parity.json').read_bytes())
    for path,sha in recovery['before_manifest'].items():assert C.digest((C.ROOT/path).read_bytes())==sha,path
    parity=C.read(C.STAGES[40]/'memory_parity.json')
    assert parity['passed'] and parity['new_api_requests']==0 and len(parity['records'])==5
    assert parity['adapter_source_sha256']==recovery['adapter_sha256']
    assert parity['source_protocol_sha256']==protocol['sha256']
    assert max(r['document_tokens'] for r in parity['records'])>=3000
    assert all(r['absolute_difference']<=parity['tolerance'] and r['within_tolerance'] and r['trace']['truncated_count']==0 for r in parity['records'])
    for row in parity['records']:
        baseline=C.ROOT/row['baseline_cache_path']
        assert C.digest(baseline.read_bytes())==row['baseline_cache_sha256']
        assert C.read(baseline)['score']==row['cached_score']
    recovery_receipt=C.read(C.STAGES[40]/'memory_recovery.json')
    telemetry=C.read(C.STAGES[40]/'memory_telemetry.json')
    assert recovery_receipt['status']=='complete' and recovery_receipt['recovery_contract_sha256']==recovery['sha256']
    assert telemetry['recovery_contract_sha256']==recovery['sha256']
    assert telemetry['max_document_tokens']<=4096 and telemetry['max_pair_tokens']<=8192
    assert telemetry['completed_singleton_calls']>0 and telemetry['peak_rss_kib']>0
    filenames={f'{q}_{p}_{m}.json' for q,p,m in expected}
    assert set(recovery['completed_keys']).isdisjoint(recovery['pending_keys'])
    assert set(recovery['completed_keys'])|set(recovery['pending_keys'])==filenames
    assert recovery['completed_rows']==len(recovery['completed_keys'])==22
    assert recovery['api_records']==41 and recovery['old_batch_size']==2 and recovery['new_max_documents_per_call']==1
    C.write(C.STAGES[40]/'answer_request_mapping.json',{'logical_answer_rows':108,'attempt_events':len(calls),
        'unique_answer_request_keys':len({r['request_key'] for r in calls}),'records':calls})
    assert C.read(C.STAGES[37]/'guard_results.json')['new_api_requests']==0
    assert C.read(C.STAGES[38]/'condition_results.json')['new_api_requests']==0
    for n,name in [(37,'operational_guard_audit'),(38,'query_condition_audit'),(39,'answer_rubric'),(40,'new_query_comparison')]:
        import nbformat
        path=C.ROOT/f'notebooks/{n}_{name}.ipynb';nb=nbformat.read(path,as_version=4)
        cells=[c for c in nb.cells if c.cell_type=='code'];assert len(cells)==3 and [c.execution_count for c in cells]==[1,2,3]
        assert all(not any(o.output_type=='error' for o in c.outputs) for c in cells)
        note=C.read(C.STAGES[n]/'notebook_execution.json');assert note['notebook_sha256']==C.digest(path.read_bytes())
        report=C.ROOT/f'docs/report/{n}_{name}_report.html';source=report.read_text();page=Page();page.feed(source)
        assert page.tables and page.guides>=page.tables
        for link in page.links:
            parsed=urlparse(link)
            if parsed.scheme or link.startswith('#'):continue
            assert (report.parent/unquote(parsed.path)).is_file(),link
        import re
        assert not re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]',source)
        checks[str(n)]={'code_cells':3,'notebook_sha256':note['notebook_sha256'],'html_sha256':C.digest(report.read_bytes()),
                       'tables':page.tables,'metric_guides':page.guides,'links_checked':len(page.links)}
    eval_summary=C.read(C.STAGES[40]/'evaluation_summary.json')
    assert len(eval_summary['metrics'])==24 and len(eval_summary['paired'])==4
    assert all(len(r['wins'])+len(r['losses'])+len(r['ties'])==15 for r in eval_summary['paired'])
    assert not eval_summary['production_adopted'] and not eval_summary['independent_holdout'] and eval_summary['repeated_runs']==0
    result={'passed':True,'grid_rows':108,'positive_questions':15,'retrieval_status_counts':dict(Counter(r['status'] for r in retrieval)),
       'answer_status_counts':dict(Counter(a['status'] for a in answers)),'known_cost_usd':str(total),'unknown_usage_records':unknown,
       'physical_requests':len(physical),'all_notebook_code_cells':12,'reports':checks,'failures':failures,
       'preservation':C.verify(),'browser_rendering':'미검증; HTML 내용·표·지표 안내·링크 검사만 수행',
       'new_diagnostics_do_not_modify_operational_sources':True,
       'recovery':{'original_rows_preserved':22,'resumed_rows':86,'snapshot_files':len(recovery['before_manifest']),
           'long_parity_pairs':len(parity['records']),'single_document_calls':telemetry['completed_singleton_calls'],
           'peak_rss_kib':telemetry['peak_rss_kib'],'batch_size_change_disclosed':True}}
    C.write(C.STAGES[40]/'completion_audit.json',result);print('Completion evidence',108,'rows',len(physical),'requests',str(total),'USD')
    return result
def validate_partial_grid(summary,binding,protocol,dataset,retrieval,answers):
    expected={f"{q['query_id']}_{p}_{m}.json" for q in dataset['queries'] for p in protocol['profiles'] for m in protocol['modes']}
    assert not summary['execution_complete'] and summary['expected']==len(expected)
    assert set(binding['completed_keys'])<=set(answers)<=set(retrieval)<=expected
    assert summary['completed_answers']==len(answers) and summary['completed_retrievals']==len(retrieval)
    assert summary['pending_answers']==len(expected)-len(answers)>0
    for row in retrieval.values():assert row['status']=='ok' and row['protocol_sha256']==protocol['sha256']
    for key,answer in answers.items():
        assert answer['status']=='ok' and answer['protocol_sha256']==protocol['sha256']
        assert answer['retrieval_sha256']==C.digest(retrieval[key])
    return len(expected)

def verify_partial():
    """Audit preserved incomplete state; never treat it as full-goal completion."""
    C.initialize(40);directory=C.STAGES[40];summary=C.read(directory/'partial_summary.json')
    assert not summary['execution_complete'] and summary['expected']==108
    binding=C.read(directory/'memory_recovery_contract.json');receipt=C.read(directory/'memory_recovery.json')
    assert C.digest({k:v for k,v in binding.items() if k!='sha256'})==binding['sha256']
    assert receipt['status']=='interrupted' and receipt['error'].startswith('MemoryStop:')
    assert C.digest((C.ROOT/'notebooks/40_memory_recovery.py').read_bytes())==binding['adapter_sha256']
    protocol=C.read(directory/'protocol.json')
    assert C.digest({k:v for k,v in protocol.items() if k!='sha256'})==binding['source_protocol_sha256']==protocol['sha256']
    assert C.digest((C.ROOT/'notebooks/40_new_query_comparison.py').read_bytes())==binding['runner_sha256']
    for path,sha in protocol['source_code'].items():assert C.digest((C.ROOT/path).read_bytes())==sha,path
    actual_r={p.name:C.read(p) for p in (directory/'retrieval').glob('*.json')}
    actual_a={p.name:C.read(p) for p in (directory/'answers').glob('*.json')}
    assert validate_partial_grid(summary,binding,protocol,C.read(C.STAGES[39]/'dataset.json'),actual_r,actual_a)==108
    for path,sha in binding['before_manifest'].items():assert C.digest((C.ROOT/path).read_bytes())==sha,path
    records=[C.read(p) for p in (directory/'api_cache').glob('*.json')]
    assert len(records)==summary['physical_requests']>=binding['api_records']
    assert all(r['status']=='ok' for r in records)
    total=Decimal(0)
    for record in records:
        u=record['usage']
        if record['request']['model']=='text-embedding-3-small':total+=Decimal(u['prompt_tokens'])*Decimal('.02')/1000000
        else:
            d=u['input_tokens_details'];read=d['cached_tokens'];written=d['cache_write_tokens']
            ordinary=u['input_tokens']-read-written;assert ordinary>=0
            total+=(Decimal(ordinary)*Decimal('.10')+Decimal(read)*Decimal('.01')+Decimal(written)*Decimal('.125')+Decimal(u['output_tokens'])*Decimal('.50'))/1000000
    assert total==Decimal(summary['known_cost_usd']) and summary['unknown_cost_records']==0 and summary['billed_cost_usd'] is None
    reports={}
    for n,name in [(37,'operational_guard_audit'),(38,'query_condition_audit'),(39,'answer_rubric'),(40,'new_query_comparison')]:
        import nbformat,re
        nbpath=C.ROOT/f'notebooks/{n}_{name}.ipynb';nb=nbformat.read(nbpath,as_version=4)
        cells=[c for c in nb.cells if c.cell_type=='code'];assert [c.execution_count for c in cells]==[1,2,3]
        assert all(not any(o.output_type=='error' for o in c.outputs) for c in cells)
        assert C.read(C.STAGES[n]/'notebook_execution.json')['notebook_sha256']==C.digest(nbpath.read_bytes())
        report=C.ROOT/f'docs/report/{n}_{name}_report.html';source=report.read_text();page=Page();page.feed(source)
        assert page.tables and page.guides>=page.tables and not re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]',source)
        for link in page.links:
            parsed=urlparse(link)
            if parsed.scheme or link.startswith('#'):continue
            assert (report.parent/unquote(parsed.path)).is_file(),link
        if n==40:assert '미완료' in source and str(summary['pending_answers']) in source and '3GiB' in source
        reports[str(n)]={'tables':page.tables,'guides':page.guides,'notebook_cells':3,'html_sha256':C.digest(report.read_bytes())}
    preservation=C.verify()
    result={'partial_state_verified':True,'full_goal_complete':False,'retrieval_rows':len(actual_r),'answer_rows':len(actual_a),'pending_rows':summary['pending_answers'],
      'physical_requests':len(records),'new_requests_since_first_recovery':len(records)-binding['api_records'],
      'new_requests_on_latest_attempt':len(records)-receipt['physical_requests_before_resume'],'known_cost_usd':str(total),
      'reports':reports,'preservation':preservation,'browser_rendering':'미검증'}
    C.write(directory/'partial_audit.json',result);print('Preserved partial state',len(actual_a),'of108',len(records),'requests',str(total),'USD')
    return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--partial',action='store_true');args=parser.parse_args()
    verify_partial() if args.partial else run()
