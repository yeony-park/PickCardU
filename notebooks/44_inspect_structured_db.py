"""Read-only schema inventory for notebook 44; no API, OCR or model calls.

The helper keeps the SQLite inventory and JSON consistency checks reproducible
without importing the production pipeline or rebuilding the experiment DB.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'notebooks/data/30_structured_benefit_db'
OUT = ROOT / 'notebooks/data/44_structured_search_db_spec'
TABLES = ['metadata', 'documents', 'facts', 'target_aliases']
INPUTS = [BASE / name for name in ['structured.sqlite', 'facts.jsonl', 'documents.jsonl', 'db_build_summary.json']]
INPUTS += [ROOT / 'notebooks' / name for name in [
    '30_build_structured_db.py', '32_search_experiment.py', '33_query_routing.py',
    '42_expanded_query_evaluation.py', '43_routing_replay.py']]
INPUTS += [ROOT / path for path in ['jobs/rag-indexer/pyproject.toml', 'packages/rag-core/pyproject.toml']]
INPUTS += [ROOT / 'jobs/rag-indexer/src/pickcardu_indexer' / name for name in ['pipeline.py', 'ocr.py']]


def receipt():
    return {str(p.relative_to(ROOT)): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
            'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns} for p in INPUTS}


def shape_profile(records, keys):
    """Distinguish an absent JSON key from an explicit null or empty object."""
    result = {}
    for key in keys:
        counts = Counter()
        for record in records:
            if key not in record:
                kind = 'missing'
            else:
                value = record[key]
                kind = ('null' if value is None else 'object' if isinstance(value, dict)
                        else 'array' if isinstance(value, list) else 'boolean' if isinstance(value, bool)
                        else 'number' if isinstance(value, (int, float)) else 'string')
            counts[kind] += 1
        result[key] = {'type_counts': dict(counts), 'missing_count': counts.get('missing', 0),
                       'explicit_null_count': counts.get('null', 0),
                       'empty_object_count': sum(key in r and isinstance(r[key], dict) and not r[key] for r in records)}
    return result


def inventory():
    before = receipt()
    summary = json.loads((BASE / 'db_build_summary.json').read_text())
    exported = [json.loads(line) for line in (BASE / 'facts.jsonl').read_text().splitlines()]
    documents = [json.loads(line) for line in (BASE / 'documents.jsonl').read_text().splitlines()]
    with sqlite3.connect((BASE / 'structured.sqlite').resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        tables = {}
        for name in TABLES:
            columns = [dict(r) for r in db.execute(f'PRAGMA table_info("{name}")')]
            profiles = {}
            for col in columns:
                key = col['name']
                values = [row[0] for row in db.execute(f'SELECT "{key}" FROM "{name}"')]
                counts = Counter(values)
                brief = sorted(counts.items(), key=lambda item: (-item[1], repr(item[0])))
                profiles[key] = {'null_count': counts.get(None, 0), 'empty_string_count': counts.get('', 0),
                    'distinct_nonnull': len([v for v in counts if v is not None]),
                    'storage_types': dict(Counter(row[0] for row in db.execute(f'SELECT typeof("{key}") FROM "{name}"'))),
                    'examples': [str(v)[:120] for v, _ in brief if v is not None][:3]}
                if key not in {'raw_json', 'derived_json', 'value_json'}:
                    profiles[key]['value_counts'] = [{'value': v, 'count': count} for v, count in brief]
                nums = [v for v in values if isinstance(v, (int, float))]
                if nums:
                    profiles[key]['numeric_min_max'] = [min(nums), max(nums)]
            tables[name] = {'row_count': db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0],
                'columns': columns, 'column_profiles': profiles,
                'foreign_keys': [dict(r) for r in db.execute(f'PRAGMA foreign_key_list("{name}")')],
                'indexes': [dict(r) for r in db.execute(f'PRAGMA index_list("{name}")')],
                'ddl': db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', ('table', name)).fetchone()[0]}
        indices = [dict(r) for r in db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' ORDER BY name")]
        metadata = {r['key']: json.loads(r['value_json']) for r in db.execute('SELECT * FROM metadata')}
        rows = {r['fact_id']: dict(r) for r in db.execute('SELECT * FROM facts')}
        doc_rows = {r['document_id']: dict(r) for r in db.execute('SELECT * FROM documents')}
        assert set(rows) == {r['fact_id'] for r in exported}
        assert set(doc_rows) == {r['document_id'] for r in documents}
        raw_keys = ['target', 'action', 'benefit_type', 'value', 'unit', 'condition', 'cap', 'period', 'frequency', 'exceptions']
        derived_keys = ['action_kind', 'action_status', 'percent', 'percent_state', 'spend_threshold_krw',
                        'spend_operator', 'spend_state', 'cap_krw', 'cap_state', 'cap_period']
        for row in exported:
            sql = rows[row['fact_id']]
            assert sql['document_id'] == row['document_id'] and sql['ordinal'] == row['ordinal']
            assert json.loads(sql['raw_json']) == row['raw_fact']
            assert json.loads(sql['derived_json']) == row['derived']
            assert all(sql[key + '_raw'] == row['fields'][key] for key in raw_keys)
            assert all(sql[key] == row['derived'][key] for key in derived_keys)
        assert all(json.loads(doc_rows[r['document_id']]['raw_json']) == r for r in documents)
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        fk_errors = [list(r) for r in db.execute('PRAGMA foreign_key_check')]
        assert integrity == 'ok' and not fk_errors
    for name, sha in summary['artifact_sha256'].items():
        assert hashlib.sha256((BASE / name).read_bytes()).hexdigest() == sha
    assert before == receipt(), 'An input changed during read-only inventory'
    nested = {}
    for key in ['action_reason', 'percent_reason', 'spend_reason', 'cap_reason', 'cap_period_reason', 'cap_period_state']:
        nested['derived.' + key] = dict(Counter(r['derived'].get(key) for r in exported))
    scopes = Counter(r['relation_scope'].get('scope_type') for r in exported)
    typed_kinds = Counter(entry.get('kind') for r in exported for entries in r['typed_normalization'].values()
                          if isinstance(entries, list) for entry in entries if isinstance(entry, dict))
    field_coverage = {key: sum(bool(r['field_evidence'].get(key)) for r in exported) for key in raw_keys}
    example = next(r for r in exported if r['derived']['action_kind'] == 'discount'
                   and r['derived']['percent_state'] == 'known' and '커피' in r['fields']['target'])
    json_shapes = {
        'facts.raw_json': shape_profile([r['raw_fact'] for r in exported], ['typed_normalization', 'field_evidence', 'relation_scope']),
        'reader_restored_facts': shape_profile(exported, ['typed_normalization', 'field_evidence', 'relation_scope']),
        'documents.raw_json': shape_profile(documents, ['identity', 'pages', 'provenance', 'ocr_provenance', 'ocr_parse_provenance'])}
    return {'stage': 44, 'observed_date': '2026-10-13', 'database': str((BASE / 'structured.sqlite').relative_to(ROOT)),
            'schema_version': summary['schema_version'], 'read_only_uri': 'mode=ro&immutable=1',
            'tables': tables, 'indexes': indices, 'metadata_values': metadata, 'build_summary': summary,
            'nested_value_counts': nested, 'scope_type_counts': dict(scopes), 'typed_kind_counts': dict(typed_kinds),
            'field_evidence_nonempty_counts': field_coverage, 'actual_coffee_example': example,
            'json_shape_profiles': json_shapes,
            'verification': {'integrity_check': integrity, 'foreign_key_errors': fk_errors,
                'sqlite_json_fact_equality_rows': len(exported), 'sqlite_json_document_equality_rows': len(documents),
                'build_summary_artifact_hashes_match': True, 'input_hash_size_mtime_unchanged': True},
            'input_receipt': before, 'new_provider_requests': 0, 'new_experiment_api_cost_usd': '0',
            'performance_comparison_executed': False, 'operating_changes': False}


def run():
    result = inventory()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'schema_inventory.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return {name: result['tables'][name]['row_count'] for name in TABLES}


if __name__ == '__main__':
    print(run())
