"""Experiment-only literal matching and bound SQL, never an alias/SQL generator.

Shared by the 60 factorial audit and 61 search comparison. Explicit dependency
injection keeps DB versions/predicates isolated without replacing frozen globals.
"""
import json
import re
import sqlite3
import time
import unicodedata


def canonical(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or ''))).casefold()


def target_matches(target, text):
    wanted = canonical(target)
    if not wanted:
        return False
    raw = unicodedata.normalize('NFKC', str(text or ''))
    # A negative parenthesis is a qualifier, not an additional positive target.
    raw = re.sub(r'\([^)]*(?:제외|불가|미제공)[^)]*\)', '', raw)
    compact = canonical(raw).replace('(', '').replace(')', '')
    positives = [compact]
    # Expand only the written common suffix of coordinated vehicle nouns.
    match = re.fullmatch(r'((?:[가-힣]+차[,/])+[가-힣]+차)(충전(?:소)?)', compact)
    if match:
        positives.extend(word + match[2] for word in re.split('[,/]', match[1]))
    if wanted == '기차':
        positives = [value.replace('전기차', '') for value in positives]
    return any(wanted in value for value in positives)


def issuer_forms(document):
    identity = document['identity']
    forms = {identity['issuer_name'], document['document_id'].split('/')[0]}
    name = identity['issuer_name']
    if name.endswith(('카드', '은행')):
        forms.add(name[:-2])
    return {unicodedata.normalize('NFKC', f).casefold() for f in forms if f}


def original_scope(question, documents):
    normalized = unicodedata.normalize('NFKC', question).casefold()
    issuers = {}
    for document in documents:
        issuer = document['document_id'].split('/')[0]
        for name in issuer_forms(document):
            if name in {'우리', '하나', '기업', '국민'}:
                # A bare pronoun/count is not issuer evidence; a full product
                # name can still identify its unique card below.
                continue
            # Retain word boundaries before compaction: 우리나라 is not 우리.
            pattern = r'(?<![a-z가-힣0-9])' + re.escape(name) + r'(?=$|[\s의중에로와과]|카드|은행)'
            if re.search(pattern, normalized):
                issuers.setdefault(issuer, set()).add(name)
    if len(issuers) > 1:
        keys = {d['document_id'] for d in documents if d['document_id'].split('/')[0] in issuers}
    elif issuers:
        keys = {d['document_id'] for d in documents if d['document_id'].split('/')[0] == next(iter(issuers))}
    else:
        keys = {d['document_id'] for d in documents}
    names = {}
    q = canonical(question)
    for document in documents:
        key = document['document_id']
        if key not in keys:
            continue
        full = canonical(document['identity']['card_name'])
        variants = {full}
        for name in issuer_forms(document):
            prefix = canonical(name)
            if full.startswith(prefix):
                variants.add(full[len(prefix):])
        variants |= {v[:-2] for v in tuple(variants) if v.endswith('카드')}
        for variant in variants:
            if len(variant) >= 4 and variant in q:
                names.setdefault(variant, set()).add(key)
    exact = set().union(*names.values()) if names else set()
    ambiguous = any(len(found) > 1 for found in names.values())
    unknown = bool(re.search(r'[A-Za-z가-힣]+카드\s*[Xx]-\d+', question)) and not exact
    unclear = ambiguous or unknown or ('스카이패스카드' in q and not exact)
    return dict(card_keys=[] if unclear else sorted(exact) if exact else sorted(keys) if issuers else None,
                issuer=next(iter(issuers)) if len(issuers) == 1 else None,
                exact_products=sorted(exact), clarify=unclear,
                reason='ambiguous_or_unknown_product' if unclear else None,
                scope_version='catalog_literal_nfkc60')


