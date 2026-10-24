"""65-only scope follow-up; preserved64 code and the catalog stay unchanged."""
import hashlib
import importlib.util
from pathlib import Path
import re
import unicodedata

_spec = importlib.util.spec_from_file_location('preserved_scope64_for65', Path(__file__).with_name('64_scope_repair.py'))
BASE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BASE)
OLD = BASE.OLD


def original_scope(question, documents):
    current = BASE.original_scope(question, documents)
    normalized = unicodedata.normalize('NFKC', question).casefold()
    identifiers = {}
    for document in documents:
        issuer = document['document_id'].split('/')[0]
        for name in OLD.issuer_forms(document):
            # Existing short catalog identifiers (not product transliterations).
            if re.fullmatch(r'[a-z]{2,3}', name) and re.search(
                    r'(?<![a-z가-힣0-9])' + re.escape(name) +
                    r'(?=$|[\s의중에로와과]|카드|은행)', normalized):
                identifiers.setdefault(issuer, set()).add(name)
    if not identifiers:
        return current
    explicit = current.get('issuer_evidence', {})
    trusted = set(explicit) | set(identifiers)
    restricted = [d for d in documents if d['document_id'].split('/')[0] in trusted]
    # Reuse preserved product/clarify matching only inside trusted issuers.
    # This cannot introduce a merchant issuer excluded by the64 context rule.
    scoped = OLD.original_scope(question, restricted)
    scoped['issuer_evidence'] = {key: sorted(set(explicit.get(key, ())) |
        identifiers.get(key, set())) for key in sorted(trusted)}
    scoped['scope_version'] = 'catalog_short_identifier_followup65'
    return scoped


def install(runtime):
    """Bind only the65 notebook process and include this source in its manifest."""
    if getattr(runtime, 'scope_repair65_installed', False):
        return
    original_hashes = runtime.M.source_hashes
    source = Path(__file__).resolve()
    def source_hashes():
        return {**original_hashes(), str(source.relative_to(runtime.ROOT)):
                hashlib.sha256(source.read_bytes()).hexdigest()}
    runtime.S.original_scope = original_scope
    runtime.M.source_hashes = source_hashes
    runtime.scope_repair65_installed = True
