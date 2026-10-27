"""Pure provenance repair used by experiment70's private DB and input audits.

The notebook owns application and checkpoints. Keeping the same deterministic
rule here lets offline regressions check it without running API notebook cells.
Original facts, source pages, money values and stored relationships are inputs.
"""
import copy
import re

VERSION = 'same_line_explicit_eligible_amount70_v1'
_QUALIFIER = re.compile(r'할인받기\s*전\s*이용금액\s*기준')
_BOUNDARY = re.compile(r'[;。.!?]|할인\s*한도|지급\s*한도|적립\s*한도|캐시백\s*한도')


def source_lines(document):
    """Use the existing line-id convention; do not wrap or normalize pages."""
    return {f"P{int(page['page']):04d}-L{index:04d}":text
            for page in document.get('pages', [])
            for index, text in enumerate(str(page.get('text') or '').splitlines(), 1)}


def cap_context_patch(fact, lines):
    """Restore an explicit same-line purchase qualifier, never infer payout.

    Field text must equal its exact original source span. Only the qualifier
    plus this fact's own fragment is added; intervening other-benefit amounts
    are not copied. No identity, question, answer key or computed cap is used.
    """
    cap = fact['fields'].get('cap') or ''
    if not cap or _QUALIFIER.search(cap):
        return None
    proofs = fact.get('field_evidence', {}).get('cap', [])
    candidates = []
    for proof in proofs:
        line = lines.get(proof.get('line_id'))
        start, end = proof.get('char_start'), proof.get('char_end')
        if (not isinstance(line, str) or not isinstance(start, int) or not isinstance(end, int)
                or not 0 <= start < end <= len(line)
                or line[start:end] != cap or proof.get('fragment') != cap):
            continue
        matches = list(_QUALIFIER.finditer(line, 0, start))
        if len(matches) != 1:
            continue
        match = matches[0]
        if _BOUNDARY.search(line[match.end():start]):
            continue
        candidates.append((proof, line, match))
    if len(candidates) != 1:
        return None
    own, line, qualifier = candidates[0]
    fields = copy.deepcopy(fact['fields'])
    fields['cap'] = qualifier.group() + ' ' + cap
    derived = copy.deepcopy(fact['derived'])
    derived.update(cap_krw=None,cap_state='unknown',
        cap_reason='Explicit same-line pre-discount purchase amount; not a benefit payout cap; rule70.',
        cap_period=None,cap_period_state='unknown',
        cap_period_reason='The purchase ceiling does not establish a payout-cap period; rule70.')
    evidence = copy.deepcopy(fact.get('field_evidence', {}))
    qualifier_proof = dict(line_id=own['line_id'],char_start=qualifier.start(),
                           char_end=qualifier.end(),fragment=qualifier.group())
    evidence['cap'] = [qualifier_proof, copy.deepcopy(own)]
    return dict(rule=VERSION,fields=fields,derived=derived,field_evidence=evidence,
                source_line_id=own['line_id'],source_line=line,original_cap=cap,
                qualifier_evidence=qualifier_proof,original_cap_evidence=copy.deepcopy(own))
