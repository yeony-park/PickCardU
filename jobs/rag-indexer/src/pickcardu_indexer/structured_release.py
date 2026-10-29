"""Publish source-bound releases from existing chunks and embeddings offline."""
from __future__ import annotations

import fcntl
import gc
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import numpy as np

from pickcardu_rag.retrieval import CHUNKING_PROFILES, LEXICAL_CONTRACT, lexical_terms
from pickcardu_rag.structured_release import validate_structured_release

from .pipeline import Indexer, canonical_json, embedding_sha256, sha256_bytes, sha256_file, tree_hash
from .structural import STRUCTURAL_CONTRACT, render_pages
from .grounding import CHUNKING_CONTRACT


def _regular_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir() or any(
        p.is_symlink() or not (p.is_file() or p.is_dir()) for p in root.rglob('*')
    ):
        raise RuntimeError('release source contains a symlink or non-regular entry')


def _corpus(path: Path) -> list[dict]:
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('source corpus integrity mismatch')
        rows = connection.execute('SELECT chunk_id,document_id,level,text,metadata_json FROM chunks ORDER BY chunk_id').fetchall()
        chunks = [dict(chunk_id=r[0], document_id=r[1], level=r[2], text=r[3], metadata=json.loads(r[4])) for r in rows]
        fts = connection.execute('SELECT chunk_id,text FROM chunks_fts ORDER BY chunk_id').fetchall()
        expected_fts = [(c['chunk_id'], ' '.join(lexical_terms(c['metadata']['retrieval_text']))) for c in chunks]
        if not chunks or fts != expected_fts:
            raise RuntimeError('source corpus FTS identity mismatch')
        return chunks
    finally:
        connection.close()


def _vectors(path: Path, profile: str, chunks: list[dict]) -> tuple[np.ndarray, dict]:
    """Open a copy so Chroma migrations cannot mutate an immutable input."""
    import chromadb

    _regular_tree(path)
    before = tree_hash(path)
    with tempfile.TemporaryDirectory() as temporary:
        working = Path(temporary) / 'chroma'
        shutil.copytree(path, working)
        for p in [working, *working.rglob('*')]:
            p.chmod(0o755 if p.is_dir() else 0o644)
        client = chromadb.PersistentClient(path=str(working))
        collection = client.get_collection(profile)
        output = collection.get(include=['embeddings', 'documents', 'metadatas'])
        metadata = dict(collection.metadata or {})
        by_id = {key: i for i, key in enumerate(output['ids'])}
        ids = [c['chunk_id'] for c in chunks]
        if len(by_id) != len(ids) or set(by_id) != set(ids):
            raise RuntimeError('source Chroma chunk identity mismatch')
        for chunk in chunks:
            i = by_id[chunk['chunk_id']]
            if output['documents'][i] != chunk['metadata']['retrieval_text'] or output['metadatas'][i] != {
                'document_id': chunk['document_id'], 'level': chunk['level']
            }:
                raise RuntimeError('source Chroma text or document identity mismatch')
        embeddings = np.asarray([output['embeddings'][by_id[key]] for key in ids], dtype=np.float32)
        del collection, client
        gc.collect()
    if before != tree_hash(path) or metadata.get('hnsw:space') != 'l2':
        raise RuntimeError('source Chroma tree or distance contract mismatch')
    if embeddings.ndim != 2 or embeddings.shape[0] != len(chunks) or embeddings.shape[1] < 1 or not np.isfinite(embeddings).all():
        raise RuntimeError('source embedding shape or finiteness mismatch')
    return embeddings, metadata


def _catalog(chunks: list[dict]) -> list[dict]:
    cards = {}
    for chunk in chunks:
        card = dict(document_id=chunk['document_id'], card_name=chunk['metadata']['card_name'],
                    issuer_name=chunk['metadata']['issuer_name'])
        if card['document_id'] in cards and cards[card['document_id']] != card:
            raise RuntimeError('corpus card catalog is inconsistent')
        cards[card['document_id']] = card
    return [cards[key] for key in sorted(cards)]


