"""Preserved operating U search with experiment-only paths for68/69."""
import copy
import importlib.util
from pathlib import Path
import sys
import time

spec=importlib.util.spec_from_file_location('answers68_retrieval',Path(__file__).with_name('68_69_answer_runtime.py'))
A=importlib.util.module_from_spec(spec);sys.modules[spec.name]=A;spec.loader.exec_module(A)
T=A.T


def inventory():
    result=copy.deepcopy(T.R.source_inventory())
    for stage in (61,64,65):
        folder=T.R.P.DATA/T.R.P.STAGES[stage]
        for sub in ('bge_cache','score_contracts'):
            path=folder/sub
            members=sorted(str(p.relative_to(T.ROOT))for p in path.glob('*.json'))
            result['directory_members'][str(path.relative_to(T.ROOT))]=members
            for name in members:result['files_sha256'][name]=T.E.file_digest(T.ROOT/name)
    return result


class Runtime(T.R.Runtime):
    def __init__(self,stage):
        super().__init__(stage)
        for previous in (64,65):
            directory=T.R.P.F.STAGES[previous]/'bge_cache'
            if directory not in T.R.M.CACHE_SOURCES:T.R.M.CACHE_SOURCES.insert(0,directory)

    def search(self,*args,**kwargs):
        T.guard_manifest(self.manifest)
        return super().search(*args,**kwargs)


def retrieve(runtime,question,vector,profile,repeat,routing,original,*,mode='SEQ',parser_binding=None):
    """One scoped U call or exact prior local search reuse; no global union."""
    T.guard_manifest(runtime.manifest)
    identity=f"{question['query_id']}:{profile}:{mode}:r{repeat}"
    base=runtime.path;target=base/'retrievals'/(identity+'.json')
    binding=T.R.P.sha(dict(question=question,vector=T.R.P.sha(vector.tobytes()),profile=profile,repeat=repeat,
        routing={k:v for k,v in routing.items()if k!='db_wall_seconds'},original=original,
        parser_binding=parser_binding,manifest=T.R.P.sha(runtime.manifest),mode=mode))
    if not hasattr(runtime,'reused_searches'):runtime.reused_searches={}
    search_key=T.R.P.sha(dict(question=question['question'],vector=T.R.P.sha(vector.tobytes()),profile=profile,
        scope=routing['scope'],clarify=original['clarify']))
    if target.exists():
        row=T.read(target)
        if row['binding']!=binding or T.read(target.with_suffix('.seal.json'))['row_sha256']!=T.R.P.sha(row):
            raise RuntimeError('Retrieval checkpoint differs from fixed inputs')
        T.R.G.T.S.validate_scope(row['result'],routing['scope'])
        T.R.G.T.S.validate_scope(row['result'],original['card_keys'])
        runtime.reused_searches.setdefault(search_key,row)
        return row
    prior=runtime.reused_searches.get(search_key)
    start=time.perf_counter();cpu=time.process_time()
    from pickcardu_rag.errors import RerankerUnavailable,EvidencePackageTooLarge
    try:
        result=(copy.deepcopy(prior['result'])if prior else dict(cards=[],evidence=[],clarify=True)
            if original['clarify']else runtime.search(question['question'],vector,profile,scope=routing['scope']))
    except (RerankerUnavailable,EvidencePackageTooLarge)as exc:
        result=dict(cards=[],evidence=[],technical_failure=f'{type(exc).__name__}: {exc}')
    T.R.G.T.S.validate_scope(result,routing['scope']);T.R.G.T.S.validate_scope(result,original['card_keys'])
    row=dict(identity=identity,query_id=question['query_id'],question=question['question'],profile=profile,mode=mode,repeat=repeat,
        binding=binding,original_scope=original,routing=routing,result=result,
        retrieval_status='failed'if result.get('technical_failure')else'ok',
        reused_search_identity=prior['identity']if prior else None,
        retrieval_wall_seconds=time.perf_counter()-start,retrieval_cpu_seconds=time.process_time()-cpu)
    T.write(target,row);T.R.X.frozen(target.with_suffix('.seal.json'),dict(row_sha256=T.R.P.sha(row)))
    runtime.reused_searches.setdefault(search_key,row)
    return row
