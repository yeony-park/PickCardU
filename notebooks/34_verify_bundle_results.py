"""Audit the whole frozen grid, source parity, whole evidence and report links."""
if not __debug__:
    raise RuntimeError('Verification requires assertions enabled; do not use python -O or PYTHONOPTIMIZE')

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('bundle_audit_common', Path(__file__).with_name('34_followup_common.py'))
F = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = F
spec.loader.exec_module(F)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(value for key, value in attrs if key == 'href')


def verify():
    base = F.STAGES[34]
    result = F.read_json(base / 'retrieval_results.json')
    protocol = F.read_json(base / 'protocol.json')
    cases = F.read_json(F.OLD[32] / 'dataset_freeze.json')['queries']
    expected = {(c['query_id'], p, m) for c in cases for p in ['fixed5', 'fixed4', 'budget5', 'budget8']
                for m in ['U', 'S_THEN_U', 'S_PLUS_U']}
    keys = [(r['query_id'], r['policy'], r['mode']) for r in result['records']]
    assert len(keys) == len(set(keys)) == 396 and set(keys) == expected
    for filename, claimed in protocol['code'].items():
        assert F.digest((F.ROOT / 'notebooks' / filename).read_bytes()) == claimed, filename
    frozen = F.read_json(base / 'component_freeze.json')
    check = dict(frozen)
    claimed = check.pop('sha256')
    assert F.digest(check) == claimed == protocol['component_freeze_sha256']
    original = {(r['query_id'], r['mode']): r for r in F.read_json(F.OLD[33] / 'retrieval_results.json')['records']
                if r['profile'] == 'parent_child_bundle'}
    B = F.load_module('bundle_audit_functions', F.ROOT / 'notebooks/34_bundle_experiment.py')
    chunks = {r['chunk_id']: r for r in
              [__import__('json').loads(line) for line in (F.OLD[31] / 'parent_child_bundle_chunks.jsonl').read_text().splitlines()]}
    for row in result['records']:
        assert row['protocol_sha256'] == protocol['sha256']
        if row['policy'] == 'fixed5':
            prior = original[(row['query_id'], row['mode'])]
            assert row['status'] == prior['status']
            assert row['original_replay_equal'] is True
            if row['status'] == 'ok':
                assert B.comparable_result(row['result']) == B.comparable_result(prior['result'])
        if row['status'] == 'ok':
            for evidence in row['result']['evidence']:
                assert evidence['card_key'] == chunks[evidence['chunk_id']]['document_id']
                assert evidence['text'].endswith(chunks[evidence['chunk_id']]['text'])
        if row['policy'].startswith('budget'):
            assert row['status'] == 'ok'
            for bundle in row['bundles']:
                assert bundle['document_tokens'] <= 4096 and bundle['pair_tokens'] <= 8192
                assert len(bundle['selected_chunk_ids']) <= int(row['policy'][-1])
                assert bundle['selected_chunk_ids'][0] == bundle['seed_chunk_id']
                assert all(chunks[key]['document_id'] == bundle['card_key'] for key in bundle['selected_chunk_ids'])
    assert result['selection']['selected_policy'] == 'budget5'
    assert result['selection']['selected_before_remaining13'] is True
    selection = F.read_json(base / 'selection.json')
    claimed = selection.pop('sha256')
    assert F.digest(selection) == claimed == result['selection']['sha256']
    document = F.ROOT / 'docs/report/34_bundle_comparison_report.html'
    parser = Links()
    parser.feed(document.read_text())
    broken = [href for href in parser.links if not href.startswith(('https://', '#')) and
              not (document.parent / href).resolve().exists()]
    assert not broken, broken
    notebook = F.read_json(F.ROOT / 'notebooks/34_bundle_comparison.ipynb')
    cells = [c for c in notebook['cells'] if c['cell_type'] == 'code']
    assert len(cells) == 4 and all(c['execution_count'] and not any(o['output_type'] == 'error' for o in c['outputs']) for c in cells)
    preservation = F.read_json(base / 'preservation_verification.json')
    assert preservation['passed'] and preservation['checked_files'] == 16861
    summary = {'passed': True, 'grid_rows': 396, 'original_replay_rows': 99, 'budgeted_rows': 198,
               'whole_chunk_evidence_verified': True, 'source_protocol_verified': True,
               'component_freeze_sha256': frozen['sha256'], 'broken_links': broken,
               'notebook_cells_executed': 4, 'protected_files': preservation['checked_files'],
               'html_sha256': F.digest(document.read_bytes())}
    F.write_json(base / 'completion_audit.json', summary)
    # Compact public result retains ALL conditions without committing raw OCR.
    F.write_json(base / 'evaluation_summary.json', {
        'selection': result['selection'], 'protocol_sha256': protocol['sha256'],
        'scope': result['scope'], 'records': [{k: r[k] for k in
            ['query_id', 'profile', 'policy', 'mode', 'split', 'subgroup', 'status', 'metrics', 'error'] if k in r}
            for r in result['records']]})
    print(F.canonical(summary))
    return summary


if __name__ == '__main__':
    F.initialize()
    verify()
