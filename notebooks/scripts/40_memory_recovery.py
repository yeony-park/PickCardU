"""Resume original frozen run with one whole document per original scorer call."""
if not __debug__:raise RuntimeError('Optimized Python is forbidden')
import gc
import importlib.util
from pathlib import Path
import sys
import time
import resource

def process_rss_kib():
    return int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:')))

class MemoryStop(BaseException):
    """Stop safely before another model allocation under host memory pressure."""

def required_headroom_kib(resident,observed_peak):
    return max(1048576,max(0,observed_peak-resident)+524288)

def check_memory_headroom(available,resident,observed_peak):
    required=required_headroom_kib(resident,observed_peak)
    if available<required:raise MemoryStop(f'Host available memory {available}KiB below measured-headroom reserve {required}KiB')
    return required

def validate_parity_baselines(C,result):
    for row in result['records']:
        path=C.ROOT/row['baseline_cache_path']
        if C.digest(path.read_bytes())!=row['baseline_cache_sha256'] or C.read(path)['score']!=row['cached_score']:
            raise RuntimeError('Parity baseline changed: '+row['baseline_cache_path'])

class OneDocumentReranker:
    def __init__(self,delegate,monitor=None):self.delegate=delegate;self.monitor=monitor
    def score(self,mode,question,documents,**kwargs):
        scores=[];traces=[];started=time.perf_counter()
        for document in documents:
            if self.monitor:self.monitor(mode,question,document,'before',None)
            values,trace=self.delegate.score(mode,question,[document],**kwargs)
            if len(values)!=1:raise RuntimeError('Original singleton scorer result count mismatch')
            scores.extend(values);traces.append(trace)
            if self.monitor:self.monitor(mode,question,document,'after',trace)
            gc.collect()
        info={**(traces[-1] if traces else {}),'batch_size':1,'actual_max_documents_per_call':1,
              'batch_count':sum(t.get('batch_count',0) for t in traces),
              'input_token_count':sum(t.get('input_token_count',0) for t in traces),
              'truncated_count':sum(t.get('truncated_count',0) for t in traces),
              'wall_seconds':time.perf_counter()-started,'calls':len(traces)}
        return scores,info

