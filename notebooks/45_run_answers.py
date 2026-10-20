"""Sequential changed-B answers only; provider access is explicitly bounded."""
import fcntl
import importlib.util
from pathlib import Path
import resource
import sys
import time

spec=importlib.util.spec_from_file_location('io45_run',Path(__file__).with_name('45_experiment_io.py'))
I=importlib.util.module_from_spec(spec);sys.modules[spec.name]=I;spec.loader.exec_module(I)


def seal():
    prepared=I.read(I.OUT/'prepared_packages.json')
    P=I.load('validate45_seal','45_prepare_packages.py')
    P.validate_prepared(prepared);P.validate_change_audit(prepared,I.read(I.OUT/'package_change_audit.json'))
    for name,sha in prepared['source_hashes'].items():
        if I.digest((I.ROOT/name).read_bytes())!=sha:raise RuntimeError('Input changed: '+name)
    names=['45_evidence_supplement.py','45_experiment_io.py','45_prepare_packages.py','45_run_answers.py',
           '35_answer_experiment.py','34_followup_common.py','36_cost_accounting.py','39_answer_rubric.py','30_build_structured_db.py']
    code={str((I.ROOT/'notebooks'/name).relative_to(I.ROOT)):I.digest((I.ROOT/'notebooks'/name).read_bytes()) for name in names}
    answer_source=I.ROOT/'packages/rag-core/src/pickcardu_rag/answering.py'
    code[str(answer_source.relative_to(I.ROOT))]=I.digest(answer_source.read_bytes())
    return I.freeze('execution_contract.json',{'packages_sha256':I.digest((I.OUT/'prepared_packages.json').read_bytes()),
        'review_contract_sha256':I.digest((I.OUT/'review_contract.json').read_bytes()),
        'paired_review_protocol_sha256':I.digest((I.OUT/'paired_review_protocol.json').read_bytes()),'sources':code,
        'package_change_audit_sha256':I.digest((I.OUT/'package_change_audit.json').read_bytes()),
        'preservation_contract_sha256':I.digest((I.OUT/'preservation_contract.json').read_bytes()),
        'model':'gpt-6-luna','reasoning':'medium','output_tokens':2400,'max_attempts':2,
        'new_api_scope':'changed B answer payload only','new_parser':0,'new_embedding':0,'new_search':0,
        'new_bge':0,'request_cap':2*sum(r['treatment_status']=='changed' for r in prepared['records']),
        'selection_rule':'per-row no regressions in core/error/diagnostic/success, improvement in answer required; ties keep A'})


def validate():
    c=I.read(I.OUT/'execution_contract.json')
    if c['sha256']!=I.digest({k:v for k,v in c.items() if k!='sha256'}):raise RuntimeError('Execution receipt mismatch')
    for name,sha in c['sources'].items():
        if I.digest((I.ROOT/name).read_bytes())!=sha:raise RuntimeError('Frozen code changed: '+name)
    if I.digest((I.OUT/'prepared_packages.json').read_bytes())!=c['packages_sha256']:raise RuntimeError('Packages changed')
    if I.digest((I.OUT/'review_contract.json').read_bytes())!=c['review_contract_sha256']:raise RuntimeError('Review criteria changed')
    if I.digest((I.OUT/'paired_review_protocol.json').read_bytes())!=c['paired_review_protocol_sha256']:raise RuntimeError('Paired review criteria changed')
    if I.digest((I.OUT/'package_change_audit.json').read_bytes())!=c['package_change_audit_sha256']:raise RuntimeError('Evidence replacement audit changed')
    if I.digest((I.OUT/'preservation_contract.json').read_bytes())!=c['preservation_contract_sha256']:raise RuntimeError('Preservation contract changed')
    packages=I.read(I.OUT/'prepared_packages.json')
    for name,sha in packages['source_hashes'].items():
        if I.digest((I.ROOT/name).read_bytes())!=sha:raise RuntimeError('Frozen original changed: '+name)
    return c,packages


def cost():
    P=I.load('price45','36_cost_accounting.py')
    rows=P.price_records([('45',str(p.relative_to(I.ROOT)),I.read(p)) for p in sorted((I.OUT/'api_cache').glob('*.json'))])
    result={'summary':P.aggregate(rows),'records':rows,'rates':{**P.RATES,'verified_date':'2026-10-20'},
            'sources':['https://developers.openai.com/api/docs/models/gpt-6-luna','https://developers.openai.com/api/docs/pricing'],
            'billing_scope':'new 45 physical requests only; official-rate usage calculation, not invoice; cache hits cost zero new API usage'}
    I.write(I.OUT/'cost_accounting.json',result)
    return result