def _read_release(root: Path) -> tuple[dict, list[dict], np.ndarray]:
    _regular_tree(root)
    if any(p.stat().st_mode & 0o222 for p in root.rglob('*') if p.is_file()):
        raise RuntimeError('source release files must be immutable')
    manifest = json.loads((root / 'manifest.json').read_text())
    profile = manifest.get('strategy')
    expected_entries = {'manifest.json', 'corpus.sqlite', 'chroma'}
    if manifest.get('structured') is not None:
        expected_entries.add('structured.sqlite')
    if {p.name for p in root.iterdir()} != expected_entries:
        raise RuntimeError('source release contains unexpected entries')
    expected_chunking = STRUCTURAL_CONTRACT if profile == 'parent_child_bundle' else CHUNKING_CONTRACT
    if (manifest.get('schema_version') != 'rag_index_release_v1' or manifest.get('release_id') != root.name
        or profile not in CHUNKING_PROFILES or manifest.get('chunking_contract') != expected_chunking
        or manifest.get('lexical_contract') != LEXICAL_CONTRACT or manifest.get('distance_contract') != 'squared_l2'
        or manifest.get('release_status') not in {'production', 'test_only'}):
        raise RuntimeError('source release contract mismatch')
    if sha256_file(root / 'corpus.sqlite') != manifest.get('corpus_sqlite_sha256'):
        raise RuntimeError('source corpus hash mismatch')
    if tree_hash(root / 'chroma') != manifest.get('chroma_tree_sha256'):
        raise RuntimeError('source Chroma tree hash mismatch')
    chunks = _corpus(root / 'corpus.sqlite')
    if (sha256_bytes(canonical_json(chunks).encode()) != manifest.get('corpus_hash')
        or [c['chunk_id'] for c in chunks] != manifest.get('chunk_ids')
        or sorted({c['document_id'] for c in chunks}) != sorted(manifest.get('document_ids', []))
        or _catalog(chunks) != manifest.get('catalog')):
        raise RuntimeError('source corpus/document/catalog identity mismatch')
    embeddings, metadata = _vectors(root / 'chroma', profile, chunks)
    if (metadata.get('corpus_hash') != manifest['corpus_hash']
        or embeddings.shape[1] != manifest.get('embedding_dimension')
        or embedding_sha256(manifest['chunk_ids'], embeddings) != manifest.get('embedding_sha256')):
        raise RuntimeError('source embedding contract mismatch')
    validate_structured_release(root, manifest)
    return manifest, chunks, embeddings


