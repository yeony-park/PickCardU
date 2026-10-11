"""Deterministic condition-guided search text; no gold labels are consumed.

The original question stays intact. Broader category members are disclosed,
not described as guaranteed synonyms. Answer generation uses the original.
"""
import json
from pathlib import Path
import unicodedata

def norm(value):
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())

def expand_query(question: str, plan: dict, aliases: dict | None = None) -> dict:
    result = {'search_question': question, 'added_terms': [], 'relations': [],
              'fallback_reason': None, 'original_question': question}
    def fallback(reason):
        return {**result, 'fallback_reason': reason}
    if plan.get('unresolved') or plan.get('route') == 'unstructured' or not plan.get('groups'):
        return fallback('unstructured_or_unresolved')
    if aliases is None:
        summary = Path(__file__).parent / 'data/30_structured_benefit_db/db_build_summary.json'
        aliases = json.loads(summary.read_text())['target_aliases']
    original = norm(question)
    groups, added, relations = [], [], []
    for group in plan['groups']:
        clauses = []
        for requirement in group:
            excluded = {norm(term) for target in requirement['exclude_targets']
                        for term in aliases.get(target, [target])}
            terms = []
            for target in requirement['targets']:
                # A merchant alone does not authorize broadening to its whole category.
                roots = [root for root, members in aliases.items()
                         if root in ['여행', '커피', '카페'] and norm(root) in original
                         and (root in requirement['targets'] or
                              target in ['커피', '카페'] and root in ['커피', '카페'])
                         and norm(target) in {norm(t) for t in members}]
                source = roots[0] if roots else target
                anchored = norm(target) in original or bool(roots)
                if not anchored:
                    return fallback('unanchored_target')
                for term in aliases.get(source, [target]):
                    if norm(term) in excluded:
                        continue
                    if term not in terms:
                        terms.append(term)
                    if norm(term) not in original and term not in added:
                        added.append(term)
                        relations.append({'source_target': source, 'term': term,
                            'relation': 'equivalent_alias' if norm(term) == norm(source) else 'hierarchical_expansion',
                            'provenance': 'fixed structured DB target_aliases; project category relation, not semantic equivalence proof'})
            action = requirement['action']
            words = {'discount': ['할인'], 'earn': ['적립', '캐시백'], 'any': []}[action]
            anchored_words = [word for word in words if norm(word) in original]
            if words and not anchored_words:
                return fallback('unanchored_action')
            if terms:
                clauses.append(' / '.join(terms) + (' ' + ' / '.join(anchored_words) if anchored_words else ''))
        if clauses:
            groups.append(' 그리고 '.join(clauses))
    if not groups:
        return fallback('no_supported_terms')
    return {**result, 'search_question': question + '\n검색 표현: (' + ') 또는 ('.join(groups) + ')',
            'added_terms': added, 'relations': relations,
            'boolean_note': 'OR/AND text is retained; vector/BM25 ranking does not enforce Boolean filtering.'}
