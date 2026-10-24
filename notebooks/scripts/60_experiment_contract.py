"""Pure budget/reuse/matrix guards shared by notebooks60--62, no network."""
from decimal import Decimal
import hashlib
import json
import math

MODES = ('U', 'SEQ_BASE', 'SEQ_REPAIRED')
PROFILES = ('card_page_section_benefit', 'parent_child_bundle')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check_budget(cost60, cost62, global_upper, reservation, stage, *, unknown=0):
    if unknown:
        raise RuntimeError('New unpriced provider usage: stop before another request')
    a, b, total, reserve = map(lambda v: Decimal(str(v)), (cost60, cost62, global_upper, reservation))
    if min(a, b, total, reserve) < 0:
        raise RuntimeError('Invalid negative monetary accounting')
    if a + b + reserve > 1 or total + reserve > 8:
        raise RuntimeError('Combined experiment/global budget cap reached')
    if stage not in (60, 62) or (a if stage == 60 else b) + reserve > Decimal('.10' if stage == 60 else '.90'):
        raise RuntimeError('Per-stage budget cap reached')


def same_payload(source, target, prompts):
    if any(source[k] != target[k] for k in ('query_id', 'profile', 'repeat', 'question')):
        return False
    # The exact evidence equality includes card identity, order, text and metadata;
    # runtime additionally binds serialized input, schema and raw source events.
    return digest(dict(model='gpt-6-luna', reasoning='medium', max_output_tokens=2400,
                       prompts=prompts, evidence=source['result']['evidence'])) == digest(dict(
                           model='gpt-6-luna', reasoning='medium', max_output_tokens=2400,
                           prompts=prompts, evidence=target['result']['evidence']))


def validate_matrix(rows, queries, profiles=PROFILES, repeats=(1, 2, 3)):
    expected = {(q, p, r, m) for q in queries for p in profiles for r in repeats for m in MODES}
    actual = [(row['query_id'], row['profile'], row['repeat'], row['mode']) for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('Incomplete, duplicate or changed experiment matrix')


def checked_score(record, query_sha256, document_sha256):
    if record.get('query_sha256')!=query_sha256 or record.get('document_sha256')!=document_sha256:
        raise RuntimeError('Cached score query/document identity mismatch')
    value=float(record['score'])
    if not math.isfinite(value):
        raise RuntimeError('Cached score is nonfinite')
    return value
