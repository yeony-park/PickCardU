"""Read-only operational chunk builders; new experiment artifacts only."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
import time

def common():
    p=Path(__file__).with_name('30_structured_search_common.py')
    spec=importlib.util.spec_from_file_location('structured_common',p);m=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=m;spec.loader.exec_module(m);return m
C=common()

def build_chunks():
    from pickcardu_indexer.benefit import build_benefit_chunks
    from pickcardu_indexer.structural import build_structural_chunks,render_pages
    import sqlite3
    db_path=C.ROOT/'data/rag/runtime/indexer-state.sqlite'
    with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        ocr_paths={key:(path,sha) for key,path,sha in db.execute(
            "SELECT document_id,path,sha256 FROM artifacts WHERE run_id=? AND provider='upstage' AND kind='ocr_text'",
            ('run_f2d29820c6dca642',))}
    docs=C.read_jsonl(C.STAGES[30]/'documents.jsonl')
    records={p:[] for p in ['card_page_section_benefit','parent_child_bundle']}
    audits=[];fact_map=[];start=time.perf_counter()
    for doc in docs:
        source=C.read_json(C.ROOT/doc['source_relative_path'])
        raw=render_pages(source['pages']);identity=source['identity'];key=source['document_id']
        kw={'document_id':key,'issuer':identity['issuer_name'],'card_name':identity['card_name']}
        for profile in records:
            try:
                if profile=='card_page_section_benefit':
                    relative,expected=ocr_paths[key];ocr_path=Path(relative)
                    if ocr_path.is_absolute():
                        marker='data/rag/runtime/'
                        if marker not in relative:raise ValueError('Unknown original absolute OCR path')
                        ocr_path=C.ROOT/marker/relative.split(marker,1)[1]
                    else:ocr_path=C.ROOT/ocr_path
                    original=ocr_path.read_bytes()
                    if C.digest(original)!=expected:raise ValueError('OCR text hash mismatch')
                    chunks,audit=build_benefit_chunks(original.decode('utf-8'),**kw,facts=source['facts'],provider='upstage',ocr_text_sha256=expected)
                    audit['input_path']=str(ocr_path.relative_to(C.ROOT));audit['input_sha256']=expected
                else:
                    chunks,hierarchy,audit=build_structural_chunks(raw,**kw)
                    C.write_json(C.STAGES[31]/'hierarchy'/f'{C.digest(key)[:16]}.json',hierarchy)
                ids={r['chunk_id'] for r in chunks}
                if len(ids)!=len(chunks):raise ValueError('Duplicate chunk IDs')
                for r in chunks:
                    if r['document_id']!=key:raise ValueError('Foreign card chunk')
                    related=r['metadata'].get('related_chunk_ids',[])
                    if not set(related)<=ids:raise ValueError('Dangling related chunk')
                records[profile].extend(chunks)
                for ordinal,fact in enumerate(source['facts']):
                    pages={int(ref['line_id'][1:5]) for refs in fact.get('field_evidence',{}).values()
                           for ref in refs if isinstance(ref,dict) and ref.get('line_id','').startswith('P')}
                    fragments=[ref['fragment'] for refs in fact.get('field_evidence',{}).values()
                               for ref in refs if isinstance(ref,dict) and ref.get('fragment')]
                    required=[str(fact.get(f,'')) for f in ['target','value'] if fact.get(f)]
                    def norm(text):return ''.join(str(text).casefold().split())
                    witnesses=[r['chunk_id'] for r in chunks if r['level'] in {'section','benefit','structural'}
                               and (not pages or set(r['metadata']['source_pages'])&pages)
                               and required and all(norm(v) in norm(r['text']) for v in required)]
                    fact_map.append({'document_id':key,'ordinal':ordinal,'profile':profile,'chunk_ids':witnesses})
                audits.append({'document_id':key,'profile':profile,'status':'ok','chunks':len(chunks),'audit':audit})
            except Exception as exc:
                audits.append({'document_id':key,'profile':profile,'status':'failed','error':f'{type(exc).__name__}: {exc}'})
    for profile,rows in records.items():
        C.write_jsonl(C.STAGES[31]/f'{profile}_chunks.jsonl',sorted(rows,key=lambda r:r['chunk_id']))
    C.write_jsonl(C.STAGES[31]/'fact_chunk_map.jsonl',fact_map)
    summary={'documents':len(docs),'profiles':{p:{'chunks':len(r),'documents':len({x['document_id'] for x in r})} for p,r in records.items()},
             'failed_documents':[r for r in audits if r['status']=='failed'],'audits':audits,
             'wall_seconds':time.perf_counter()-start,'assumption':'OCR passed; current imperfect heading hierarchy retained',
             'code_sha256':{p:C.digest((C.ROOT/p).read_bytes()) for p in [
                 'jobs/rag-indexer/src/pickcardu_indexer/benefit.py','jobs/rag-indexer/src/pickcardu_indexer/structural.py',
                 'packages/rag-core/src/pickcardu_rag/retrieval.py','packages/rag-core/src/pickcardu_rag/answering.py']}}
    C.write_json(C.STAGES[31]/'chunking_summary.json',summary)
    print({k:v for k,v in summary.items() if k not in {'audits','code_sha256'}})
    return summary

def build_fts(profile):
    """Executed by database-manager; schema and tokenization match production."""
    import sqlite3
    from pickcardu_rag.retrieval import lexical_terms
    path=C.STAGES[31]/profile;path.mkdir(exist_ok=True)
    db=path/'corpus.sqlite'
    if db.exists():return {'reused':True,'path':str(db)}
    rows=C.read_jsonl(C.STAGES[31]/f'{profile}_chunks.jsonl')
    with sqlite3.connect(db) as conn:
        conn.executescript("CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL, metadata_json TEXT NOT NULL) STRICT; CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED,text,tokenize='unicode61');")
        conn.executemany('INSERT INTO chunks VALUES(?,?,?,?,?)',[(r['chunk_id'],r['document_id'],r['level'],r['text'],C.canonical(r['metadata'])) for r in rows])
        conn.executemany('INSERT INTO chunks_fts VALUES(?,?)',[(r['chunk_id'],' '.join(lexical_terms(r['metadata']['retrieval_text']))) for r in rows])
    return {'chunks':len(rows),'path':str(db)}

def build_vectors(profile):
    import chromadb
    import numpy as np
    from chromadb.config import Settings
    path=C.STAGES[31]/profile;path.mkdir(exist_ok=True)
    rows=C.read_jsonl(C.STAGES[31]/f'{profile}_chunks.jsonl')
    start=time.perf_counter();api=C.CachedAPI()
    embeddings=api.embeddings([r['metadata']['retrieval_text'] for r in rows])
    np.save(path/'embeddings.npy',embeddings)
    client=chromadb.PersistentClient(path=str(path/'chroma'),settings=Settings(anonymized_telemetry=False))
    collection=client.get_or_create_collection(profile,metadata={'hnsw:space':'l2'})
    if collection.count() not in {0,len(rows)}:raise RuntimeError('Partial Chroma index; explicit recovery required')
    if not collection.count():
        for start_row in range(0,len(rows),128):
            batch=rows[start_row:start_row+128]
            collection.add(ids=[r['chunk_id'] for r in batch],documents=[r['metadata']['retrieval_text'] for r in batch],
                           metadatas=[{'document_id':r['document_id'],'level':r['level']} for r in batch],embeddings=embeddings[start_row:start_row+128].tolist())
    output=collection.get(ids=[r['chunk_id'] for r in rows],include=['embeddings'])
    stored={k:v for k,v in zip(output['ids'],output['embeddings'],strict=True)}
    if any(not np.array_equal(embeddings[i],np.asarray(stored[r['chunk_id']],dtype=np.float32)) for i,r in enumerate(rows)):
        raise RuntimeError('Chroma embedding identity mismatch')
    summary={'profile':profile,'chunks':len(rows),'embedding_shape':list(embeddings.shape),'model':C.EMBEDDING_MODEL,
             'distance':'squared_l2','wall_seconds':time.perf_counter()-start,'index_bytes':sum(p.stat().st_size for p in path.rglob('*') if p.is_file())}
    C.write_json(path/'index_summary.json',summary);print(summary,flush=True);return summary

if __name__=='__main__': build_chunks()
