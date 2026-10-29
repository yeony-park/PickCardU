"""Authorized embedding calls only; no SQLite/Chroma writes or source edits."""
from pathlib import Path
import importlib.util
import sys
import shutil
import time

p=Path(__file__).with_name('30_structured_search_common.py')
s=importlib.util.spec_from_file_location('structured_common',p);C=importlib.util.module_from_spec(s)
sys.modules[s.name]=C;s.loader.exec_module(C)

def main():
    api=C.CachedAPI()
    # A verified terminated DNS failure is archived, never discarded or counted as success.
    for path in api.cache.glob('embedding_batch_*.json'):
        row=C.read_json(path)
        if row['status']=='failed' and 'APIConnectionError' in row.get('error',''):
            history=api.cache/'failure_history';history.mkdir(exist_ok=True)
            target=history/(path.stem+f'_{time.time_ns()}.json')
            shutil.copy2(path,target);path.unlink()
    for profile in ['card_page_section_benefit','parent_child_bundle']:
        rows=C.read_jsonl(C.STAGES[31]/f'{profile}_chunks.jsonl')
        result=api.embeddings([r['metadata']['retrieval_text'] for r in rows])
        print(profile,'embedding cache ready',result.shape,flush=True)

if __name__=='__main__':main()
