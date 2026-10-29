"""Experiment-only I/O, immutable inputs and exact-payload OpenAI caches."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'notebooks/data'
STAGES = {n: DATA / name for n, name in {
    30:'30_structured_benefit_db', 31:'31_operational_chunking_comparison',
    32:'32_structured_search_comparison', 33:'33_query_routing'}.items()}
MODEL = 'gpt-6-luna'
REASONING = 'medium'
EMBEDDING_MODEL = 'text-embedding-3-small'

def initialize():
    if Path(sys.prefix).name != 'skn25':
        raise RuntimeError('Only the approved conda skn25 environment is allowed')
    sys.dont_write_bytecode = True
    for path in STAGES.values(): path.mkdir(parents=True, exist_ok=True)
    cache = STAGES[30] / 'execution_cache'
    cache.mkdir(exist_ok=True)
    for key in ['HF_HOME','XDG_CACHE_HOME','TMPDIR','MPLCONFIGDIR','JUPYTER_RUNTIME_DIR']:
        target = cache / key.lower(); target.mkdir(exist_ok=True); os.environ[key] = str(target)
    os.environ.update(PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      TOKENIZERS_PARALLELISM='false', ANONYMIZED_TELEMETRY='false')
    for part in ['packages/rag-core/src','jobs/rag-indexer/src','services/rag-api/src']:
        sys.path.insert(0,str(ROOT/part))

def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()

def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':'))

def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def read_jsonl(path): return [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s]

def write_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if not any(path.resolve().is_relative_to(p.resolve()) for p in STAGES.values()):
        raise RuntimeError(f'Experiment output outside stage directories: {path}')
    temp=path.with_suffix(path.suffix+'.writing')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    temp.replace(path)

def write_jsonl(path, rows):
    Path(path).write_text(''.join(canonical(r)+'\n' for r in rows),encoding='utf-8')

def load_module(name, path):
    spec=importlib.util.spec_from_file_location(name,str(path)); module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

def api_client():
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(ROOT/'.env',override=False)
    if not os.getenv('OPENAI_API_KEY'): raise RuntimeError('OPENAI_API_KEY is unavailable')
    return OpenAI(api_key=os.environ['OPENAI_API_KEY'],max_retries=0,timeout=120)

def usage_dict(response):
    usage=getattr(response,'usage',None)
    if isinstance(usage,dict):return usage
    return usage.model_dump(mode='json') if usage is not None else {}

class CachedAPI:
    """SDK-shaped proxy; all LLM requests enforce exact model and reasoning."""
    def __init__(self):
        self.client=None;self.responses=self;self.cache=STAGES[30]/'api_cache';self.cache.mkdir(exist_ok=True)
        self.events=[]
    def _client(self):
        if self.client is None:self.client=api_client()
        return self.client
    def parse(self, **kwargs):
        schema=kwargs['text_format'];kwargs.update(model=MODEL,reasoning={'effort':REASONING})
        kwargs['timeout']=120
        request={k:v for k,v in kwargs.items() if k not in {'text_format','timeout'}}
        request['schema']=schema.model_json_schema()
        key=digest(request);path=self.cache/f'{key}.json';start=time.perf_counter()
        if path.exists():
            stored=read_json(path)
            if stored['status']!='ok':raise RuntimeError('Previous API failure; explicit recovery required: '+stored['error'])
            parsed=schema.model_validate(stored['parsed'])
            response=SimpleNamespace(output_parsed=parsed,usage=stored['usage'])
            self.events.append({'kind':'llm','cache_hit':True,'key':key,'elapsed':time.perf_counter()-start})
            return response
        write_json(path,{'status':'started','request':request,'started_at':time.time()})
        try:
            response=self._client().responses.parse(**kwargs)
            if response.output_parsed is None: raise RuntimeError('Refused/incomplete/empty structured response')
            write_json(path,{'status':'ok','request':request,'parsed':response.output_parsed.model_dump(mode='json'),
                             'raw_response':response.model_dump(mode='json'),'usage':usage_dict(response),
                             'wall_seconds':time.perf_counter()-start,'finished_at':time.time()})
        except Exception as exc:
            write_json(path,{'status':'failed','request':request,'error':f'{type(exc).__name__}: {exc}',
                             'wall_seconds':time.perf_counter()-start})
            raise
        self.events.append({'kind':'llm','cache_hit':False,'key':key,'elapsed':time.perf_counter()-start})
        return response
    def embeddings(self, texts):
        import numpy as np
        cache=self.cache/'embeddings';cache.mkdir(exist_ok=True)
        keys=[digest({'text':s,'model':EMBEDDING_MODEL,'dimension':1536}) for s in texts]
        missing=list(dict.fromkeys(i for i,k in enumerate(keys) if not (cache/f'{k}.npy').exists()))
        # Deduplicate repeated text even when it appears in both profiles.
        unique={keys[i]:i for i in missing};indices=list(unique.values())
        for start in range(0,len(indices),32):
            batch=indices[start:start+32];t=time.perf_counter()
            request={'model':EMBEDDING_MODEL,'input':[texts[i] for i in batch],'dimensions':1536}
            key=digest(request);record=self.cache/f'embedding_batch_{key}.json'
            if record.exists() and read_json(record)['status'] not in {'ok'}:
                raise RuntimeError('Interrupted/failed embedding batch requires recovery')
            write_json(record,{'status':'started','count':len(batch),'request_sha256':key})
            try:
                response=self._client().embeddings.create(**request)
                vectors=sorted(response.data,key=lambda r:r.index)
                if len(vectors)!=len(batch):raise ValueError('Embedding count mismatch')
                for i,row in zip(batch,vectors,strict=True):
                    vector=np.asarray(row.embedding,dtype=np.float32)
                    if vector.shape!=(1536,) or not np.isfinite(vector).all():raise ValueError('Invalid embedding')
                    np.save(cache/f'{keys[i]}.npy',vector)
                write_json(record,{'status':'ok','count':len(batch),'usage':usage_dict(response),
                                   'wall_seconds':time.perf_counter()-t,'request_sha256':key})
            except Exception as exc:
                write_json(record,{'status':'failed','count':len(batch),'error':f'{type(exc).__name__}: {exc}'})
                raise
            print(f'embeddings {min(start+32,len(indices))}/{len(indices)}',flush=True)
        return np.asarray([np.load(cache/f'{k}.npy',allow_pickle=False) for k in keys],dtype=np.float32)

initialize()
