import json
from pathlib import Path

import pytest

from pickcardu_indexer.structured import build_structured_database
from pickcardu_rag.structured import QueryPlan, StructuredStore


ROOT = Path(__file__).resolve().parents[3]
SOURCES = ROOT / 'data/rag/runtime/working/run_f2d29820c6dca642/documents'


def test_builder_preserves_db3_values_and_raw_sources(tmp_path):
    sources = sorted(SOURCES.glob('*/upstage/normalized.json'))
    assert sources
    metadata = build_structured_database(sources, tmp_path / 'structured.sqlite', ROOT)
    store = StructuredStore(tmp_path / 'structured.sqlite')
    db = store.connect()
    try:
        import sqlite3
        baseline = sqlite3.connect((ROOT / 'notebooks/data/60_structured_retrieval_repair/db_v3/structured.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
        expected = baseline.execute('SELECT fact_id,raw_json,derived_json FROM facts ORDER BY fact_id').fetchall()
        actual = db.execute('SELECT fact_id,raw_json,derived_json FROM facts ORDER BY fact_id').fetchall()
        baseline.close()
        assert len(actual) == 3403
        assert [(fid, json.loads(raw), json.loads(derived)) for fid, raw, derived in actual] == [
            (fid, json.loads(raw), json.loads(derived)) for fid, raw, derived in expected]
        assert metadata['normalization_contract'] == 'db3-normalization30-56-60-v1'
        assert len(metadata['documents']) == 106
    finally:
        db.close()
    with pytest.raises(FileExistsError):
        build_structured_database(sources, tmp_path / 'structured.sqlite', ROOT)