def _publish(output_runtime: Path, manifest: dict, chunks: list[dict], embeddings: np.ndarray, populate) -> Path:
    release_id = manifest['release_id']
    parent = output_runtime / 'index-release'
    parent.mkdir(parents=True, exist_ok=True)
    locks = output_runtime / '.release-build-locks'
    locks.mkdir(exist_ok=True)
    target = parent / release_id
    with (locks / (release_id + '.lock')).open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if target.exists():
            existing, _, _ = _read_release(target)
            if existing != manifest:
                raise RuntimeError('existing release manifest mismatch')
        else:
            staging = Path(tempfile.mkdtemp(prefix='.' + release_id + '.', dir=parent))
            try:
                populate(staging)
                (staging / 'manifest.json').write_text(canonical_json(manifest) + '\n')
                # The temporary directory deliberately has a different name.
                if sha256_file(staging / 'corpus.sqlite') != manifest['corpus_sqlite_sha256'] or tree_hash(staging / 'chroma') != manifest['chroma_tree_sha256']:
                    raise RuntimeError('release changed during publication')
                validate_structured_release(staging, manifest, read_only=False)
                os.rename(staging, target)
                Indexer._make_read_only(target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        # This existing helper only needs the runtime root and its two validators;
        # avoid creating an unrelated indexer state database during offline import.
        materializer = SimpleNamespace(runtime_root=output_runtime, _read_embeddings=Indexer._read_embeddings,
                                       _verify_release=Indexer._verify_release)
        Indexer._materialize_serving(materializer, target, manifest, chunks, embeddings)
        return target


def import_snapshot_release(snapshot_root: Path, chunks_path: Path, output_runtime: Path, *,
                            embedding_model: str = 'text-embedding-3-small') -> Path:
    """Import a saved profile snapshot after checking SQLite, JSONL, NPY and Chroma."""
    if snapshot_root.resolve() == output_runtime.resolve() or snapshot_root.resolve() in output_runtime.resolve().parents:
        raise ValueError('release output must be outside the source snapshot')
    if chunks_path.is_symlink() or not chunks_path.is_file():
        raise RuntimeError('chunks JSONL must be a regular file')
    _regular_tree(snapshot_root)
    summary = json.loads((snapshot_root / 'index_summary.json').read_text())
    profile = summary.get('profile')
    if (profile not in CHUNKING_PROFILES or summary.get('model') != embedding_model
        or summary.get('distance') != 'squared_l2'):
        raise RuntimeError('snapshot model/profile/distance contract mismatch')
    chunks = _corpus(snapshot_root / 'corpus.sqlite')
    supplied = [json.loads(line) for line in chunks_path.read_text().splitlines() if line.strip()]
    if supplied != chunks:
        raise RuntimeError('snapshot corpus and chunks JSONL mismatch')
    vectors = np.load(snapshot_root / 'embeddings.npy', allow_pickle=False)
    embeddings, _ = _vectors(snapshot_root / 'chroma', profile, chunks)
    if (vectors.shape != embeddings.shape or not np.array_equal(vectors, embeddings)
        or summary.get('embedding_shape') != list(vectors.shape) or summary.get('chunks') != len(chunks)):
        raise RuntimeError('snapshot embedding identity mismatch')
    source = dict(corpus_sha256=sha256_file(snapshot_root / 'corpus.sqlite'),
                  chunks_sha256=sha256_file(chunks_path), embeddings_sha256=sha256_file(snapshot_root / 'embeddings.npy'),
                  chroma_sha256=tree_hash(snapshot_root / 'chroma'), embedding_model=embedding_model, profile=profile)
    identity = sha256_bytes(canonical_json(source).encode())[:16]
    release_id = 'release_snapshot_' + identity
    catalog = _catalog(chunks)
    ids = [c['chunk_id'] for c in chunks]
    corpus_hash = sha256_bytes(canonical_json(chunks).encode())
    manifest = dict(schema_version='rag_index_release_v1', release_id=release_id, run_id='snapshot_' + identity,
        strategy=profile, chunking_contract=STRUCTURAL_CONTRACT if profile == 'parent_child_bundle' else CHUNKING_CONTRACT,
        lexical_contract=LEXICAL_CONTRACT, vector_mode='verified_snapshot_reuse', release_status='production',
        source_validation='assumed_pdf_pass', distance_contract='squared_l2', corpus_hash=corpus_hash,
        document_ids=[c['document_id'] for c in catalog], catalog=catalog, chunk_ids=ids,
        embedding_model=embedding_model, embedding_dimension=embeddings.shape[1],
        embedding_sha256=embedding_sha256(ids, embeddings), corpus_sqlite_sha256=source['corpus_sha256'],
        embedding_usage=dict(provider_called=False, item_count=len(chunks), new_embeddings=0), snapshot_source=source)
    target = output_runtime / 'index-release' / release_id
    if target.exists():
        existing, _, _ = _read_release(target)
        manifest['chroma_tree_sha256'] = existing['chroma_tree_sha256']
        return _publish(output_runtime, manifest, chunks, embeddings, lambda root: None)
    # Chroma's binary identity includes its collection UUID. Build it once, then
    # retain those exact bytes for every subsequent deterministic release reuse.
    with tempfile.TemporaryDirectory() as temporary:
        built = Path(temporary) / 'chroma'
        Indexer._build_chroma(built, chunks, embeddings, corpus_hash, profile)
        manifest['chroma_tree_sha256'] = tree_hash(built)
        def populate(root):
            shutil.copyfile(snapshot_root / 'corpus.sqlite', root / 'corpus.sqlite')
            shutil.copytree(built, root / 'chroma')
        return _publish(output_runtime, manifest, chunks, embeddings, populate)


def _source_binding(chunks: list[dict], contract: dict, structured_database: Path, repository_root: Path) -> None:
    connection = sqlite3.connect(structured_database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        documents = {row[0]: json.loads(row[1]) for row in connection.execute('SELECT document_id,raw_json FROM documents')}
        facts = {}
        for key, raw, derived in connection.execute('SELECT document_id,raw_json,derived_json FROM facts ORDER BY document_id,ordinal'):
            facts.setdefault(key, []).append((json.loads(raw), json.loads(derived)))
    finally:
        connection.close()
    by_key = {d['document_id']: d for d in contract['documents']}
    if set(by_key) != {c['document_id'] for c in chunks} or set(documents) != set(by_key):
        raise RuntimeError('structured source document scope mismatch')
    hashes = {}
    for key, source in by_key.items():
        relative = PurePosixPath(source['source_relative_path'])
        document_id = PurePosixPath(key)
        if relative.is_absolute() or '..' in relative.parts or document_id.is_absolute() or '..' in document_id.parts:
            raise RuntimeError('structured source path is invalid')
        normalized = repository_root.joinpath(*relative.parts)
        pdf = repository_root / 'data/raw' / (key + '.pdf')
        if sha256_file(normalized) != source['normalized_json_sha256']:
            raise RuntimeError('structured normalized source hash mismatch')
        if sha256_file(pdf) != source['source_pdf_sha256']:
            raise RuntimeError('structured source PDF hash mismatch')
        value = json.loads(normalized.read_text())
        if (value['document_id'] != key or value['source_pdf_sha256'] != source['source_pdf_sha256']
            or value['provider'] != source['provider'] or value['pages'] != documents[key]['pages']
            or value['identity'] != documents[key]['identity']
            or any(value.get(field) != source.get(field) for field in ('normalization_contract', 'structure_schema_version'))):
            raise RuntimeError('structured normalized source provenance mismatch')
        from .structured_normalization import normalize_fact
        expected_facts = [(fact, normalize_fact(fact, value['pages'])) for fact in value['facts']]
        if facts.get(key, []) != expected_facts:
            raise RuntimeError('structured raw facts or DB3 normalization differ from source')
        hashes[key] = dict(provider=source['provider'],
            structural=sha256_bytes(render_pages(value['pages']).encode()),
            ocr=sha256_file(normalized.parent / 'ocr.txt') if (normalized.parent / 'ocr.txt').is_file() else None)
    for chunk in chunks:
        origin, metadata = hashes[chunk['document_id']], chunk['metadata']
        if metadata.get('source_sha256') is not None:
            valid = metadata['source_sha256'] == origin['structural']
        else:
            valid = metadata.get('ocr_provider') == origin['provider'] and metadata.get('ocr_text_sha256') == origin['ocr'] and origin['ocr'] is not None
        if not valid:
            raise RuntimeError('corpus OCR source provenance does not match structured source')


def attach_structured_release(source_release: Path, structured_database: Path, output_runtime: Path, *,
                              repository_root: Path | None = None) -> Path:
    """Bind a DB to a new release, preserving source bytes and the active pointer."""
    if source_release.resolve() == output_runtime.resolve() or source_release.resolve() in output_runtime.resolve().parents:
        raise ValueError('release output must be outside the source release')
    source_manifest_hash = sha256_file(source_release / 'manifest.json')
    manifest, chunks, embeddings = _read_release(source_release)
    if structured_database.is_symlink() or not structured_database.is_file():
        raise RuntimeError('structured database must be a regular file')
    connection = sqlite3.connect(structured_database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        contract = json.loads(connection.execute("SELECT value_json FROM metadata WHERE key='contract'").fetchone()[0])
    finally:
        connection.close()
    contract = {**contract, 'sha256': sha256_file(structured_database)}
    _source_binding(chunks, contract, structured_database, repository_root or Path.cwd())
    release_id = 'release_structured_' + sha256_bytes(canonical_json(dict(
        source_manifest_sha256=source_manifest_hash, structured=contract)).encode())[:16]
    result_manifest = {**manifest, 'release_id': release_id, 'structured': contract,
        'source_release': dict(release_id=manifest['release_id'], manifest_sha256=source_manifest_hash),
        'embedding_usage': dict(provider_called=False, item_count=len(chunks), new_embeddings=0)}
    def populate(root):
        shutil.copyfile(source_release / 'corpus.sqlite', root / 'corpus.sqlite')
        shutil.copytree(source_release / 'chroma', root / 'chroma')
        shutil.copyfile(structured_database, root / 'structured.sqlite')
        if sha256_file(source_release / 'manifest.json') != source_manifest_hash:
            raise RuntimeError('source release changed during attachment')
    return _publish(output_runtime, result_manifest, chunks, embeddings, populate)
