"""Pure same-row normalization for the experiment67 DB copy.

Raw OCR/JSON, global/common-condition inheritance and table relationships are
untouched. The notebook owns copying, applying the patch, and invariants.
"""
from decimal import Decimal
import re
import unicodedata

VERSION = 'connected_regular_spend67_v1'
_AMOUNT = r'(\d[\d,]*(?:\.\d+)?)\s*(만)?\s*원'
_PREVIOUS = re.compile(r'(?:전월|지난\s*달)(?:(?!전월|지난\s*달|신규|등록|발급|유예).){0,90}?' + _AMOUNT + r'\s*이상')
_UNRESOLVED = re.compile(r'[~～∼]|미만|이하|초과|신규|등록|발급|유예|전전월|최근\s*\d+\s*개월|관계없이|상관없이|회수|차년도|연간|연\s*\d+\s*만원|\d+\s*년간?')


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def spend_patch(fact):
    """Return a proof-carrying patch only for a unique explicit regular minimum.

    Unknown stays unknown when a range, grace period, yearly test, missing field
    provenance, raw/typed disagreement or multiple previous-spend tiers remains.
    Absence never becomes zero. Existing known/not-applicable states stay fixed.
    """
    if fact['derived']['spend_state'] != 'unknown':
        return None
    raw = fact['raw_fact']
    text = unicodedata.normalize('NFKC', fact['fields']['condition'])
    if raw.get('condition') != fact['fields']['condition'] or _UNRESOLVED.search(text):
        return None
    matches = list(_PREVIOUS.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    tail = text[match.end():]
    # A second unlabelled amount tier is not a transaction minimum.
    if re.search(_AMOUNT + r'\s*이상', tail) and not re.search(r'건당|이용건당|결제건|결제\s*시|이용\s*건', tail):
        return None
    amount = Decimal(match[1].replace(',', '')) * (10000 if match[2] else 1)
    if amount <= 0 or amount != amount.to_integral_value():
        return None
    typed = fact['typed_normalization']
    if set(typed.get('condition_operators', [])) != {'gte'}:
        return None
    values = [Decimal(x['decimal']) for x in typed.get('condition', []) if x['kind'] == 'KRW']
    if amount not in values:
        return None
    evidence = raw.get('field_evidence', {})
    fields = [evidence.get(key, []) for key in ('target', 'condition')]
    scope = raw.get('relation_scope', {})
    lines = set(scope.get('line_ids', [])) | set(scope.get('row_line_ids', []))
    if (scope.get('scope_type') not in {'bounded_block', 'table_row'}
            or any(not values for values in fields)
            or not {p['line_id'] for field in fields for p in field} <= lines):
        return None
    quoted = ' '.join(p['fragment'] for p in fields[1])
    if compact(match.group()) not in compact(quoted):
        return None
    return {'fact_id': fact['fact_id'], 'document_id': fact['document_id'],
            'rule': VERSION, 'raw_condition': fact['fields']['condition'],
            'connected_quote': match.group(), 'condition_field_evidence': fields[1],
            'relation_scope': scope, 'typed_KRW': [str(value) for value in values],
            'updates': {'spend_threshold_krw': int(amount), 'spend_operator': 'gte',
                        'spend_state': 'known',
                        'spend_reason': 'Single regular previous-month minimum is linked in its own condition field and agrees with typed KRW; rule67.'}}