def evaluate_fact(Q, fact, requirement):
    r = Q.Requirement.model_validate(requirement).model_dump(mode='json')
    fields = fact['fields']
    wanted = Q.P.E.DB._expanded_targets(r['targets'])
    if not any(target_matches(t, fields['target']) for t in wanted):
        return False
    if any(target_matches(t, fields['target']) for t in Q.P.E.DB._expanded_targets(r['exclude_targets'])):
        return False
    derived = dict(fact['derived'])
    if derived.get('cap_period_state') != 'known':
        period = Q.P.E.explicit_cap_period(fields['cap'])
        if period:
            derived.update(cap_period=period, cap_period_state='known')
    adjusted = {**r, 'targets': [], 'exclude_targets': []}
    if Q.P.E.DB._evaluate_fact({**fact, 'derived': derived}, adjusted).status != 'true':
        return False
    return Q.frequency_matches(fields['frequency'], r['min_frequency'], r['frequency_period'])


def query(Q, value, scope, directory, facts, *, repaired):
    started = time.perf_counter()
    value = Q.QueryPlan.model_validate(value).model_dump(mode='json')
    predicate = (lambda f, r: evaluate_fact(Q, f, r)) if repaired else Q.evaluate_fact
    with sqlite3.connect((directory / 'structured.sqlite').resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        stored = {r[0] for r in db.execute('SELECT document_id FROM documents')}
        if scope is not None and not set(scope) <= stored:
            raise ValueError('Unknown card scope')
        db.create_function('matches_requirement', 2, lambda fid, r: int(predicate(facts[fid], json.loads(r))), deterministic=True)
        logs, matched, requirement_ids = [], {}, {}
        for gi, group in enumerate(value['groups']):
            clauses, params = [], []
            if scope is not None:
                clauses.append('d.document_id IN (' + ','.join('?' for _ in scope) + ')' if scope else '0')
                params.extend(scope)
            if value['unsupported_filters']:
                clauses.append('0')
            for requirement in group:
                clauses.append('EXISTS (SELECT 1 FROM facts f WHERE f.document_id=d.document_id AND matches_requirement(f.fact_id,?)=1)')
                params.append(json.dumps(requirement, ensure_ascii=False, sort_keys=True))
            sql = 'SELECT d.document_id FROM documents d WHERE ' + ' AND '.join(clauses) + ' ORDER BY d.document_id'
            cards = [row[0] for row in db.execute(sql, params)]
            logs.append(dict(sql=sql, parameters=params, eligible_cards=cards))
            for card in cards:
                witnesses = [[r[0] for r in db.execute('SELECT fact_id FROM facts WHERE document_id=? AND matches_requirement(fact_id,?)=1 ORDER BY ordinal', (card, json.dumps(req, ensure_ascii=False, sort_keys=True)))] for req in group]
                if not all(witnesses) or any(facts[fid]['document_id'] != card for row in witnesses for fid in row):
                    raise RuntimeError('Same-card AND witness mismatch')
                requirement_ids.setdefault(card, {})[str(gi)] = witnesses
                matched.setdefault(card, set()).update(fid for row in witnesses for fid in row)
        if not value['groups']:
            sql = 'SELECT document_id FROM documents WHERE 0'
            db.execute(sql).fetchall()
            logs.append(dict(sql=sql, parameters=[], eligible_cards=[]))
    return dict(db_attempted=True, eligible_cards=sorted(matched), matching_fact_ids={k: sorted(v) for k,v in matched.items()},
                requirement_fact_ids=requirement_ids, query_log=logs, plan=value, db_wall_seconds=time.perf_counter()-started)


def route(Q, plan, original, directory, facts, *, repaired, parser_failed=False):
    base = dict(route='U', scope=original['card_keys'], eligible_cards=[], matching_fact_ids={}, db_attempted=False)
    if original['clarify']:
        return {**base, 'route': 'clarify', 'reason': original['reason']}
    if parser_failed:
        return {**base, 'reason': 'parser_failure'}
    result = query(Q, plan, original['card_keys'], directory, facts, repaired=repaired)
    cards = result['eligible_cards']
    return {**base, **result, 'route': 'SEQ' if cards else 'U', 'scope': cards or original['card_keys'],
            'reason': 'matched' if cards else 'no_results'}
