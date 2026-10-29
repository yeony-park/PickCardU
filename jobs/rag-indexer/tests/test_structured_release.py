import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from pickcardu_indexer.pipeline import Indexer, canonical_json
from pickcardu_indexer.structured import build_structured_database
from pickcardu_indexer.structured_release import attach_structured_release, import_snapshot_release


@pytest.fixture
def inputs(tmp_path):
    repository = tmp_path / 'repository'
    source = repository / 'working/upstage/normalized.json'
    source.parent.mkdir(parents=True)
    pdf = repository / 'data/raw/issuer/card.pdf'
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b'fixture PDF')
    text = '# Fixture card\n커피 10% 할인'
    (source.parent / 'ocr.txt').write_text(text)
    document = dict(document_id='issuer/card', provider='upstage',
        source_pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        identity={'card_name': 'Fixture card', 'issuer_name': 'Issuer'},
        normalization_contract='fixture-v1', structure_schema_version='fixture-v1',
        pages=[{'page': 1, 'text': text}], facts=[])
    source.write_text(json.dumps(document))
    metadata = dict(document_id='issuer/card', card_name='Fixture card', issuer_name='Issuer',
        level='benefit', source_pages=[1], child_ids=[], related_chunk_ids=[], parent_id=None,
        retrieval_text=text, reranker_text=text, ocr_provider='upstage',
        ocr_text_sha256=hashlib.sha256(text.encode()).hexdigest())
    chunks = [dict(chunk_id='chunk-1', document_id='issuer/card', level='benefit', text=text, metadata=metadata)]
    snapshot = tmp_path / 'card_page_section_benefit'
    snapshot.mkdir()
    chunks_path = tmp_path / 'chunks.jsonl'
    chunks_path.write_text(''.join(canonical_json(c) + '\n' for c in chunks))
    embeddings = np.array([[0.25, 0.5]], dtype=np.float32)
    Indexer._build_fts(snapshot / 'corpus.sqlite', chunks)
    Indexer._build_chroma(snapshot / 'chroma', chunks, embeddings, collection_name=snapshot.name)
    np.save(snapshot / 'embeddings.npy', embeddings)
    (snapshot / 'index_summary.json').write_text(json.dumps(dict(profile=snapshot.name,
        model='text-embedding-3-small', distance='squared_l2', embedding_shape=[1, 2], chunks=1)))
    structured = tmp_path / 'structured.sqlite'
    build_structured_database([source], structured, repository)
    return dict(snapshot=snapshot, chunks_path=chunks_path, runtime=tmp_path / 'runtime',
                repository=repository, source=source, pdf=pdf, structured=structured)


def import_base(inputs):
    return import_snapshot_release(inputs['snapshot'], inputs['chunks_path'], inputs['runtime'])


def test_snapshot_import_reuses_verified_vectors_without_changing_source(inputs):
    original = {p.relative_to(inputs['snapshot']).as_posix(): p.read_bytes()
                for p in inputs['snapshot'].rglob('*') if p.is_file()}
    root = import_base(inputs)
    assert root == import_base(inputs)
    manifest = json.loads((root / 'manifest.json').read_text())
    assert manifest['source_validation'] == 'assumed_pdf_pass'
    assert manifest['embedding_usage']['provider_called'] is False
    assert manifest['embedding_usage']['new_embeddings'] == 0
    assert manifest['chunk_ids'] == ['chunk-1']
    assert original == {p.relative_to(inputs['snapshot']).as_posix(): p.read_bytes()
                        for p in inputs['snapshot'].rglob('*') if p.is_file()}
    assert not (inputs['runtime'] / 'active-index.json').exists()


def test_snapshot_import_rejects_embedding_or_corpus_mismatch(inputs):
    np.save(inputs['snapshot'] / 'embeddings.npy', np.array([[7., 8.]], dtype=np.float32))
    with pytest.raises(RuntimeError, match='embedding'):
        import_base(inputs)


def test_attach_makes_a_new_immutable_release_and_preserves_old_bytes(inputs):
    base = import_base(inputs)
    before = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    release = attach_structured_release(base, inputs['structured'], inputs['runtime'],
                                        repository_root=inputs['repository'])
    assert release != base
    assert release == attach_structured_release(base, inputs['structured'], inputs['runtime'],
                                                repository_root=inputs['repository'])
    assert (release / 'corpus.sqlite').read_bytes() == (base / 'corpus.sqlite').read_bytes()
    assert (release / 'structured.sqlite').read_bytes() == inputs['structured'].read_bytes()
    manifest = json.loads((release / 'manifest.json').read_text())
    assert manifest['structured']['documents'][0]['source_pdf_sha256'] == hashlib.sha256(inputs['pdf'].read_bytes()).hexdigest()
    assert manifest['embedding_sha256'] == json.loads((base / 'manifest.json').read_text())['embedding_sha256']
    assert before == {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    assert (release / 'structured.sqlite').stat().st_mode & 0o222 == 0
    assert not (inputs['runtime'] / 'active-index.json').exists()


@pytest.mark.parametrize('field', ['source', 'pdf'])
def test_attach_rejects_changed_source_or_pdf(inputs, field):
    base = import_base(inputs)
    inputs[field].write_bytes(inputs[field].read_bytes() + b' ')
    with pytest.raises(RuntimeError, match='source|PDF'):
        attach_structured_release(base, inputs['structured'], inputs['runtime'],
                                  repository_root=inputs['repository'])


def test_attach_rejects_corrupt_source_release(inputs):
    base = import_base(inputs)
    path = base / 'corpus.sqlite'
    path.chmod(0o644)
    path.write_bytes(path.read_bytes() + b'changed')
    path.chmod(0o444)
    with pytest.raises(RuntimeError, match='corpus'):
        attach_structured_release(base, inputs['structured'], inputs['runtime'],
                                  repository_root=inputs['repository'])


def test_attach_rejects_other_ocr_source_even_when_normalized_json_is_unchanged(inputs):
    base = import_base(inputs)
    (inputs['source'].parent / 'ocr.txt').write_text('different provider OCR')
    with pytest.raises(RuntimeError, match='OCR source provenance'):
        attach_structured_release(base, inputs['structured'], inputs['runtime'],
                                  repository_root=inputs['repository'])


def test_import_refuses_to_write_inside_its_source(inputs):
    with pytest.raises(ValueError, match='outside'):
        import_snapshot_release(inputs['snapshot'], inputs['chunks_path'], inputs['snapshot'] / 'output')


def test_offline_cli_commands_do_not_create_indexer_state_or_activate(inputs, capsys):
    from pickcardu_indexer.__main__ import main

    runtime = inputs['runtime']
    result = main(['--runtime-root', str(runtime), 'snapshot-import',
                  '--snapshot-root', str(inputs['snapshot']), '--chunks', str(inputs['chunks_path'])])
    assert result == 0
    base = Path(json.loads(capsys.readouterr().out)['release_path'])
    result = main(['--runtime-root', str(runtime), 'structured-attach',
                  '--source-release', str(base), '--database', str(inputs['structured']),
                  '--repository-root', str(inputs['repository'])])
    assert result == 0
    attached = Path(json.loads(capsys.readouterr().out)['release_path'])
    assert (attached / 'structured.sqlite').is_file()
    assert not (runtime / 'indexer-state.sqlite').exists()
    assert not (runtime / 'active-index.json').exists()
