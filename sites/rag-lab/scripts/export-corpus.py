"""Build Sites keyword assets from validated local chunks without an embedding API."""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("handoff", type=Path)
parser.add_argument("--pdf-root", type=Path, required=True)
args = parser.parse_args()
source = args.handoff.resolve()
site = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "scripts/rag_pipeline"))
import common
import hybrid_rag
from hybrid_index import HybridIndex

common.ROOT = args.pdf_root.resolve()
common.RAW_DIR = common.ROOT / "data/raw"
hybrid_rag.CANONICAL_DIR = source / "data/rag/runtime/canonical"
runtime = source / "data/rag/runtime"
validation = hybrid_rag.validate_chunk_corpus(
    runtime / "chunks/parents.jsonl", runtime / "chunks/children.jsonl", False,
    source / "data/rag/reports/chunk_summary.json",
)
index = HybridIndex(Path(":memory:"))
index.rebuild(runtime / "chunks/parents.jsonl", runtime / "chunks/children.jsonl", validation["index_metadata"])
connection = index.connection
parents = [json.loads(r[0]) for r in connection.execute("SELECT metadata FROM parents ORDER BY parent_id")]
rows = connection.execute("SELECT rowid,* FROM children ORDER BY child_id").fetchall()
children = [json.loads(r["metadata"]) for r in rows]
fts_ids = {r["rowid"]: r["child_id"] for r in connection.execute("SELECT rowid,child_id FROM children_fts")}
ordinal = {r["child_id"]: i for i, r in enumerate(rows)}
connection.execute("CREATE VIRTUAL TABLE temp.vocab USING fts5vocab(main, children_fts, 'instance')")
postings = {}
lengths = [0] * len(rows)
for term, doc, count in connection.execute("SELECT term,doc,count(*) FROM vocab GROUP BY term,doc ORDER BY term,doc"):
    i = ordinal[fts_ids[doc]]
    postings.setdefault(term, []).append([i, count])
    lengths[i] += count

def compact(row):
    fields = ['chunk_id','parent_id','document_id','issuer','card_name','page_start','page_end','section_path','text','source_path']
    return {key: row[key] for key in fields if key in row}

gold = common.read_jsonl(source / 'data/rag/eval/gold_queries.jsonl')
normalize = lambda s: ''.join(str(s).casefold().split())
for case in gold:
    case['expected_parent_ids'] = [p['chunk_id'] for p in parents
        if p['document_id'] == case['expected_document_id']
        and p['page_start'] <= case['expected_page'] <= p['page_end']
        and (not case.get('expected_terms') or any(normalize(t) in normalize(p['text']) for t in case['expected_terms']))]

output = site / 'public/corpus'
output.mkdir(parents=True, exist_ok=True)
def write(name, obj):
    (output / name).write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':')))

write('index.json', {'parents':list(map(compact, parents)), 'children':list(map(compact, children)),
    'postings':postings, 'lengths':lengths, 'norms':[1.0 for r in rows]})
write('gold.json',gold)
manifest = {'documents':len({p['document_id'] for p in parents}), 'parents':len(parents),
    'children':len(children), 'dimensions':0, 'model':'pending-embedding-build',
    'version':common.value_sha256({'index_metadata':validation['index_metadata'],'embedding_model':'pending-embedding-build'}),
    'dataset_id':'gold-v1','case_count':len(gold),'validation':'PDF + canonical + chunk/config + SQLite fingerprints verified',
    'metadata':validation['index_metadata'],
    'assets':{name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in ['index.json','gold.json']}}
write('manifest.json',manifest)
# The browser/server manifest is updated only after the embedding build succeeds.
index.close()
print(json.dumps({k:manifest[k] for k in ['documents','parents','children','case_count','validation']}))
print('Keyword assets prepared. Run node scripts/build-e5.mjs before preview or deployment.')
