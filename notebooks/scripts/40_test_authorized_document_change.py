"""An approved reference-doc edit must not weaken experiment-input checks."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
HELPER = HERE / '40_authorized_document_change.py'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixture(tmp_path):
    common = load(HERE / '34_followup_common.py', 'doc_change_test_common')
    common.ROOT = tmp_path
    common.STAGES = {34: tmp_path / 'old', 40: tmp_path / 'new'}
    for p in common.STAGES.values():
        p.mkdir()
    doc = tmp_path / 'docs/EVALUATION_METRICS.md'
    doc.parent.mkdir()
    doc.write_text('old reference document')
    source = tmp_path / 'operational.py'
    source.write_text('protected retrieval source')
    before = common.file_record(doc)
    rows = [before, common.file_record(source)]
    (common.STAGES[34] / 'protected_files.json').write_text(json.dumps({'files': rows}))
    doc.write_text('approved notebook metric definitions')
    allowance = {'path': before['path'], 'before': before,
                 'after': common.file_record(doc), 'commit': 'approved-doc-commit',
                 'snapshot_sha256': hashlib.sha256((common.STAGES[34] / 'protected_files.json').read_bytes()).hexdigest()}
    return common, allowance, doc, source


def verify(common, allowance):
    if not HELPER.exists():
        return common.verify_preservation(40)
    return load(HELPER, 'authorized_doc_under_test').verify_snapshot(common, 40, allowance)


def test_approved_doc_change_is_reported_while_other_files_stay_protected(fixture):
    common, allowance, _, _ = fixture
    summary = verify(common, allowance)
    assert summary['passed']
    assert summary['changed'] == ['docs/EVALUATION_METRICS.md']
    assert summary['unauthorized_changes'] == []
    assert summary['authorized_changes'] == [allowance]
    assert summary['checked_files'] == 2


def test_another_source_change_is_still_rejected(fixture):
    common, allowance, _, source = fixture
    source.write_text('unapproved retrieval edit')
    with pytest.raises(RuntimeError, match='preservation'):
        verify(common, allowance)


def test_doc_edit_after_approval_is_rejected(fixture):
    common, allowance, doc, _ = fixture
    doc.write_text('a further unapproved document edit')
    with pytest.raises(RuntimeError, match='preservation'):
        verify(common, allowance)


def test_missing_source_is_still_rejected(fixture):
    common, allowance, _, source = fixture
    source.unlink()
    with pytest.raises(RuntimeError, match='preservation'):
        verify(common, allowance)


def test_allowance_cannot_be_reused_for_an_operational_file(fixture):
    common, allowance, _, source = fixture
    allowance = {**allowance, 'path': source.name}
    with pytest.raises(RuntimeError, match='reference document'):
        verify(common, allowance)


def test_original_doc_snapshot_must_match_allowance(fixture):
    common, allowance, _, _ = fixture
    allowance = {**allowance, 'before': {**allowance['before'], 'sha256': 'wrong'}}
    with pytest.raises(RuntimeError, match='preservation'):
        verify(common, allowance)


def test_rewriting_snapshot_cannot_hide_an_operational_edit(fixture):
    common, allowance, _, source = fixture
    source.write_text('unapproved retrieval edit')
    p = common.STAGES[34] / 'protected_files.json'
    snapshot = common.read_json(p)
    snapshot['files'][1] = common.file_record(source)
    common.write_json(p, snapshot)
    with pytest.raises(RuntimeError, match='snapshot'):
        verify(common, allowance)


def binding_module(fixture, monkeypatch):
    common, allowance, doc, _ = fixture
    helper = load(HELPER, 'doc_binding_under_test')
    monkeypatch.setattr(helper, 'ORIGINAL_SHA256', allowance['before']['sha256'])
    monkeypatch.setattr(helper, 'APPROVED_SHA256', allowance['after']['sha256'])
    monkeypatch.setattr(helper, 'SNAPSHOT_SHA256', allowance['snapshot_sha256'])
    def git_blob(args, **kwargs):
        assert args == ['git', 'show', f'{helper.APPROVED_COMMIT}:{helper.DOCUMENT}']
        assert kwargs['cwd'] == common.ROOT and kwargs['check']
        return SimpleNamespace(stdout=doc.read_bytes())
    monkeypatch.setattr(helper.subprocess, 'run', git_blob)
    return helper, common


def test_binding_rejects_wrong_commit_blob(fixture, monkeypatch):
    helper, common = binding_module(fixture, monkeypatch)
    monkeypatch.setattr(helper.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=b'wrong commit content'))
    with pytest.raises(RuntimeError, match='approved commit'):
        helper.bind_allowance(common)


def test_binding_rejects_a_changed_existing_receipt(fixture, monkeypatch):
    helper, common = binding_module(fixture, monkeypatch)
    receipt = helper.bind_allowance(common)
    common.write_json(common.STAGES[40] / 'authorized_document_change_binding.json', {**receipt, 'commit': 'another'})
    with pytest.raises(RuntimeError, match='receipt'):
        helper.bind_allowance(common)


def test_binding_rejects_rewritten_snapshot(fixture, monkeypatch):
    helper, common = binding_module(fixture, monkeypatch)
    p = common.STAGES[34] / 'protected_files.json'
    common.write_json(p, {'files': []})
    with pytest.raises(RuntimeError, match='snapshot'):
        helper.bind_allowance(common)


def test_install_applies_to_actual_followup_verification_boundary(fixture, monkeypatch):
    helper, common = binding_module(fixture, monkeypatch)
    audit = load(HERE / '37_followup_common.py', 'actual_doc_followup_boundary')
    audit.ROOT = common.ROOT
    audit.F = common
    audit.STAGES = {**common.STAGES, 37: common.ROOT / 'prior'}
    common.STAGES = audit.STAGES
    audit.read = common.read_json
    audit.write = common.write_json
    monkeypatch.setattr(audit, 'initialize', lambda stage: None)
    common.write_json(audit.STAGES[37] / 'prior_files.json', {'files': [common.file_record(fixture[3])]})
    helper.install(audit)
    result = audit.verify()
    assert result['passed'] and result['changed'] == []
    assert result['old']['changed'] == ['docs/EVALUATION_METRICS.md']
    assert len(result['old']['authorized_changes']) == 1
