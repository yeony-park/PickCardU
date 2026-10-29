"""Join explicit source-review decisions to frozen saved answer conditions.

Verdicts are supplied by reviewers, never inferred from a word match or old
gold-card membership. Missing, duplicate, stale or contradictory records stop
aggregation rather than silently receiving a passing default.
"""
import importlib.util
from pathlib import Path
import sys
from collections import Counter

spec=importlib.util.spec_from_file_location('summary43_io',Path(__file__).with_name('43_offline_validation.py'))
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)

def summarize():
    C.R.load('summary43_binding','43_review_binding.py').verify_reviews(C.PATH)
    packets=C.R.read(C.PATH/'review_packets.json')
    assignments=C.R.read(C.PATH/'review_assignments.json')
    contract=C.R.read(C.PATH/'review_contract.json')
    source=C.R.read(C.R.DATA/'42_expanded_query_evaluation/combined_results.json')
    if assignments['source_results_sha256']!=C.R.digest((C.R.DATA/'42_expanded_query_evaluation/combined_results.json').read_bytes()):
        raise RuntimeError('Old result source changed')
    reviews=[]
    for name in ['main_review.json','independent_review.json']:
        value=C.R.read(C.PATH/name)
        if value['contract_sha256']!=contract['sha256']:
            raise RuntimeError('Review contract mismatch')
        reviews.extend(value['records'])
    index={r['packet_id']:r for r in reviews}
    packet_index={p['packet_id']:p for p in packets['records']}
    if len(index)!=len(reviews) or set(index)!=set(packet_index):
        raise RuntimeError('Review contains duplicate/missing/unexpected packets')
    raw={(r['query_id'],r['profile'],r['mode']):r for r in source['answers']}
    rows=[]
    for assignment in assignments['records']:
        packet=packet_index[assignment['packet_id']]
        review=index[assignment['packet_id']]
        if review['verdict'] not in ['PASS','PARTIAL','FAIL','UNCERTAIN'] or not isinstance(review['wrong_core_recommendation'],bool):
            raise RuntimeError('Invalid source-review classification')
        if not review.get('finding_ko') or not review.get('references'):
            raise RuntimeError('Missing review reasoning/source references')
        evidence={(e['card_key'],e['chunk_id'],e['page_num']) for e in packet['evidence']}
        if any((e['card_key'],e['chunk_id'],e['page_num']) not in evidence for e in review['references']):
            raise RuntimeError('Review source reference is not packet-owned')
        key=assignment['query_id'],assignment['profile'],assignment['mode']
        if C.R.digest(raw[key])!=assignment['answer_sha256']:
            raise RuntimeError('Assignment answer identity changed')
        rows.append({**assignment,**review,'group':packet['review_requirement']['group'],
            'diagnostic':bool(packet['review_requirement']['expected_behavior']),
            'old_known_positive_complete':raw[key]['scores']['known_positive_complete']})
    modes=['U','S_THEN_U','S_PLUS_U']
    metrics=[]
    for group in ['all','numeric','proper_noun','semantic']:
        for mode in modes:
            selected=[r for r in rows if r['mode']==mode and not r['diagnostic'] and (group=='all' or r['group']==group)]
            counts=Counter(r['verdict'] for r in selected)
            metrics.append({'group':group,'mode':mode,'n':len(selected),'PASS':counts['PASS'],'PARTIAL':counts['PARTIAL'],
                'FAIL':counts['FAIL'],'UNCERTAIN':counts['UNCERTAIN'],
                'wrong_core_recommendation':sum(r['wrong_core_recommendation'] for r in selected),
                'old_automatic_complete':sum(r['old_known_positive_complete'] or 0 for r in selected)})
    lookup={(r['query_id'],r['mode']):r for r in rows}
    ids=sorted({r['query_id'] for r in rows if not r['diagnostic']})
    pairs=[]
    for baseline,candidate in [('U','S_THEN_U'),('S_THEN_U','S_PLUS_U')]:
        wins,losses,ties=[],[],[]
        for q in ids:
            a=lookup[q,baseline]['verdict']=='PASS'
            b=lookup[q,candidate]['verdict']=='PASS'
            (wins if b>a else losses if b<a else ties).append(q)
        pairs.append({'baseline':baseline,'candidate':candidate,'wins':wins,'losses':losses,'ties':ties,
            'wrong_core_error_increase':sum(lookup[q,candidate]['wrong_core_recommendation'] for q in ids)>
                sum(lookup[q,baseline]['wrong_core_recommendation'] for q in ids)})
    result={'conditions':rows,'metrics':metrics,'paired':pairs,'positive_denominator':44,
        'diagnostics':[{'mode':m,'pass':sum(r['verdict']=='PASS' for r in rows if r['mode']==m and r['diagnostic']),
                        'n':4} for m in modes],
        'review_binding':{'packets_sha256':C.R.digest(packets),'assignments_sha256':C.R.digest(assignments),
            'review_contract_sha256':contract['sha256'],'reviews_sha256':C.R.digest(reviews)},
        'posthoc':True,'independent_holdout':False,'production_adopted':False,'old_scores_changed':False,
        'interpretation':'사후 핵심요구 판정은 자동평가의 오탐/누락 진단. 모든 부수 문장의 사실 정확도 또는 미래 질문 성공률은 아님'}
    C.R.write(C.PATH/'answer_review_summary.json',result)
    return result

if __name__=='__main__':
    print(summarize()['metrics'])
