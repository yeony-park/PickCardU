"""Offline DB3 database builds. Existing inputs and releases are immutable."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath

from pickcardu_rag.structured_values import FIELDS, NORMALIZATION_CONTRACT, QUERY_CONTRACT, TARGET_ALIASES, _canonical, _fact_id

from .structured_normalization import normalize_fact


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def build_structured_database(sources: list[Path], destination: Path, repository_root: Path) -> dict:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if not sources:
        raise ValueError('normalized sources are required')
    documents, facts, contracts = [], [], set()
    for path in sorted(sources):
        raw = path.read_bytes()
        source = json.loads(raw)
        key, provider = source['document_id'], source['provider']
        parts = PurePosixPath(key)
        if parts.is_absolute() or len(parts.parts) != 2 or '..' in parts.parts or provider not in {'upstage', 'luna'}:
            raise ValueError('invalid document identity or provider')
        pdf_hash = sha256(repository_root / 'data/raw' / (key + '.pdf'))
        if pdf_hash != source['source_pdf_sha256']:
            raise ValueError('source PDF hash mismatch')
        relative = path.resolve().relative_to(repository_root.resolve()).as_posix()
        document = dict(document_id=key, provider=provider, identity=source['identity'],
            source_pdf_sha256=pdf_hash, normalized_json_sha256=hashlib.sha256(raw).hexdigest(),
            source_relative_path=relative, source_path_sha256=hashlib.sha256(relative.encode()).hexdigest(),
            page_count=len(source.get('pages') or []), fact_count=len(source['facts']),
            **{k: source.get(k) for k in ('structure_schema_version', 'normalization_contract', 'provenance',
                                         'ocr_provenance', 'ocr_parse_provenance', 'pages')})
        contracts.add((provider, source.get('structure_schema_version'), source.get('normalization_contract'),
                       (source.get('provenance') or {}).get('config_hash')))
        documents.append(document)
        for ordinal, fact in enumerate(source['facts']):
            derived = normalize_fact(fact, source.get('pages') or [])
            fields = [str(fact.get(k) or '') for k in FIELDS]
            facts.append((_fact_id(provider, pdf_hash, key, ordinal), key, ordinal, *fields,
                *(derived.get(k) for k in ('action_kind', 'action_status', 'percent', 'percent_state',
                  'spend_threshold_krw', 'spend_operator', 'spend_state', 'cap_krw', 'cap_state', 'cap_period')),
                canonical(fact), canonical(derived)))
    if len(contracts) != 1 or len({d['document_id'] for d in documents}) != len(documents):
        raise ValueError('duplicate documents or mixed source contracts')
    metadata = dict(normalization_contract=NORMALIZATION_CONTRACT, query_contract=QUERY_CONTRACT,
        documents=[{k: d[k] for k in ('document_id', 'provider', 'source_pdf_sha256', 'normalized_json_sha256',
                                      'source_relative_path', 'normalization_contract', 'structure_schema_version')}
                   for d in sorted(documents, key=lambda d: d['document_id'])])
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.structured-', suffix='.sqlite', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with sqlite3.connect(temporary) as db:
            _create_schema(db)
            db.executemany('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                [(d['document_id'], d['provider'], d['identity']['card_name'], d['identity']['issuer_name'],
                  d['source_pdf_sha256'], d['normalized_json_sha256'], d['source_relative_path'],
                  d['source_path_sha256'], d['page_count'], d['fact_count'], canonical(d)) for d in documents])
            db.executemany('INSERT INTO facts VALUES (' + ','.join('?' for _ in range(25)) + ')', facts)
            db.executemany('INSERT INTO target_aliases VALUES (?,?,?)',
                           [(g, a, _canonical(a)) for g, aliases in sorted(TARGET_ALIASES.items()) for a in aliases])
            db.execute('INSERT INTO metadata VALUES (?,?)', ('contract', canonical(metadata)))
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('structured database integrity failure')
        temporary.chmod(0o444)
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {**metadata, 'sha256': sha256(destination)}


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            card_name TEXT,
            issuer_name TEXT,
            source_pdf_sha256 TEXT NOT NULL,
            normalized_json_sha256 TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            source_path_sha256 TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL,
            raw_json TEXT NOT NULL
        );
        CREATE TABLE facts (
            fact_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            ordinal INTEGER NOT NULL,
            target_raw TEXT NOT NULL,
            action_raw TEXT NOT NULL,
            benefit_type_raw TEXT NOT NULL,
            value_raw TEXT NOT NULL,
            unit_raw TEXT NOT NULL,
            condition_raw TEXT NOT NULL,
            cap_raw TEXT NOT NULL,
            period_raw TEXT NOT NULL,
            frequency_raw TEXT NOT NULL,
            exceptions_raw TEXT NOT NULL,
            action_kind TEXT,
            action_status TEXT NOT NULL,
            percent REAL,
            percent_state TEXT NOT NULL,
            spend_threshold_krw INTEGER,
            spend_operator TEXT,
            spend_state TEXT NOT NULL,
            cap_krw INTEGER,
            cap_state TEXT NOT NULL,
            cap_period TEXT,
            raw_json TEXT NOT NULL,
            derived_json TEXT NOT NULL,
            UNIQUE(document_id, ordinal)
        );
        CREATE TABLE target_aliases (
            alias_group TEXT NOT NULL,
            alias TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            PRIMARY KEY(alias_group, alias)
        );
        CREATE INDEX idx_facts_document ON facts(document_id);
        CREATE INDEX idx_facts_action ON facts(action_kind, action_status);
        CREATE INDEX idx_facts_percent ON facts(percent) WHERE percent_state = 'known';
        CREATE INDEX idx_facts_spend ON facts(spend_threshold_krw) WHERE spend_state = 'known';
        CREATE INDEX idx_facts_cap ON facts(cap_krw) WHERE cap_state = 'known';
        """
    )
