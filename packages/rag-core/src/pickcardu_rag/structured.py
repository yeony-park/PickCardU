"""Fixed DB3 query plans and read-only, same-card SQL selection."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .structured_values import FIELDS, _evaluate_fact, _expanded_targets


class Requirement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    targets: list[str]
    action: Literal['discount', 'earn', 'any']
    min_percent: float | None
    max_spend_krw: int | None
    min_cap_krw: int | None
    cap_period: Literal['month', 'day', 'any']
    exclude_targets: list[str]
    min_frequency: int | None
    frequency_period: Literal['month', 'day', 'any']

    @model_validator(mode='after')
    def bounds(self):
        for name in ('min_percent', 'max_spend_krw', 'min_cap_krw', 'min_frequency'):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError('negative or nonfinite filter')
        if not any(t.strip() for t in self.targets):
            raise ValueError('a benefit filter requires a target')
        return self


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    groups: list[list[Requirement]] = Field(description='OR groups; AND within one group on the SAME card')
    requested_fields: list[Literal['target', 'action', 'benefit_type', 'value', 'unit', 'condition', 'cap', 'period', 'frequency', 'exceptions']]
    unsupported_filters: list[str]

    @model_validator(mode='after')
    def groups_not_empty(self):
        if any(not group for group in self.groups):
            raise ValueError('empty inner AND is invalid')
        return self


def narrowing_reason(plan: QueryPlan) -> str | None:
    if any(r.max_spend_krw is not None or r.min_cap_krw is not None for g in plan.groups for r in g):
        return 'numeric_guard'
    if plan.unsupported_filters:
        return 'unsupported_conditions'
    if not plan.groups:
        return 'empty_conditions'
    return None


def compact(text: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', text or '').casefold().split())


def target_matches(target: str, text: str) -> bool:
    wanted, text = compact(target), unicodedata.normalize('NFKC', text or '')
    text = re.sub(r'\([^)]*(?:제외|불가|미제공)[^)]*\)', '', text)
    text = compact(text).replace('(', '').replace(')', '')
    positives = [text]
    match = re.fullmatch(r'((?:[가-힣]+차[,/])+[가-힣]+차)(충전(?:소)?)', text)
    if match:
        positives.extend(word + match[2] for word in re.split('[,/]', match[1]))
    if wanted == '기차':
        positives = [value.replace('전기차', '') for value in positives]
    return bool(wanted) and any(wanted in value for value in positives)


def explicit_cap_period(text: str) -> str | None:
    dense = ''.join(text.split())
    month = bool(re.search(r'매월|월간|월별|당월|해당월', dense) or
                 re.search(r'(?<![가-힣])월\s*(?:(?:할인|적립|통합)\s*)?(?:한도|최대|\d)', text))
    day = bool(re.search(r'하루|매일|일일', dense) or
               re.search(r'(?:1\s*일\s*(?:최대|한도|1회)|(?<![가-힣])일\s*(?:최대|한도|\d))', text))
    return 'month' if month and not day else 'day' if day and not month else None


def evaluate_fact(fact: dict, r: dict) -> bool:
    fields, derived = fact['fields'], dict(fact['derived'])
    if not any(target_matches(t, fields['target']) for t in _expanded_targets(r['targets'])):
        return False
    if any(target_matches(t, fields['target']) for t in _expanded_targets(r['exclude_targets'])):
        return False
    if derived.get('cap_period_state') != 'known':
        period = explicit_cap_period(fields['cap'])
        if period:
            derived.update(cap_period=period, cap_period_state='known')
    if _evaluate_fact({**fact, 'derived': derived}, {**r, 'targets': [], 'exclude_targets': []}).status != 'true':
        return False
    if r['min_frequency'] is None:
        return True
    pattern = {'month': r'(?:월|매월)(\d+)회', 'day': r'(?:일|하루)(\d+)회', 'any': r'(\d+)회'}[r['frequency_period']]
    values = [int(v) for v in re.findall(pattern, re.sub(r'\s+', '', fields['frequency']))]
    return len(set(values)) == 1 and values[0] >= r['min_frequency']


class StructuredStore:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)

    def query(self, plan: QueryPlan, card_keys: tuple[str, ...] | None = None) -> tuple[str, ...]:
        if not plan.groups or plan.unsupported_filters:
            return ()
        db = self.connect()
        try:
            stored = {row[0] for row in db.execute('SELECT document_id FROM documents')}
            if card_keys is not None and not set(card_keys) <= stored:
                raise ValueError('unknown card scope')
            columns = ','.join(f'{field}_raw' for field in FIELDS)
            facts = {row[0]: dict(fact_id=row[0], fields=dict(zip(FIELDS, row[1:-1])), derived=json.loads(row[-1]))
                     for row in db.execute(f'SELECT fact_id,{columns},derived_json FROM facts')}
            db.create_function('matches_requirement', 2,
                               lambda fid, r: int(evaluate_fact(facts[fid], json.loads(r))), deterministic=True)
            cards = set()
            for group in plan.groups:
                clauses, parameters = [], []
                if card_keys is not None:
                    clauses.append('d.document_id IN (' + ','.join('?' for _ in card_keys) + ')' if card_keys else '0')
                    parameters.extend(card_keys)
                for r in group:
                    clauses.append('EXISTS (SELECT 1 FROM facts f WHERE f.document_id=d.document_id AND matches_requirement(f.fact_id,?)=1)')
                    parameters.append(r.model_dump_json())
                cards.update(row[0] for row in db.execute('SELECT d.document_id FROM documents d WHERE ' + ' AND '.join(clauses), parameters))
            return tuple(sorted(cards))
        finally:
            db.close()


def explicit_scope(question: str, catalog, allowed: tuple[str, ...] | None = None) -> tuple[str, ...] | None:
    """Literal catalog forms (64/65); independently intersect issuer and product."""
    normalized, dense = unicodedata.normalize('NFKC', question).casefold(), compact(question)
    issuers, products, entries = set(), {}, []
    for card in catalog:
        key, issuer, name = card['card_key'], card['issuer'], card['card_name']
        forms = {issuer.casefold(), key.split('/')[0].casefold()}
        if issuer.endswith(('카드', '은행')):
            forms.add(issuer[:-2].casefold())
        full = compact(name)
        variants = {full}
        for form in forms:
            prefix = compact(form)
            if full.startswith(prefix):
                variants.add(full[len(prefix):])
        variants |= {v[:-2] for v in tuple(variants) if v.endswith('카드')}
        for v in variants:
            if len(v) >= 4 and v in dense:
                products.setdefault(v, set()).add(key)
        entries.append((key, forms))
    occurrences = [(variant, match.start(), match.end()) for variant in products
                   for match in re.finditer(re.escape(variant), dense)]
    products = {variant: keys for variant, keys in products.items() if any(
        not any(other_start <= start and end <= other_end and other_end-other_start > end-start
                for _, other_start, other_end in occurrences)
        for name, start, end in occurrences if name == variant)}
    issuer_question = normalized
    for variant in sorted(products, key=len, reverse=True):
        issuer_question = re.sub(r'\s*'.join(re.escape(c) for c in variant), ' ', issuer_question)
    for key, forms in entries:
        for form in forms:
            left = r'(?<![a-z가-힣0-9])'
            if form.endswith(('카드', '은행')):
                pattern = left + re.escape(form[:-2]) + r'\s*' + re.escape(form[-2:])
            elif re.fullmatch('[a-z]{2,3}', form):
                pattern = left + re.escape(form) + r'(?=$|[\s의중에로와과]|카드|은행)'
            else:
                suffix = r'\s*(?:카드|은행)'
                if form not in {'우리', '하나', '기업', '국민'}:
                    suffix += r'|\s*중(?:에서)?|\s*에서\s*나온|\s*발급'
                pattern = left + re.escape(form) + r'(?=(?:' + suffix + '))'
            if re.search(pattern, issuer_question):
                issuers.add(key.split('/')[0])
    if any(len(keys) > 1 for keys in products.values()) or (
        not products and (re.search(r'[A-Za-z가-힣]+카드\s*[Xx]-\d+', question) or '스카이패스카드' in dense)
    ):
        return ()
    result = None if allowed is None else set(allowed)
    for scope in ([{c['card_key'] for c in catalog if c['card_key'].split('/')[0] in issuers}] if issuers else []) + (
        [set().union(*products.values())] if products else []
    ):
        result = scope if result is None else result & scope
    return None if result is None else tuple(sorted(result))
