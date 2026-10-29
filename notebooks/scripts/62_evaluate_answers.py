"""Validate manual answer/source/input bindings and compute quality diagnostics.

This is offline scoring only. It neither calls models nor decides whether a
claim means the same thing as a source passage; that judgment is explicit input.
"""
from collections import Counter, defaultdict
import hashlib
import json
import statistics


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def compact(text):
    return ''.join(str(text).casefold().split())


def score_answer(job, answer, review, retrieval_review, group, *, answer_by_card):
    if group not in ('explicit','general','uncertain'):
        raise ValueError('Unknown question group')
    expected=dict(identity=job['identity'],answer_row_sha256=digest(answer),
        retrieval_result_sha256=digest(job['result']),retrieval_review_sha256=digest(retrieval_review),
        actual_input_sha256=answer['input_sha256'])
    if any(review.get(k)!=v for k,v in expected.items()) or not review.get('reviewed_by') or not review.get('reason'):
        raise ValueError('Manual answer review has stale/missing provenance')
    verdict=review['verdict']
    if verdict not in ('PASS','PARTIAL','FAIL','TECHNICAL_FAILURE','UNCERTAIN'):
        raise ValueError('Invalid answer verdict')
    if answer['status']!='ok' and verdict!='TECHNICAL_FAILURE' or answer['status']=='ok' and verdict=='TECHNICAL_FAILURE':
        raise ValueError('Technical failure cannot disappear from the answer denominator')
    if type(review['false_certainty'])is not bool or not isinstance(review['critical_error_ids'],list):
        raise ValueError('Missing critical-error audit')
    if verdict=='PASS' and (review['false_certainty'] or review['critical_error_ids']):
        raise ValueError('Critical errors cannot be counted as PASS')
    body=answer.get('answer') or {};candidates={c['card_key']for c in job['result']['cards']}
    structured={c['card_key']for c in [*body.get('recommendations',[]),*body.get('claims',[])]}
    audits=review['material_card_audits'];keys=[r['card_key']for r in audits]
    if len(keys)!=len(set(keys)) or set(keys)!=structured or not structured<=candidates:
        raise ValueError('Actual recommendations/claims have incomplete card ownership audit')
    if any(not r.get('reason')or r['actual_role']not in ('recommendation','claim_only','explicitly_rejected_or_comparison')for r in audits):
        raise ValueError('Unreviewed actual-card role')
    active={r['card_key']for r in audits if r['actual_role']!='explicitly_rejected_or_comparison'}
    source={r['card_key']:r for r in retrieval_review['card_reviews']}
    if verdict=='PASS' and group!='uncertain' and not any(source[k]['relevant']is True for k in active):
        raise ValueError('Positive question PASS requires an actually used source-relevant card')
    facts={(k,f['condition_id']):f for k in active for f in source[k]['facts']}
    annotated=review['conditions'];pairs=[(r['card_key'],r['condition_id'])for r in annotated]
    if len(pairs)!=len(set(pairs)) or set(pairs)!=set(facts):
        raise ValueError('Actual-card material condition was dropped or invented during adjudication')
    scoped=answer_by_card(body,candidates,review.get('prose_spans',[]))
    supported=reflected=correct_without_input=0;generation_omissions=[]
    for judgment in annotated:
        key=judgment['card_key'];fact=facts[key,judgment['condition_id']]
        status=judgment['answer_status']
        if status not in ('supported','absent','ambiguous','contradicted','unknown','not_applicable')or not judgment.get('reason'):
            raise ValueError('Unreviewed answer condition')
        quotes=judgment['answer_quotes']
        if any(not isinstance(q,str)or not q.strip()or compact(q)not in compact(scoped.get(key,''))for q in quotes):
            raise ValueError('Condition quote is absent from its actual owning-card answer')
        if status=='supported' and (not quotes or fact['source_status']!='verified'):
            raise ValueError('Source uncertainty or missing quote cannot become supported')
        in_input=fact['source_status']=='verified'and fact['input_status']=='supported'
        supported+=in_input
        reflected+=in_input and status=='supported'
        correct_without_input+=status=='supported'and not in_input
        if in_input and status!='supported':generation_omissions.append(key+':'+judgment['condition_id'])
    return {**{k:job[k]for k in ('identity','query_id','profile','mode','repeat')},'group':group,
        'verdict':verdict,'passed':verdict=='PASS','input_supported_conditions':supported,
        'grounded_reflected_conditions':reflected,'answer_reflection':reflected/supported if supported else None,
        'correct_without_input':correct_without_input,'generation_omissions':generation_omissions,
        'false_certainty':review['false_certainty'],'critical_error_ids':review['critical_error_ids'],
        'manual_review_sha256':digest(review)}


