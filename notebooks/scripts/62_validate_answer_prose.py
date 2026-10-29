"""Offline supplement to the sealed 62 scorer; no model calls or prompt changes.

A notebook alone cannot enforce this reusable validation at every aggregate entry.
Manual ownership/meaning judgments are input, never inferred from word overlap.
The helper binds them to complete prose, source PDFs and the actual model input.
"""
import copy
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def compact(value):
    return ''.join(str(value).casefold().split())


def score_answer(job, answer, review, retrieval_review, group, *, score_frozen,
                 answer_by_card, validate_source_support):
    audit=review.get('prose_audit', {})
    body=answer.get('answer') or {};text=body.get('answer_text', '')
    if (audit.get('answer_text_sha256')!=digest(text) or not audit.get('reviewed_by')
            or not audit.get('reason')):
        raise ValueError('Missing/stale full-prose manual review')
    candidates={c['card_key']for c in job['result']['cards']}
    structured={c['card_key']for c in [*body.get('claims', []), *body.get('recommendations', [])]}
    names=[compact(c.get('card_name',''))for c in job['result']['cards']if len(compact(c.get('card_name','')))>=3]
    cursor=0;spans=[]
    for part in audit['segments']:
        start,end=part['start'],part['end'];key=part['card_key']
        if (type(start)is not int or type(end)is not int or start!=cursor
                or not start<end<=len(text) or not part.get('reason')
                or key is not None and key not in candidates):
            raise ValueError('Incomplete/overlapping or unowned prose partition')
        cursor=end
        if key is not None:spans.append(part)
        else:
            neutral=part.get('neutral_review', {});piece=text[start:end]
            if (neutral.get('text_sha256')!=digest(piece) or not neutral.get('reviewed_by')
                    or neutral.get('no_card_specific_fact_or_comparison')is not True
                    or not neutral.get('reason') or any(name in compact(piece)for name in names)
                    or re.search(r'\d[\d,.]*\s*(?:%|퍼센트|원|만원|천원|포인트|마일|회|개월)',piece)):
                raise ValueError('Neutral prose cannot hide card facts or unreviewed comparison claims')
    if cursor!=len(text):raise ValueError('Prose partition does not cover the entire answer')
    source={c['card_key']:c for c in retrieval_review['card_reviews']}
    checks=audit['card_checks'];keys=[c['card_key']for c in checks]
    if len(keys)!=len(set(keys)) or set(keys)!=structured|{s['card_key']for s in spans}:
        raise ValueError('Missing/duplicate structured, prose-only or comparison card audit')
    material={c['card_key']:c for c in review['material_card_audits']}
    owned=answer_by_card(body,candidates,spans)
    evidence={k:'\n'.join(e['text']for e in job['result']['evidence']if e['card_key']==k)for k in candidates}
    active=[];comparisons=[];all_conditions=[];assertion_count=0;comparison_condition_count=0
    shared_contexts={}
    for check in checks:
        key=check['card_key'];src=source[key];role=check['actual_role']
        if (check.get('source_pdf_sha256')!=src['source_pdf_sha256']
                or check.get('input_sha256')!=answer['input_sha256'] or not check.get('reason')
                or role not in ('recommendation','claim_only','explanation','explicitly_rejected_or_comparison')):
            raise ValueError('Missing/stale source, input or role audit')
        if key in material and role!=material[key]['actual_role']:
            raise ValueError('Prose audit cannot silently change the material card role')
        if check.get('uses_existing_conditions'):
            facts=[f for f in review['conditions']if f['card_key']==key]
            if check.get('condition_review_sha256')!=digest(facts):
                raise ValueError('Stale inherited condition audit')
        else:facts=check['conditions']
        expected={f['condition_id']:f for f in src['facts']}
        ids=[f['condition_id']for f in facts]
        if len(ids)!=len(set(ids)) or set(ids)!=set(expected):
            raise ValueError('Prose/comparison card has missing or invented condition audit')
        if key in material and not set(material[key].get('checked_condition_ids', []))<=set(ids):
            raise ValueError('Material comparison condition declaration is not actually audited')
        for fact in facts:
            status=fact['answer_status'];quotes=fact['answer_quotes']
            if (fact['card_key']!=key or not fact.get('reason')
                    or status not in ('supported','absent','ambiguous','contradicted','unknown','not_applicable')
                    or any(not q.strip()or compact(q)not in compact(owned.get(key,''))for q in quotes)
                    or status=='supported'and(not quotes or expected[fact['condition_id']]['source_status']!='verified')):
                raise ValueError('Invalid owning-card condition quote or source judgment')
        assertions=check['assertion_checks']
        if not assertions:raise ValueError('Every used/comparison card needs actual assertion review')
        for assertion in assertions:
            quotes=assertion['answer_quotes'];proofs=assertion['source_support'];input_quotes=assertion['input_quotes']
            context=assertion.get('shared_predicate_context');quote_scope=owned.get(key,'')
            if context is not None:
                start,end=context['start'],context['end'];participants=context['card_keys']
                if (type(start)is not int or type(end)is not int or not 0<=start<end<=len(text)
                        or not context.get('reason') or context.get('text_sha256')!=digest(text[start:end])
                        or len(participants)!=len(set(participants)) or len(participants)<2
                        or key not in participants or not set(participants)<=set(keys) or not proofs
                        or compact(text[start:end])not in compact('\n'.join(quotes))):
                    raise ValueError('Shared predicate lacks exact context or participant source audit')
                signature=digest(dict(start=start,end=end,text_sha256=context['text_sha256'],card_keys=sorted(participants)))
                entry=shared_contexts.setdefault(signature,dict(expected=set(participants),seen=set()))
                entry['seen'].add(key);quote_scope+='\n'+text[start:end]

            if (not assertion.get('reason') or not quotes
                    or any(not q.strip()or compact(q)not in compact(quote_scope)for q in quotes)
                    or assertion['input_status']not in ('supported','absent','ambiguous')
                    or assertion['input_status']=='supported'and not input_quotes
                    or any(not q.strip()or compact(q)not in compact(evidence[key])for q in input_quotes)):
                raise ValueError('Actual assertion/owning-card input quote is unbound')
            if not proofs and (assertion.get('source_status')!='not_asserted_only_current_input_insufficiency'
                    or any(f['answer_status']=='supported'for f in facts)):
                raise ValueError('Actual assertion lacks source review')
            for proof in proofs:
                if proof.get('sha256')!=src['source_pdf_sha256']:
                    raise ValueError('Assertion source belongs to another card')
                validate_source_support(proof)
            assertion_count+=1
        assertion_text=compact('\n'.join(q for assertion in assertions for q in assertion['answer_quotes']))
        if any(compact(text[span['start']:span['end']]).strip(' .,!?;:·…')not in assertion_text
                for span in spans if span['card_key']==key and text[span['start']:span['end']].strip()):
            raise ValueError('Card-specific prose segment has no source/input-bound assertion quote')
        if role=='explicitly_rejected_or_comparison':
            comparisons.append(key);comparison_condition_count+=len(facts)
        else:
            active.append(key);all_conditions.extend(facts)
    if any(value['seen']!=value['expected']for value in shared_contexts.values()):
        raise ValueError('Not every shared-predicate participant has its own source/input assertion audit')
    # The original sealed scorer still checks all raw/current/reused answer bindings.
    augmented=copy.deepcopy(review);augmented['prose_spans']=spans
    score=score_frozen(job,answer,augmented,retrieval_review,group,answer_by_card=answer_by_card)
    supported=reflected=correct_without_input=0;omitted=[]
    for fact in all_conditions:
        key=fact['card_key'];src=next(f for f in source[key]['facts']if f['condition_id']==fact['condition_id'])
        in_input=src['source_status']=='verified'and src['input_status']=='supported'
        supported+=in_input;reflected+=in_input and fact['answer_status']=='supported'
        correct_without_input+=fact['answer_status']=='supported'and not in_input
        if in_input and fact['answer_status']!='supported':omitted.append(key+':'+fact['condition_id'])
    return {**score,'input_supported_conditions':supported,'grounded_reflected_conditions':reflected,
        'answer_reflection':reflected/supported if supported else None,
        'correct_without_input':correct_without_input,'generation_omissions':omitted,
        'manual_review_sha256':digest(review),'active_card_keys':sorted(active),
        'comparison_card_keys':sorted(comparisons),'prose_partition_segments':len(audit['segments']),
        'audited_card_count':len(checks),'audited_assertion_count':assertion_count,
        'comparison_condition_count':comparison_condition_count,
        'prose_audit_policy':'All prose covered and all used/comparison cards source/input audited; comparisons excluded only from success/reflection denominators.'}