def run():
    started=time.perf_counter();cpu=time.process_time()
    contract,prepared=validate()
    lock_path=I.OUT/'sequential.lock'
    with lock_path.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        preservation=I.preserve()
        P=I.load('validate45_run','45_prepare_packages.py')
        P.validate_prepared(prepared);P.validate_change_audit(prepared,I.read(I.OUT/'package_change_audit.json'))
        A=I.load('answer_adapter45','35_answer_experiment.py')
        A.F.STAGES[45]=I.OUT;A.F.initialize(45)
        from pickcardu_rag.answering import OpenAIService,serialize_answer_payload
        allowed={serialize_answer_payload(r['question'],r['B']['evidence']) for r in prepared['records'] if r['treatment_status']=='changed'}
        class Cache(A.ResponseCache):
            def fetch(self,kwargs):
                validate()
                if kwargs['model']!='gpt-6-luna' or kwargs['input']!=[{'role':'user','content':kwargs['input'][0]['content']}] or kwargs['input'][0]['content'] not in allowed:
                    raise RuntimeError('Only frozen changed-B answer requests allowed')
                if kwargs['max_output_tokens']!=2400 or kwargs.get('tools')!=[]:raise RuntimeError('Provider contract changed')
                # parse() has already journaled this pending request before fetch.
                files=list((I.OUT/'api_cache').glob('*.json'))
                if len(files)>contract['request_cap']:raise RuntimeError('Physical request cap exceeded')
                return super().fetch(kwargs)
        output=[]
        for package in prepared['records']:
            path=I.OUT/'answers'/f"{package['query_id']}_{package['profile']}.json"
            binding=I.digest({'contract':contract['sha256'],'package':package})
            if path.exists():
                row=I.read(path)
                if row['input_binding']!=binding:raise RuntimeError('Answer cache binding mismatch')
            elif package['A']['payload_sha256']==package['B']['payload_sha256']:
                row={'query_id':package['query_id'],'profile':package['profile'],'input_binding':binding,
                     'origin':'reused_A','status':package['A']['answer_row']['status'],
                     'answer':package['A']['answer_row'].get('answer'),'events':[],'wall_seconds':0}
            else:
                api=Cache(cache=I.OUT/'api_cache',old_cache=I.OUT/'execution_cache/disabled_prior_cache',
                          policy='2400',allow_network=True,revalidate_failed=False)
                provider=OpenAIService(api_key=None,client=api,llm_model='gpt-6-luna',answer_payload_bytes=64000)
                before=time.perf_counter()
                try:
                    answer,usage=provider.answer(package['question'],package['B']['evidence'])
                    row={'status':'ok','answer':answer.model_dump(mode='json'),'usage':usage}
                except Exception as error:
                    row={'status':'failed','answer':None,'error':f'{type(error).__name__}: {error}'}
                row.update(query_id=package['query_id'],profile=package['profile'],input_binding=binding,
                           origin='changed_B',events=api.events,wall_seconds=time.perf_counter()-before)
            row['preservation_preflight_sha256']=I.digest(preservation)
            I.write(path,row);output.append(row)
            if row['origin']!='reused_A':print(row['query_id'],row['profile'],row['status'],flush=True)
            I.write(I.OUT/'answer_progress.json',{'completed':len(output),'total':96,'changed_completed':sum(r['origin']=='changed_B' for r in output)})
        I.write(I.OUT/'answers.json',{'records':output,'contract_sha256':contract['sha256']})
        ledger=cost()
        I.write(I.OUT/'answer_resources.json',{'wall_seconds_this_process':time.perf_counter()-started,
            'cpu_seconds_this_process':time.process_time()-cpu,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'new_api_requests':ledger['summary']['record_count'],'new_embeddings':0,'new_parser_calls':0,'new_bge_calls':0,
            'gpu':'사용 안함','source':'single sequential answer process; preparation and Codex review excluded'})
        return {'answer_rows':len(output),'status':dict(__import__('collections').Counter(r['status'] for r in output)),
                'new_cost':ledger['summary']}


if __name__=='__main__':
    if '--seal' in sys.argv:print(seal()['sha256'])
    else:print(run())