def aggregate(rows, *, group_by_query, profiles, modes, repeats=(1,2,3)):
    if not group_by_query or not profiles or not modes or not repeats or not set(group_by_query.values())<={'explicit','general','uncertain'}:
        raise ValueError('Expected nonempty frozen question/profile/mode/repeat contract')
    expected={(q,p,m,r)for q in group_by_query for p in profiles for m in modes for r in repeats}
    actual=[(r['query_id'],r['profile'],r['mode'],r['repeat'])for r in rows]
    if len(actual)!=len(set(actual)) or set(actual)!=expected or len({r['identity']for r in rows})!=len(rows):
        raise ValueError('Answer matrix has missing/extra/duplicate identities')
    if any(r['group']!=group_by_query[r['query_id']]or r['identity']!=f"{r['query_id']}:{r['profile']}:{r['mode']}:r{r['repeat']}"for r in rows):
        raise ValueError('Changed answer identity or subgroup')
    results=[]
    for profile in profiles:
        for mode in modes:
            chosen=[r for r in rows if r['profile']==profile and r['mode']==mode]
            per_query={q:sum(r['passed']for r in chosen if r['query_id']==q)for q in group_by_query}
            groups={g:[r for r in chosen if r['group']==g]for g in set(group_by_query.values())}
            means=[]
            for q in group_by_query:
                values=[r['answer_reflection']for r in chosen if r['query_id']==q and r['answer_reflection']is not None]
                if values:means.append(statistics.mean(values))
            rates=[sum(r['passed']for r in chosen if r['repeat']==rep)/len(group_by_query)for rep in repeats]
            results.append(dict(profile=profile,mode=mode,logical_answers=len(chosen),passed=sum(r['passed']for r in chosen),
                pass_macro=statistics.mean(rates),verdict_counts=dict(Counter(r['verdict']for r in chosen)),
                subgroups={g:dict(logical_rows=len(v),passed=sum(r['passed']for r in v),pass_macro=sum(r['passed']for r in v)/len(v))for g,v in groups.items()},
                per_query_pass_count=per_query,repeat_pass_rates=rates,population_std=statistics.pstdev(rates),
                conditional_reflection_macro=statistics.mean(means)if means else None,conditional_reflection_question_denominator=len(means)))
    return results


def select(evidence_summary, answer_summary, answer_rows, *, profiles,
           group_by_query=None, repeats=(1,2,3)):
    """Apply the pre-approved per-profile gates; never choose a raw best score."""
    modes=('U','SEQ_BASE','SEQ_REPAIRED')
    if not group_by_query or set(group_by_query.values())!={'explicit','general','uncertain'}:
        raise ValueError('Selection requires the complete frozen question/group contract')
    expected={(q,p,m,r)for q in group_by_query for p in profiles for m in modes for r in repeats}
    actual=[(r['query_id'],r['profile'],r['mode'],r['repeat'])for r in answer_rows]
    if len(actual)!=len(set(actual)) or set(actual)!=expected or any(
        r['group']!=group_by_query[r['query_id']]or
        r['identity']!=f"{r['query_id']}:{r['profile']}:{r['mode']}:r{r['repeat']}"for r in answer_rows):
        raise ValueError('Selection cannot proceed on partial or changed answer identities')
    e={(r['profile'],r['mode'],r['group']):r for r in evidence_summary}
    a={(r['profile'],r['mode']):r for r in answer_summary}
    if (len(e)!=len(evidence_summary)or set(e)!={(p,m,g)for p in profiles for m in modes for g in set(group_by_query.values())}
            or len(a)!=len(answer_summary)or set(a)!={(p,m)for p in profiles for m in modes}):
        raise ValueError('Missing or duplicate comparison summary')
    rows={(r['query_id'],r['profile'],r['mode'],r['repeat']):r for r in answer_rows}
    decisions=[]
    for profile in profiles:
        failures=[];candidate=a[profile,'SEQ_REPAIRED']
        for control in ('U','SEQ_BASE'):
            before=a[profile,control]
            for field in ('hit_at3_macro','mrr_at3_macro'):
                if e[profile,'SEQ_REPAIRED','explicit'][field]<e[profile,control,'explicit'][field]:
                    failures.append(control+':ranking_regression:'+field)
            new=e[profile,'SEQ_REPAIRED','explicit']['complete_evidence_macro']
            old=e[profile,control,'explicit']['complete_evidence_macro']
            if new is None or old is None or (new<=old if control=='U'else new<old):
                failures.append(control+':complete_evidence_gate')
            if candidate['pass_macro']<before['pass_macro']:
                failures.append(control+':overall_pass_regression')
            for group in ('explicit','general'):
                if candidate['subgroups'][group]['pass_macro']<before['subgroups'][group]['pass_macro']:
                    failures.append(control+':subgroup_pass_regression:'+group)
            new_uncertain=sum(r['false_certainty']for r in answer_rows if r['profile']==profile and r['mode']=='SEQ_REPAIRED'and r['group']=='uncertain')
            old_uncertain=sum(r['false_certainty']for r in answer_rows if r['profile']==profile and r['mode']==control and r['group']=='uncertain')
            if new_uncertain>old_uncertain:failures.append(control+':false_certainty_regression')
            for r in (v for v in answer_rows if v['profile']==profile and v['mode']=='SEQ_REPAIRED'):
                base=rows[r['query_id'],profile,control,r['repeat']]
                fresh=set(r['critical_error_ids'])-set(base['critical_error_ids'])
                if fresh:failures.append(control+':new_critical_error:'+r['identity']+':'+','.join(sorted(fresh)))
        decisions.append(dict(profile=profile,eligible=not failures,failures=failures,
            decision='eligible_experiment_candidate'if not failures else'not_selected_quality_gate_failed',
            production_adopted=False,policy='U improvement and preservation of baseline SEQ; no trade across groups or profiles'))
    return decisions
