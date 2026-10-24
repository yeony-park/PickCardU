"""Experiment-only issuer scope: require issuer context, preserve product logic.

Uses the existing catalog forms without merchant/typo/synonym dictionaries.
Does not change QueryPlan, SQL, DB values, retrieval or answer generation.
"""
import importlib.util
from pathlib import Path
import re
import unicodedata

spec = importlib.util.spec_from_file_location('literal60_for64', Path(__file__).with_name('60_query_repair.py'))
OLD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(OLD)


def original_scope(question, documents):
    normalized = unicodedata.normalize('NFKC', question).casefold()
    issuers = {}
    for document in documents:
        issuer = document['document_id'].split('/')[0]
        for name in OLD.issuer_forms(document):
            left = r'(?<![a-z가-힣0-9])'
            if name.endswith(('카드', '은행')):
                # Full catalog issuer, with a boundary before the institutional
                # suffix: 우리 카드 is issuer context; 우리 생활비 is not.
                pattern = left + re.escape(name[:-2]) + r'\s*' + re.escape(name[-2:])
            else:
                suffix = r'\s*(?:카드|은행)'
                if name not in {'우리', '하나', '기업', '국민'}:
                    suffix += r'|\s*중(?:에서)?|\s*에서\s*나온|\s*발급'
                pattern = left + re.escape(name) + r'(?=(?:' + suffix + r'))'
            if re.search(pattern, normalized):
                issuers.setdefault(issuer, set()).add(name)
    keys = {d['document_id'] for d in documents
            if not issuers or d['document_id'].split('/')[0] in issuers}
    names = {}
    compact_question = OLD.canonical(question)
    for document in documents:
        key = document['document_id']
        if key not in keys:
            continue
        full = OLD.canonical(document['identity']['card_name'])
        variants = {full}
        for name in OLD.issuer_forms(document):
            prefix = OLD.canonical(name)
            if full.startswith(prefix):
                variants.add(full[len(prefix):])
        variants |= {v[:-2] for v in tuple(variants) if v.endswith('카드')}
        for variant in variants:
            if len(variant) >= 4 and variant in compact_question:
                names.setdefault(variant, set()).add(key)
    exact = set().union(*names.values()) if names else set()
    ambiguous = any(len(found) > 1 for found in names.values())
    unknown = bool(re.search(r'[A-Za-z가-힣]+카드\s*[Xx]-\d+', question)) and not exact
    unclear = ambiguous or unknown or ('스카이패스카드' in compact_question and not exact)
    return dict(card_keys=[] if unclear else sorted(exact) if exact else sorted(keys) if issuers else None,
                issuer=next(iter(issuers)) if len(issuers) == 1 else None,
                exact_products=sorted(exact), clarify=unclear,
                reason='ambiguous_or_unknown_product' if unclear else None,
                issuer_evidence={k: sorted(v) for k, v in sorted(issuers.items())},
                scope_version='catalog_explicit_issuer_context64')
