"""Validate manual source/input judgments; never infer truth from keyword hits.

Actual-card conditions, rather than a single preferred reference card, define
complete evidence. Source semantics remain a separately reviewed human/agent
judgment; this helper verifies identities, quotations and aggregation only.
"""
from collections import defaultdict
import hashlib
import json
import statistics


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def score_retrieval(job, review, group, *, validate_source, source_pdf_sha_by_card):
    if group not in ('explicit','general','uncertain'):
        raise ValueError('Unknown question group')
    if review.get('identity')!=job['identity'] or review.get('result_sha256')!=digest(job['result']):
        raise ValueError('Source review is not bound to the actual retrieval')
    if not review.get('reviewed_by') or not review.get('reason'):
        raise ValueError('Unreviewed manual annotation')
    cards=job['result']['cards'];reviews=review['card_reviews']
    if len(cards)>3:
        raise ValueError('Result exceeds the frozen Top3 card budget')
    keys=[c['card_key']for c in cards];review_keys=[c['card_key']for c in reviews]
    if len(keys)!=len(set(keys)) or len(review_keys)!=len(set(review_keys)) or set(keys)!=set(review_keys):
        raise ValueError('Actual-card source review set is incomplete or duplicate')
    by_key={c['card_key']:c for c in reviews};texts=defaultdict(str)
    for row in job['result']['evidence']:
        texts[row['card_key']]+='\n'+row['text']
    if set(texts)-set(keys):
        raise ValueError('Evidence has an unaudited card outside returned Top3')
    valid_ranks=[];possible_ranks=[];complete=[];counts=[]
    for rank,card in enumerate(cards[:3],1):
        audit=by_key[card['card_key']];relevant=audit['relevant']
        expected_pdf = source_pdf_sha_by_card[card['card_key']]
        if audit.get('source_pdf_sha256') != expected_pdf:
            raise ValueError('Card source document ownership mismatch')
        if relevant is not None and type(relevant)is not bool:
            raise ValueError('Source relevance must be true/false/uncertain')
        if not audit.get('reason'):
            raise ValueError('Card source judgment lacks its reason')
        if relevant is True:valid_ranks.append(rank)
        if relevant is not False:possible_ranks.append(rank)
        facts=audit['facts'];ids=[f['condition_id']for f in facts]
        if len(ids)!=len(set(ids)) or relevant is True and not facts:
            raise ValueError('Relevant card requires unique source conditions')
        supported=verified=0;all_supported=bool(facts)
        for fact in facts:
            if fact['source_status'] not in ('verified','uncertain') or fact['input_status'] not in ('supported','absent','ambiguous','contradicted','unknown','not_applicable'):
                raise ValueError('Invalid or unreviewed condition status')
            if fact['source_status']=='verified':
                if not fact['source_support']:
                    raise ValueError('Verified condition lacks PDF support')
                for proof in fact['source_support']:
                    if proof.get('sha256') != expected_pdf:
                        raise ValueError('Another card source cannot support this condition')
                    validate_source(proof)
                verified+=1
            if fact['input_status']=='supported':
                if not fact['input_quotes'] or not all(q and q in texts[card['card_key']]for q in fact['input_quotes']):
                    raise ValueError('Supported quote missing from this card input')
                if fact['source_status']!='verified':
                    raise ValueError('Source-uncertain condition cannot count as supported')
                supported+=1
            else:
                all_supported=False
        complete.append(relevant is True and all_supported and verified==len(facts))
        counts.append(dict(card_key=card['card_key'],source_verified=verified,input_supported=supported,
                           complete=complete[-1],source_relevant=relevant))
    return {**{k:job[k]for k in ('identity','query_id','profile','mode','repeat')},'group':group,
        'complete_evidence':None if group=='uncertain'else any(complete),
        'hit_at3':float(bool(valid_ranks)) if group=='explicit'else None,
        'mrr_at3':1/min(valid_ranks) if group=='explicit'and valid_ranks else 0. if group=='explicit'else None,
        'hit_at3_upper':float(bool(possible_ranks)) if group=='explicit'else None,
        'mrr_at3_upper':1/min(possible_ranks) if group=='explicit'and possible_ranks else 0. if group=='explicit'else None,
        'card_condition_counts':counts,'manual_review_sha256':digest(review)}


def aggregate(rows, *, group_by_query, profiles, modes, repeats=(1,2,3)):
    expected={(q,p,m,r)for q in group_by_query for p in profiles for m in modes for r in repeats}
    actual=[(row['query_id'],row['profile'],row['mode'],row['repeat'])for row in rows]
    if len(actual)!=len(set(actual)) or set(actual)!=expected:
        raise ValueError('Missing, duplicate or extra query/profile/mode/repeat in evaluation')
    if len({r['identity']for r in rows})!=len(rows) or any(
        row['identity']!=f"{row['query_id']}:{row['profile']}:{row['mode']}:r{row['repeat']}" or
        row['group']!=group_by_query[row['query_id']] for row in rows):
        raise ValueError('Changed evaluation identity or question group')
    groups=defaultdict(list)
    for row in rows:groups[row['profile'],row['mode'],row['group']].append(row)
    out=[]
    for (profile,mode,group),items in sorted(groups.items()):
        queries=defaultdict(list)
        for row in items:queries[row['query_id']].append(row)
        result=dict(profile=profile,mode=mode,group=group,questions=len(queries),logical_rows=len(items))
        for field in ('complete_evidence','hit_at3','mrr_at3','hit_at3_upper','mrr_at3_upper'):
            qmeans=[statistics.mean(float(r[field])for r in repeated if r[field]is not None)
                    for repeated in queries.values()if any(r[field]is not None for r in repeated)]
            result[field+'_macro']=statistics.mean(qmeans)if qmeans else None
        out.append(result)
    return out
