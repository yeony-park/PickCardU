import hashlib
import json
from pathlib import Path

import pytest

from pickcardu_indexer.structured import build_structured_database
from pickcardu_rag.structured import QueryPlan, StructuredStore


ROOT = Path(__file__).resolve().parents[3]
SOURCES = ROOT / 'data/rag/runtime/working/run_f2d29820c6dca642/documents'


def test_builder_preserves_db3_values_and_raw_sources(tmp_path):
    baseline_path = ROOT / 'notebooks/data/60_structured_retrieval_repair/db_v3/structured.sqlite'
    if not SOURCES.is_dir() or not baseline_path.is_file():
        pytest.skip('historical DB3 corpus comparison requires excluded local experiment data')
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


@pytest.mark.parametrize('provider', ['luna', 'upstage'])
def test_builder_preserves_synthetic_values_and_raw_sources(tmp_path, provider):
    repository = tmp_path / 'repository'
    pdf = repository / 'data/raw/issuer/card.pdf'
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b'synthetic PDF fixture')
    source = repository / 'working' / provider / 'normalized.json'
    source.parent.mkdir(parents=True)
    facts = [dict(target='커피', action='할인', benefit_type='카페 할인', value='10', unit='%',
                  typed_normalization={'value': [{'kind': 'ratio', 'decimal': '0.1'}]}),
             dict(target='넷플릭스', action='적립', benefit_type='구독 적립', value='5', unit='%',
                  typed_normalization={'value': [{'kind': 'ratio', 'decimal': '0.05'}]})]
    document = dict(document_id='issuer/card', provider=provider,
        source_pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        identity={'card_name': 'Fixture card', 'issuer_name': 'Fixture issuer'},
        normalization_contract='typed-facts-v2', structure_schema_version='field-evidence-relation-v6',
        pages=[{'page': 1, 'text': '커피 10% 할인\n넷플릭스 5% 적립'}], facts=facts)
    source.write_text(json.dumps(document, ensure_ascii=False))
    before = source.read_bytes(), pdf.read_bytes()
    output = tmp_path / 'structured.sqlite'
    metadata = build_structured_database([source], output, repository)
    store = StructuredStore(output)
    db = store.connect()
    try:
        rows = db.execute('SELECT raw_json,derived_json FROM facts ORDER BY ordinal').fetchall()
        assert [json.loads(raw) for raw, _ in rows] == facts
        assert [json.loads(derived)['percent'] for _, derived in rows] == [10, 5]
        assert metadata['documents'][0]['provider'] == provider
        assert len(metadata['documents']) == 1
    finally:
        db.close()
    requirement = dict(targets=['커피'], action='discount', min_percent=10,
        max_spend_krw=None, min_cap_krw=None, cap_period='any', exclude_targets=[],
        min_frequency=None, frequency_period='any')
    plan = QueryPlan(groups=[[requirement]], requested_fields=[], unsupported_filters=[])
    assert store.query(plan) == ('issuer/card',)
    assert before == (source.read_bytes(), pdf.read_bytes())
    assert output.stat().st_mode & 0o222 == 0
    with pytest.raises(FileExistsError):
        build_structured_database([source], output, repository)
