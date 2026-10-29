"""Whole-section condition supplementation shared by notebooks57 and58.

This helper owns the reusable, testable evidence invariant, not a report or
answer rubric. It never sees gold facts, source answer pages or expected cards.
"""
import copy
import math

SUFFIX = '전월실적 한도 횟수 서비스 선택 제외 조건'
HEADING_TERMS = ('전월', '실적', '한도', '횟수', '서비스', '선택', '제외', '조건')


def validate_scope(result,scope):
    if scope is not None and any(row['card_key'] not in scope for row in [*result['cards'],*result['evidence']]):
        raise RuntimeError('Retrieved cards or evidence escaped the original/structured scope')


def supplement(question, original, candidates, score, *, fits):
    """Keep selected cards and all A evidence; append at most two sections/card."""
    result = copy.deepcopy(original)
    keys = [card['card_key'] for card in original['cards']]
    existing = {row['chunk_id'] for row in original['evidence']}
    existing_text = [row['text'] for row in original['evidence']]
    eligible = []
    seen = set(existing)
    for row in candidates:
        heading = ' '.join([str(row.get('section') or ''), *row.get('heading_path', [])])
        if (row['card_key'] not in keys or row['chunk_id'] in seen or
                not any(term in heading for term in HEADING_TERMS) or
                any(row['text'] in text for text in existing_text)):
            continue
        seen.add(row['chunk_id'])
        eligible.append(copy.deepcopy(row))
    scores = score(question+' '+SUFFIX, eligible) if eligible else []
    if len(scores) != len(eligible) or not all(math.isfinite(float(s)) for s in scores):
        raise RuntimeError('Incomplete/nonfinite supplementary rerank scores')
    ranked = sorted(zip(eligible,scores), key=lambda pair:(-float(pair[1]),pair[0]['chunk_id']))
    appended, omitted = [], []
    for key in keys:
        base_count = sum(e['card_key'] == key for e in original['evidence'])
        if base_count > 5:
            raise RuntimeError('A evidence exceeds frozen five-per-card contract')
        count = 0
        for source, value in ranked:
            if source['card_key'] != key or count >= 2:
                continue
            row = {k:source[k] for k in ['chunk_id','card_key','card_name','issuer','text','page_num','section','level']}
            row.update(score=float(value),rank=len(result['evidence'])+1,origin='same_card_condition_section')
            if not fits(question,[*result['evidence'],row]):
                omitted.append(source['chunk_id'])
                continue
            result['evidence'].append(row)
            appended.append(source['chunk_id'])
            count += 1
    for card in result['cards']:
        card['evidence_count'] = sum(e['card_key'] == card['card_key'] for e in result['evidence'])
    result['condition_supplement'] = dict(query_suffix=SUFFIX,heading_terms=list(HEADING_TERMS),
        appended_chunk_ids=appended,omitted_for_budget=omitted,
        additional_sections_per_card=2,original_evidence_count=len(original['evidence']),
        no_eviction=True,no_truncation=True,no_gold_inputs=True)
    assert result['evidence'][:len(original['evidence'])] == original['evidence']
    return result
