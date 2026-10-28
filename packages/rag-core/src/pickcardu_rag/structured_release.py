"""Validate the optional structured database bound to an immutable release."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from pathlib import Path

from .structured_values import NORMALIZATION_CONTRACT, QUERY_CONTRACT, TARGET_ALIASES, _canonical


def validate_structured_release(root: Path, manifest: dict, *, required=False, read_only=True):
    from .structured import StructuredStore

    contract, path = manifest.get('structured'), root / 'structured.sqlite'
    if contract is None:
        if required or path.exists() or path.is_symlink():
            raise RuntimeError('structured database is missing or undeclared')
        return None
    if not isinstance(contract, dict) or not path.is_file() or path.is_symlink():
        raise RuntimeError('structured database contract is invalid')
    if read_only and stat.S_IMODE(path.stat().st_mode) & 0o222:
        raise RuntimeError('structured database must be read-only')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if contract.get('sha256') != digest:
        raise RuntimeError('structured database hash mismatch')
    if contract.get('normalization_contract') != NORMALIZATION_CONTRACT or contract.get('query_contract') != QUERY_CONTRACT:
        raise RuntimeError('structured normalization/query contract mismatch')
    store = StructuredStore(path)
    db = store.connect()
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('structured database integrity mismatch')
        metadata = json.loads(db.execute("SELECT value_json FROM metadata WHERE key='contract'").fetchone()[0])
        if metadata != {k: v for k, v in contract.items() if k != 'sha256'}:
            raise RuntimeError('structured source metadata mismatch')
        documents = db.execute('SELECT document_id,provider,card_name,issuer_name,source_pdf_sha256,normalized_json_sha256 FROM documents ORDER BY document_id').fetchall()
        if [d[0] for d in documents] != sorted(manifest['document_ids']):
            raise RuntimeError('structured document identities mismatch')
        sources = {d['document_id']: d for d in contract['documents']}
        catalog = {d['document_id']: d for d in manifest['catalog']}
        if len(sources) != len(documents) or len(catalog) != len(documents):
            raise RuntimeError('structured source/catalog identities mismatch')
        for key, provider, name, issuer, pdf_hash, json_hash in documents:
            source, card = sources[key], catalog[key]
            if (source['provider'], source['source_pdf_sha256'], source['normalized_json_sha256']) != (provider, pdf_hash, json_hash):
                raise RuntimeError('structured source hashes mismatch')
            if (card['card_name'], card['issuer_name']) != (name, issuer):
                raise RuntimeError('structured card catalog mismatch')
        aliases = db.execute('SELECT alias_group,alias,alias_normalized FROM target_aliases ORDER BY alias_group,alias').fetchall()
        if aliases != sorted((g, a, _canonical(a)) for g, values in TARGET_ALIASES.items() for a in values):
            raise RuntimeError('structured aliases mismatch')
        # JSON and indexed scalar values must describe the same normalized fact.
        for row in db.execute('SELECT action_kind,action_status,percent,percent_state,spend_threshold_krw,spend_operator,spend_state,cap_krw,cap_state,cap_period,derived_json FROM facts'):
            derived = json.loads(row[-1])
            columns = ('action_kind','action_status','percent','percent_state','spend_threshold_krw','spend_operator','spend_state','cap_krw','cap_state','cap_period')
            if tuple(derived.get(k) for k in columns) != row[:-1]:
                raise RuntimeError('structured derived/scalar mismatch')
    except (sqlite3.Error, KeyError, TypeError, ValueError) as error:
        raise RuntimeError('invalid structured database') from error
    finally:
        db.close()
    return store
