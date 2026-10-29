"""Offline arithmetic and provenance checks for experiment71.

Semantic statuses come from manual source/input review, never token matching.
The notebook owns preparation/reporting. This helper is separated only to test
same-card AND, unknown/N/A denominators and exact quotation boundaries offline.
"""
import hashlib
import json

STATES = {'supported', 'absent', 'ambiguous', 'contradicted'}


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def path_key(path):
    return tuple(sorted(path['cards'])), path.get('path_id', '')


def score(review, contract):
    required = contract['units']
    paths = review['paths']
    if contract['path_kind'] == 'not_applicable':
        if required or paths:
            raise ValueError('N/A must not manufacture factual requirements')
        return dict(card_hit=None, coverage=None, complete=None, unresolved_paths=0)
    if not required or len(required) != len(set(required)):
        raise ValueError('A factual contract needs unique requirements')
    verified, unresolved, seen = [], 0, set()
    for path in paths:
        key = path_key(path)
        if key in seen:
            raise ValueError('Alternative paths on one card need distinct path_id')
        seen.add(key)
        cards = path['cards']
        if not cards or len(cards) != len(set(cards)):
            raise ValueError('A path needs unique cards')
        if contract['path_kind'] == 'same_card' and len(cards) != 1:
            raise ValueError('Do not merge different cards for same-card AND')
        if contract['path_kind'] == 'comparison' and len(cards) < 2:
            raise ValueError('Comparison needs at least two card paths')
        if set(path['units']) != set(required) or set(path['units'].values()) - STATES:
            raise ValueError('Missing, extra or unknown requirement status')
        if path['eligibility'] == 'verified':
            verified.append(path)
        elif path['eligibility'] == 'unresolved':
            unresolved += 1
        elif path['eligibility'] != 'rejected':
            raise ValueError('Unknown source eligibility')
    if not verified:
        unknown = unresolved > 0
        return dict(card_hit=None if unknown else False,
                    coverage=None if unknown else 0,
                    complete=None if unknown else False, unresolved_paths=unresolved)
    best = max(sum(s == 'supported' for s in p['units'].values()) for p in verified)
    return dict(card_hit=True, coverage=best / len(required),
                complete=best == len(required), unresolved_paths=unresolved)


def check_quote(citation, packet):
    index = citation['evidence_index']
    if type(index) is not int or not 0 <= index < len(packet['evidence']):
        raise ValueError('Evidence index must be a nonnegative in-range integer')
    evidence = packet['evidence'][index]
    field = citation.get('field', 'text')
    if field not in {'text', 'issuer', 'card_name'}:
        raise ValueError('Only actual transmitted evidence fields are quotable')
    text = evidence[field]
    start, end = citation['start'], citation['end']
    if not 0 <= start < end <= len(text) or text[start:end] != citation['quote']:
        raise ValueError('Input quote does not match exact serialized evidence')
    if citation['card_key'] != evidence['card_key']:
        raise ValueError('Input quote belongs to another card')


def check_source(citation, catalog):
    source = catalog[citation['condition_id']]
    if citation['card_key'] != source['card_key']:
        raise ValueError('Source owner changed')
    if not citation['quote'] or not any(citation['quote'] in q for q in source['literal_quotes']):
        raise ValueError('Source quote does not match registered original')


def validate(review, contract, packet, catalog, unit_input_fields=None):
    if review['input_id'] != packet['input_id'] or review['packet_sha256'] != sha(packet):
        raise ValueError('Review is bound to a different input')
    score(review, contract)
    allowed = unit_input_fields if unit_input_fields is not None else {
        unit: ['text'] for unit in contract['units']}
    if set(allowed) != set(contract['units']):
        raise ValueError('Field policy must cover exactly the required units')
    for unit, fields in allowed.items():
        if not fields or set(fields) - {'text', 'issuer', 'card_name'}:
            raise ValueError('Invalid field policy')
        if unit != 'issuer' and set(fields) != {'text'}:
            raise ValueError('Benefit/numeric/condition units require text evidence')
    cards = {c['card_key'] for c in packet['cards']}
    covered = set()
    for path in review['paths']:
        if set(path['cards']) - cards:
            raise ValueError('A path contains an unretrieved card')
        covered.update(path['cards'])
        if not path.get('reason'):
            raise ValueError('Manual source eligibility reasoning is required')
        for citation in path['source_quotes']:
            check_source(citation, catalog)
            if citation['card_key'] not in path['cards']:
                raise ValueError('Eligibility proof uses another card')
        if path['eligibility'] == 'verified' and not path['source_quotes']:
            raise ValueError('Source eligibility needs original evidence')
        if path['eligibility'] == 'verified' and not any(
                catalog[s['condition_id']]['source_status'] == 'verified'
                for s in path['source_quotes']):
            raise ValueError('Uncertain source cannot certify source eligibility')
        if set(path['unit_evidence']) != set(contract['units']):
            raise ValueError('Requirement audit is incomplete')
        for unit, detail in path['unit_evidence'].items():
            if not detail.get('reason'):
                raise ValueError('Every status needs semantic reasoning')
            for citation in detail['input_quotes']:
                if citation.get('field', 'text') not in allowed[unit]:
                    raise ValueError('Metadata cannot replace required benefit evidence')
                check_quote(citation, packet)
                if citation['card_key'] not in path['cards']:
                    raise ValueError('A unit uses another card input')
            for citation in detail['source_quotes']:
                check_source(citation, catalog)
                if citation['card_key'] not in path['cards']:
                    raise ValueError('A unit uses another card source')
            if path['units'][unit] == 'supported' and (not detail['input_quotes'] or not detail['source_quotes']):
                raise ValueError('Supported requires actual input and original proof')
            if path['units'][unit] == 'supported' and any(
                    catalog[s['condition_id']]['source_status'] != 'verified'
                    for s in detail['source_quotes']):
                raise ValueError('Uncertain source cannot certify supported evidence')
    if contract['path_kind'] != 'not_applicable' and cards != covered:
        raise ValueError('Every returned card must be reviewed, not just a chosen winner')
    return True
