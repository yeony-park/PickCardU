"""Prepare immutable saved-answer packets; no models, APIs, DB writes or server.

The helper owns content/mtime preservation and packet identity for reproducible
offline review. Notebook-only execution cannot enforce these cross-file binds.
"""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('offline43_runtime', Path(__file__).with_name('41_followup_runtime.py'))
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)
PATH = R.DATA / '43_offline_validation'
R.STAGES[43] = PATH

def initialize():
    runtime = R.Runtime(43)
    runtime.verify()
    return runtime

def prepare():
    runtime = initialize()
    previous = R.DATA/'42_expanded_query_evaluation'
    binding = R.load('offline43_binding','42_input_binding.py')
    binding.verify_inputs(previous)
    audit = R.read(previous/'completion_audit.json')
    assert audit['passed'] and audit['all_scores_recalculated']
    source = R.read(previous/'combined_results.json')
    assert audit['full_matrix_rows']==384
    dataset = R.read(previous/'dataset.json')
    modes = ['U','S_THEN_U','S_PLUS_U']
    look = {(r['query_id'],r['profile'],r['mode']):r for r in source['retrievals']}
    cases = {q['query_id']:q for q in dataset['queries']}
    numeric = {
        'E01':(['GS25'],'discount',10),'E02':(['CU'],'earn',7),'E03':(['디즈니플러스'],'discount',5),
        'E04':(['멜론'],'discount',7),'E05':(['네이버쇼핑'],'discount',5),'E06':(['백화점'],'discount',5),
        'E07':(['병원'],'discount',10),'E08':(['약국'],'discount',5),'E09':(['다이소'],'earn',7),
        'E10':(['SSG.COM'],'discount',10),'E11':(['교보문고'],'discount',5),'E12':(['커피'],'discount',10),
        'E13':(['커피'],'discount',10),'E14':(['GS25','넷플릭스'],'discount',10),
        'E15':(['병원','약국'],'discount',5),'E16':(['멜론','네이버쇼핑'],'discount',7)}
    requirements = []
    for q in dataset['queries']:
        requirement = {'query_id':q['query_id'],'question':q['question'],'task':q['task'],
            'core_ko':'원질문에 요청된 혜택 대상·행동을 소유 근거로 입증하고 유보/부정으로 철회하지 않아야 함',
            'expected_behavior':q['expected_behavior'], 'group':q['subgroup']}
        if q['query_id'] in numeric:
            targets, action, rate = numeric[q['query_id']]
            requirement.update(targets=targets, action=action, min_percent=rate,
                target_logic='OR' if q['query_id']=='E16' else 'AND', same_card=True,
                max_previous_month_spend_krw=300000 if q['query_id']=='E12' else None,
                min_monthly_discount_cap_krw=5000 if q['query_id']=='E13' else None)
        elif q['task']=='detail':
            requirement.update(explicit_card_name=q['question'].split('의 ')[0],
                core_ko='명시 카드의 요청 혜택율/정액과 적용 조건 적어도 하나를 소유 근거로 설명. 전월실적·한도·결제방법·대상제외·발급특례 중 원문지원 조건. 부수 문장 모두를 검증한 완전정확성 지표는 아님')
        requirements.append(requirement)
    review_contract = R.freeze(PATH/'review_contract.json', {
        'requirements':requirements, 'priority':['stored_ocr_page','owned_retrieval_evidence','structured_fact','answer_wording'],
        'verdicts':{'PASS':'핵심요구를 하나 이상의 소유근거 추천/명시카드가 충족',
            'PARTIAL':'관련혜택이 있으나 핵심 대상/행동/조건 일부를 입증하지 못함',
            'FAIL':'핵심혜택을 찾지 못했거나 요구와 다른 혜택만 답함',
            'UNCERTAIN':'질문/자료/조건 관계가 불명확하여 통과로 합산하지 않음'},
        'wrong_core_recommendation_guard':'다른 추천의 핵심혜택 오주장도 별도 기록. PASS 증가와 함께 이 오류가 증가하면 우세 판단 금지',
        'negation':'자료에 없다는 유보는 실제 혜택의 전역 부재가 아님. 핵심대상 미확정/철회는 문자열 존재로 PASS하지 않음',
        'generic_scope':'모든 가맹점 기본혜택은 관련 업종의 명시 혜택과 구분. 적용 제외/분류 미확정이면 요청의 구체 혜택 충족으로 확대하지 않음',
        'diagnostic_expectations':'일반 조언·예산으로 무조건최적 확정 금지·미확인상점/499%의 근거 없는 긍정 금지',
        'posthoc':True,'independent_holdout':False,'human_blind_review':False,'prior_context_bias_possible':True,
        'old_scores_changed':False,'new_api_requests':0})
    answers = [r for r in source['answers'] if r['profile']=='card_page_section_benefit' and r['mode'] in modes]
    assert len(answers)==144
    assignments, packets = [], {}
    for answer in answers:
        qid = answer['query_id']
        retrieval = look[qid,answer['profile'],answer['mode']]
        assert answer['retrieval_sha256']==R.digest({k:v for k,v in retrieval.items() if k not in ['metrics','subgroup']})
        evidence = [{k:v for k,v in e.items() if k not in ['rank','score']} for e in retrieval['result']['evidence']]
        packet = {'query_id':qid,'question':cases[qid]['question'],'answer':answer.get('answer'),
                  'evidence':sorted(evidence,key=lambda e:e['chunk_id']),
                  'review_requirement':next(r for r in requirements if r['query_id']==qid)}
        packet_id = qid+'_'+R.digest(packet)[:12]
        packets[packet_id] = {'packet_id':packet_id,**packet}
        assignments.append({'query_id':qid,'mode':answer['mode'],'profile':answer['profile'],
            'packet_id':packet_id,'answer_sha256':R.digest(answer),'retrieval_sha256':R.digest(retrieval)})
    # Packet evidence is mode-hidden, not a claim of independently blind review.
    R.freeze(PATH/'review_packets.json', {'records':sorted(packets.values(),key=lambda r:r['packet_id']),
        'contract_sha256':review_contract['sha256'],'mode_and_old_score_hidden':True})
    R.freeze(PATH/'review_assignments.json', {'records':assignments,'source_results_sha256':R.digest((previous/'combined_results.json').read_bytes())})
    R.freeze(PATH/'protocol.json', {'stage':43,'answer_conditions':144,'positive_questions':44,'diagnostic_questions':4,
        'modes':modes,'profiles':['card_page_section_benefit'],'new_api_requests':0,
        'independent_holdout':False,'review_contract_sha256':review_contract['sha256'],
        'sources':{str(p.relative_to(R.ROOT)):R.digest(p.read_bytes()) for p in [previous/'combined_results.json',previous/'parser_results.json',previous/'dataset.json',
            R.DATA/'30_structured_benefit_db/facts.jsonl',R.DATA/'30_structured_benefit_db/documents.jsonl',
            R.ROOT/'notebooks/30_build_structured_db.py',R.ROOT/'notebooks/32_search_experiment.py',R.ROOT/'notebooks/42_expanded_query_evaluation.py']}})
    return {'answer_conditions':len(answers),'unique_review_packets':len(packets),'new_api_requests':0}

if __name__=='__main__':
    print(prepare())