def tools():
    path=Path(__file__).with_name('40_new_query_comparison.py')
    spec=importlib.util.spec_from_file_location('unchanged_frozen_runner',path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module

def bind_recovery_contract(C,path,value):
    """Keep the first recovery boundary immutable through further interruptions."""
    if not path.exists():return C.freeze(path,value)
    original=C.read(path)
    for key in ['source_protocol_sha256','runner_sha256','adapter_sha256','parity_sha256']:
        if original[key]!=value[key]:raise RuntimeError('Recovery binding changed: '+key)
    if C.digest({k:v for k,v in original.items() if k!='sha256'})!=original['sha256']:
        raise RuntimeError('Recovery contract digest mismatch')
    for filename,sha in original['before_manifest'].items():
        if C.digest((C.ROOT/filename).read_bytes())!=sha:raise RuntimeError('Recovery snapshot changed: '+filename)
    return original

def parity():
    M=tools();C=M.C;C.initialize(40);M.validate_freeze()
    B,x=C.experiment(40)
    samples=[]
    def capture(mode,question,documents,**kwargs):
        values=[]
        for document in documents:
            key=C.digest({'mode':mode,'q':question,'doc':document,'kwargs':kwargs,'contract':x.scorer.contract})
            paths=[C.STAGES[40]/'bge_cache'/(key+'.json'),C.F.STAGES[34]/'bge_cache'/(key+'.json'),C.F.OLD[32]/'bge_cache'/(key+'.json')]
            found=next((p for p in paths if p.exists()),None)
            if found is None:raise RuntimeError('No saved baseline score; parity selection cannot compute one')
            value=C.read(found)['score'];values.append(value)
            length=x.guard.lengths(question,document)
            samples.append({'mode':mode,'question':question,'document':document,'kwargs':kwargs,'cached_score':value,'length':length,
                            'baseline_cache_path':str(found.relative_to(C.ROOT)),'baseline_cache_sha256':C.digest(found.read_bytes())})
        return values,{'new_pairs':0,'cache_hits':len(values)}
    x.scorer.score=capture
    dataset=C.read(C.STAGES[39]/'dataset.json');q=dataset['queries'][0]
    embedding=M.Embeddings().embeddings([q['question']])[0]
    C.component(B,x,40,q['question'],embedding,'card_page_section_benefit',None)
    x.search(q['question'],embedding,'card_page_section_benefit',None,'budget5')
    short=[r for r in samples if r['length']['document_tokens']<=1800][:3]
    samples.clear()
    for q in dataset['queries'][:4]:
        embedding=M.Embeddings().embeddings([q['question']])[0]
        C.component(B,x,40,q['question'],embedding,'parent_child_bundle',None)
        x.search(q['question'],embedding,'parent_child_bundle',None,'budget5')
    old_dataset=C.read(C.F.OLD[32]/'dataset_freeze.json')
    old_components=C.read(C.F.STAGES[34]/'component_freeze.json');x.components.update(old_components['components'])
    for q in old_dataset['queries']:
        if q['query_id'] not in ['Q05','Q28']:continue
        embedding=B.ReadOnlyEmbeddingAPI().embeddings([q['question']])[0]
        x.search(q['question'],embedding,'parent_child_bundle',None,'budget5')
    long=sorted(samples,key=lambda r:-r['length']['document_tokens'])[:2]
    print('Cached parity sample lengths',len(short),[r['length']['document_tokens'] for r in long],flush=True)
    if len(short)<3 or not long or long[0]['length']['document_tokens']<3000:
        raise RuntimeError('Short and near-document-limit cached parity samples unavailable')
    samples=short+long
    adapter=OneDocumentReranker(x.scorer.delegate);rows=[]
    for sample in samples:
        values,trace=adapter.score(sample['mode'],sample['question'],[sample['document']],**sample['kwargs'])
        diff=abs(values[0]-sample['cached_score'])
        rows.append({'question_sha256':C.digest(sample['question']),'document_sha256':C.digest(sample['document']),
                     'cached_score':sample['cached_score'],'singleton_score':values[0],'absolute_difference':diff,
                     'guard':sample['kwargs'],'trace':trace,'within_tolerance':diff<=1e-4,
                     'baseline_cache_path':sample['baseline_cache_path'],'baseline_cache_sha256':sample['baseline_cache_sha256'],
                     **sample['length'],'rss_kib_after':process_rss_kib(),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
    result={'passed':all(r['within_tolerance'] for r in rows),'tolerance':1e-4,'records':rows,'new_api_requests':0,
        'source_protocol_sha256':C.read(C.STAGES[40]/'protocol.json')['sha256'],
        'adapter_source_sha256':C.digest(Path(__file__).read_bytes()),
        'limits':'Three short plus two long cached pairs; not proof of identical full-corpus ranking or future host OOM safety; batch2 and singleton scores can differ by float32 rounding.'}
    C.write(C.STAGES[40]/'memory_parity.json',result)
    if not result['passed']:raise RuntimeError('Singleton parity failed')
    print('Singleton parity',len(rows),'pairs',max(r['absolute_difference'] for r in rows),flush=True)
    return result

def resume():
    M=tools();C=M.C;C.initialize(40)
    parity_result=C.read(C.STAGES[40]/'memory_parity.json')
    protocol=C.read(C.STAGES[40]/'protocol.json')
    if not parity_result['passed'] or parity_result['source_protocol_sha256']!=protocol['sha256']:
        raise RuntimeError('Recovery parity not bound to current frozen protocol')
    if parity_result['adapter_source_sha256']!=C.digest(Path(__file__).read_bytes()):raise RuntimeError('Adapter changed after parity')
    validate_parity_baselines(C,parity_result)
    M.validate_freeze();C.verify()
    before_manifest={}
    for directory in ['retrieval','answers','plans','api_cache','components','bge_cache']:
        for p in (C.STAGES[40]/directory).rglob('*'):
            if p.is_file():before_manifest[str(p.relative_to(C.ROOT))]=C.digest(p.read_bytes())
    rs={p.name:C.read(p) for p in (C.STAGES[40]/'retrieval').glob('*.json')}
    ans={p.name:C.read(p) for p in (C.STAGES[40]/'answers').glob('*.json')}
    if not set(ans)<=set(rs):raise RuntimeError('Answer lacks completed retrieval')
    for key,r in rs.items():
        if r['protocol_sha256']!=protocol['sha256'] or (key in ans and (ans[key]['protocol_sha256']!=protocol['sha256'] or ans[key]['retrieval_sha256']!=C.digest(r))):
            raise RuntimeError('Invalid completed row binding')
    plans=list((C.STAGES[40]/'plans').glob('*.json'))
    if len(plans)!=18 or any(C.read(p)['status']!='ok' or C.read(p)['protocol_sha256']!=protocol['sha256'] for p in plans):raise RuntimeError('Parser cache preflight failed')
    api_files=list((C.STAGES[40]/'api_cache').glob('*.json'))
    if any(C.read(p)['status']!='ok' for p in api_files):raise RuntimeError('Unfinished API record; automatic re-call forbidden')
    dataset=C.read(C.STAGES[39]/'dataset.json');import numpy as np
    for q in dataset['queries']:
        key=C.digest({'text':q['question'],'model':'text-embedding-3-small','dimension':1536})
        p=C.STAGES[40]/'api_cache/embeddings'/(key+'.npy')
        vector=np.load(p,allow_pickle=False)
        if vector.shape!=(1536,) or not np.isfinite(vector).all():raise RuntimeError('Frozen embedding cache invalid')
    expected={f"{q['query_id']}_{p}_{m}.json" for q in dataset['queries'] for p in protocol['profiles'] for m in protocol['modes']}
    if not set(rs)<=expected:raise RuntimeError('Unexpected completed row')
    recovery=bind_recovery_contract(C,C.STAGES[40]/'memory_recovery_contract.json',{'source_protocol_sha256':protocol['sha256'],
        'runner_sha256':C.digest((C.ROOT/'notebooks/scripts/40_new_query_comparison.py').read_bytes()),
        'adapter_sha256':C.digest(Path(__file__).read_bytes()),'parity_sha256':C.digest((C.STAGES[40]/'memory_parity.json').read_bytes()),
        'completed_keys':sorted(ans),'pending_keys':sorted(expected-set(ans)),'before_manifest':before_manifest,
        'completed_rows':len(ans),'api_records':len(api_files),'old_batch_size':2,'new_max_documents_per_call':1})
    before_manifest={**before_manifest,**recovery['before_manifest']}
    telemetry={'max_document_tokens':0,'max_pair_tokens':0,'peak_rss_kib':0,'completed_singleton_calls':0,
               'minimum_host_available_kib':None,'minimum_reserve_kib':1048576,'extra_growth_margin_kib':524288,
               'parity_observed_peak_rss_kib':max(r['peak_rss_kib'] for r in parity_result['records']),
               'recovery_contract_sha256':recovery['sha256']}
    def memory_gate(phase):
        available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
        resident=process_rss_kib();peak=max(telemetry['parity_observed_peak_rss_kib'],resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        telemetry.update(last_phase=phase,last_resident_kib=resident,last_required_headroom_kib=required_headroom_kib(resident,peak),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        telemetry['minimum_host_available_kib']=min(telemetry['minimum_host_available_kib'] or available,available)
        C.write(C.STAGES[40]/'memory_telemetry.json',telemetry)
        if phase!='after':check_memory_headroom(available,resident,peak)
    original_budget_check=M.check_budget
    def guarded_budget(*args,**kwargs):
        original_budget_check(*args,**kwargs);memory_gate('before_new_api')
    M.check_budget=guarded_budget
    original_factory=C.experiment
    def private_factory(n):
        B,x=original_factory(n)
        def monitor(mode,question,document,phase,trace):
            length=x.guard.lengths(question,document)
            telemetry['max_document_tokens']=max(telemetry['max_document_tokens'],length['document_tokens'])
            telemetry['max_pair_tokens']=max(telemetry['max_pair_tokens'],length['pair_tokens'])
            if phase=='after':telemetry['completed_singleton_calls']+=1
            memory_gate(phase)
        x.scorer.delegate=OneDocumentReranker(x.scorer.delegate,monitor=monitor)
        return B,x
    C.experiment=private_factory
    start=time.time();before=len(list((C.STAGES[40]/'api_cache').glob('*.json')))
    C.write(C.STAGES[40]/'memory_recovery.json',{'status':'running','started_at':start,'completed_rows_before_resume':len(rs),
      'recovery_contract_sha256':recovery['sha256'],
      'physical_requests_before_resume':before,'old_batch_size':2,'new_max_documents_per_call':1,
      'reason':'Kernel global OOM killed PID150555 with exit137; no token-limit failure',
      'changed':'Only number of whole documents submitted per original reranker call; weights,CPU float32,token limits,chunks,scope,LLM prompt and generation unchanged',
      'source_protocol_sha256':C.read(C.STAGES[40]/'protocol.json')['sha256']})
    try:result=M.run()
    except BaseException as e:
        receipt=C.read(C.STAGES[40]/'memory_recovery.json');receipt.update(status='interrupted',error=f'{type(e).__name__}: {e}')
        C.write(C.STAGES[40]/'memory_recovery.json',receipt);raise
    for path,sha in before_manifest.items():
        if C.digest((C.ROOT/path).read_bytes())!=sha:raise RuntimeError('Completed cache or result was modified: '+path)
    if {p.name for p in (C.STAGES[40]/'answers').glob('*.json')}!=expected:raise RuntimeError('Pending matrix incomplete')
    receipt=C.read(C.STAGES[40]/'memory_recovery.json');receipt.update(status='complete',finished_at=time.time(),resume_wall_seconds=time.time()-start,
        physical_requests_after_resume=len(list((C.STAGES[40]/'api_cache').glob('*.json'))))
    C.write(C.STAGES[40]/'memory_recovery.json',receipt)
    return result
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--parity',action='store_true');args=p.parse_args()
    parity() if args.parity else resume()
