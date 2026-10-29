"""Card-only sequential routing; fallback stays inside each original scope."""
from __future__ import annotations

import logging
import time

from .structured import explicit_scope, narrowing_reason


logger = logging.getLogger(__name__)


def structured_scope(question, catalog, store, extractor, *, wallet=None, new=None, targets=None):
    started = time.perf_counter()
    usage = dict(path='hybrid', reason='', provider_called=False, candidate_count=0,
                 extraction_ms=0.0, query_ms=0.0, groups={})
    def finish(keys, reason):
        usage['reason'] = reason
        usage['total_ms'] = round((time.perf_counter() - started) * 1000, 3)
        return keys, usage
    if targets is not None:
        usage['path'] = 'skipped'
        return finish(None, 'precision_comparison')
    explicit = explicit_scope(question, catalog)
    all_keys = tuple(c['card_key'] for c in catalog)
    bases = {'all': all_keys} if wallet is None else {'wallet': wallet}
    if new is not None:
        bases['new'] = new
    if explicit is not None:
        bases = {name: tuple(k for k in keys if k in explicit) for name, keys in bases.items()}
    original = tuple(sorted({k for keys in bases.values() for k in keys}))
    if not original:
        return finish((), 'empty_explicit_scope')
    plan, extraction_usage = extractor.extract(question)
    usage.update(extraction_usage)
    if plan is None:
        return finish(original, 'extraction_failure')
    reason = narrowing_reason(plan)
    if reason:
        return finish(original, reason)
    query_started = time.perf_counter()
    selected = set()
    try:
        for name, base in bases.items():
            matches = store.query(plan, base) if base else ()
            if not set(matches) <= set(base):
                raise ValueError('structured adapter escaped original scope')
            usage['groups'][name] = dict(original_count=len(base), candidate_count=len(matches),
                                         selected_count=len(matches or base), reason='matched' if matches else 'no_results')
            selected.update(matches or base)
    except Exception as error:
        logger.warning('structured query failed: %s', type(error).__name__, exc_info=True)
        usage.update(error_type=type(error).__name__, groups={}, query_ms=round((time.perf_counter()-query_started)*1000, 3))
        return finish(original, 'query_error')
    usage['query_ms'] = round((time.perf_counter() - query_started) * 1000, 3)
    usage['candidate_count'] = sum(g['candidate_count'] for g in usage['groups'].values())
    matched = any(g['reason'] == 'matched' for g in usage['groups'].values())
    usage['path'] = 'structured_then_hybrid' if matched else 'hybrid'
    return finish(tuple(sorted(selected)), 'matched' if matched else 'no_results')
