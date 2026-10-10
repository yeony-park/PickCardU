"""Verify original source/artifacts and the numbered notebook/report contract."""
from pathlib import Path
import importlib.util
import sys
import hashlib
import time

s=importlib.util.spec_from_file_location('structured_common',Path(__file__).with_name('30_structured_search_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)

def verify():
    baseline=C.read_json(C.STAGES[30]/'original_files_manifest.json');changed=[];missing=[];t=time.perf_counter()
    for row in baseline['files']:
        p=C.ROOT/row['path']
        if not p.is_file():missing.append(row['path']);continue
        h=hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda:f.read(2**20),b''):h.update(block)
        st=p.stat()
        if h.hexdigest()!=row['sha256'] or st.st_size!=row['size'] or st.st_mtime_ns!=row['mtime_ns']:changed.append(row['path'])
    copy=C.read_json(C.ROOT/'data/rag/runtime/worktree-copy-manifest.json')
    model_entries=[r for r in copy['entries'] if r['path'].startswith('.cache/reranker/bge-reranker-v2-m3/')]
    model_changed=[]
    for row in model_entries:
        p=C.ROOT/row['path'];h=hashlib.sha256()
        if not p.is_file():model_changed.append(row['path']);continue
        with p.open('rb') as f:
            for block in iter(lambda:f.read(2**20),b''):h.update(block)
        st=p.stat()
        if h.hexdigest()!=row['sha256'] or st.st_size!=row['bytes'] or st.st_mtime_ns!=row['source_marker']['mtime_ns']:
            model_changed.append(row['path'])
    summary={'protected_original_files':len(baseline['files']),'changed':changed,'missing':missing,
             'reranker_protected_files':len(model_entries),'reranker_changed':model_changed,
             'wall_seconds':time.perf_counter()-t,'content_size_mtime_preserved':not changed and not missing and not model_changed,
             'exclusions':baseline['excluded_directories'],'note':'The original snapshot excludes dependency/vendor/cache directories. Operational reranker records its own artifact fingerprint; source/runtime/old notebooks/reports are individually checked.'}
    C.write_json(C.STAGES[30]/'preservation_verification.json',summary);print(summary)
    if changed or missing or model_changed:raise RuntimeError('Original preservation failed')
    return summary

if __name__=='__main__':verify()
