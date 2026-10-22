"""Offline manual filter adjudication and question-macro scoring for56.

Separate from the frozen paid runner: this helper verifies manual decisions
without changing prompts, gold or provider checkpoints after execution starts.
It never infers semantic correctness from a keyword or an empty result set.
"""
from collections import defaultdict
import hashlib
import json
import statistics
from itertools import product

SCORE_SCOPE='schema-representable explicit conditions; requested_fields excluded; fixed unrepresentable qualifiers reported separately'


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def filter_key(plan):
    if plan is None:
        return sha(None)
    # Requested fields do not become SQL constraints and are excluded here.
    groups=[]
    for group in plan['groups']:
        requirements=[]
        for item in group:
            row=dict(item)
            for key in ['targets','exclude_targets']:
                if key in row:row[key]=sorted(row[key])
            requirements.append(row)
        groups.append(sorted(requirements,key=sha))
    normalized=dict(groups=sorted(groups,key=sha),unsupported_filters=sorted(plan['unsupported_filters']))
    return sha(normalized)


def adjudicate(row,reviews,gold_sha):
    parser=row['parser'];qid=row['source_query_id']
    result=dict(query_id=qid,form=row['form'],parser_version=row['parser_version'],
        repeat=parser['repeat'],gold_sha256=gold_sha,source_row_sha256=sha(row),
        score_scope=SCORE_SCOPE,unrepresentable_qualifier_completeness=None,
        transport_or_format_failure=parser['status']!='ok')
    if parser['status']!='ok':
        return dict(**result,strict_correct=False,sensitive_correct=False,schema_filter_correct_given_output=None,
            reason='No usable parser output; preserve separately from semantic errors')
    key=qid+':'+filter_key(parser['plan'])
    if key not in reviews:
        raise ValueError('Missing manual review: '+key)
    review=reviews[key]
    if (review.get('gold_sha256')!=gold_sha or review.get('filter_key')!=filter_key(parser['plan'])
            or review.get('query_id')!=qid or not review.get('reviewed_by') or not review.get('reason')
            or review.get('score_scope')!=SCORE_SCOPE
            or any(type(review.get(k)) is not bool for k in ['strict_correct','sensitive_correct'])):
        raise ValueError('Unbound or incomplete manual review: '+key)
    return dict(**result,strict_correct=review['strict_correct'],sensitive_correct=review['sensitive_correct'],
        schema_filter_correct_given_output=review['strict_correct'],reason=review['reason'],review_key=key)


def _macro(rows,metric,excluded=()):
    groups=defaultdict(list)
    for row in rows:
        if row['query_id'] not in excluded and row.get(metric) is not None:
            groups[row['query_id']].append(float(row[metric]))
    values={qid:statistics.mean(v) for qid,v in groups.items()}
    return statistics.mean(values.values()) if values else None,values


def aggregate(rows,*,query_ids,repeats=(1,2,3),ambiguous_query_ids=()):
    if len(query_ids)!=len(set(query_ids)):
        raise ValueError('Duplicate query IDs in parser evaluation matrix')
    expected=set(product(query_ids,['old','new'],['short','detailed'],repeats))
    identities=[tuple(r[k] for k in ['query_id','parser_version','form','repeat']) for r in rows]
    if len(identities)!=len(set(identities)) or set(identities)!=expected:
        raise ValueError('Incomplete or duplicate parser evaluation matrix')
    result={}
    for version in sorted({r['parser_version'] for r in rows}):
        own=[r for r in rows if r['parser_version']==version]
        strict,per_query=_macro(own,'strict_correct')
        sensitive,_=_macro(own,'sensitive_correct')
        unambiguous,clean=_macro(own,'strict_correct',excluded=ambiguous_query_ids)
        schema_filter,_=_macro(own,'schema_filter_correct_given_output')
        result[version]=dict(logical_rows=len(own),question_count=len(per_query),
            score_scope=SCORE_SCOPE,unrepresentable_qualifier_completeness=None,
            strict_question_macro=strict,sensitivity_question_macro=sensitive,
            unambiguous_question_macro=unambiguous,unambiguous_question_count=len(clean),
            schema_filter_macro_given_output=schema_filter,
            failed_rows=sum(r['transport_or_format_failure'] for r in own),
            correct_rows=sum(r['strict_correct'] for r in own),per_query_strict=per_query,
            by_form={form:dict(zip(['accuracy','per_query'],_macro([r for r in own if r['form']==form],'strict_correct')))
                     for form in ['short','detailed']})
    result['_matrix']=dict(query_ids=sorted(query_ids),repeats=list(repeats),logical_rows=len(rows),
        adjudications_sha256=sha(rows),status='complete_identity_matrix',
        ambiguous_query_ids=sorted(ambiguous_query_ids),score_scope=SCORE_SCOPE,
        unrepresentable_qualifier_completeness=None)
    return result


def select(rows,*,query_ids,review_completion,gold_payload,sensitivity_contract,independent_review):
    """Original fixed accuracy rule plus disclosed conservative gold safeguard."""
    if (len(query_ids)!=37 or len(set(query_ids))!=37 or len(rows)!=444
            or review_completion.get('status')!='accepted_after_independent_review'
            or review_completion.get('adjudications_sha256')!=sha(rows)
            or review_completion.get('query_ids')!=sorted(query_ids)
            or not review_completion.get('reviewed_by')):
        raise ValueError('Final parser selection requires the complete reviewed444-row seal')
    if (review_completion.get('independent_review_sha256')!=sha(independent_review)
            or independent_review.get('reviewer')!='registered Codex reviewer'
            or independent_review.get('independent_of_main') is not True
            or independent_review.get('independent_human_review') is not False
            or independent_review.get('status')!='accepted'
            or independent_review.get('adjudications_sha256')!=sha(rows)
            or independent_review.get('blocking_count')!=0 or independent_review.get('major_count')!=0):
        raise ValueError('Final parser selection lacks its bound independent agent review')
    gold_sha=sha(gold_payload);sensitivity_sha=sha(sensitivity_contract)
    ambiguous=set(sensitivity_contract.get('ambiguous',{}))
    if (sensitivity_contract.get('status')!='independent_review_after_freeze_before_manual_scoring'
            or sensitivity_contract.get('frozen_question_pairs_sha256')!=gold_sha
            or sensitivity_contract.get('original_gold_modified') is not False
            or ambiguous!={'H04','H16'}
            or {q['query_id'] for q in gold_payload['records']}!=set(query_ids)
            or review_completion.get('gold_sha256')!=gold_sha
            or review_completion.get('sensitivity_contract_sha256')!=sensitivity_sha
            or any(r.get('gold_sha256')!=gold_sha or r.get('score_scope')!=SCORE_SCOPE for r in rows)):
        raise ValueError('Gold or sensitivity contract differs from reviewed evaluation')
    summary=aggregate(rows,query_ids=query_ids,ambiguous_query_ids=ambiguous)
    summary['_matrix'].update(gold_sha256=gold_sha,sensitivity_contract_sha256=sensitivity_sha)
    def preferred(metric):
        return 'new' if summary['new'][metric]>summary['old'][metric] else 'old'
    strict=preferred('strict_question_macro')
    sensitivity=preferred('sensitivity_question_macro')
    clean=preferred('unambiguous_question_macro')
    selected=strict if strict==sensitivity==clean else 'old'
    return dict(parser_version=selected,strict_preference=strict,sensitivity_preference=sensitivity,summary=summary,
        unambiguous_preference=clean,reason='retain old if reasonable-gold analyses disagree; otherwise fixed question-macro rule, old on tie')
