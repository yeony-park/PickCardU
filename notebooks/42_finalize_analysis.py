"""Attach source-review notes and resource details to saved results, without APIs.

Notes describe inspected question/benefit differences, not a semantic rescore or
an exhaustive audit of every incidental sentence. Frozen scores stay intact.
"""
import importlib.util
import json
from collections import Counter
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('final42_runtime', Path(__file__).with_name('41_followup_runtime.py'))
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)

MAIN_NOTES = {
    'E18': 'benefit의 네 방식은 쿠팡을 포함한 온라인쇼핑/배달앱의 이용액 순위별8%/4%,전월40만원 조건을 회수했다. parent-child의 네 방식은 같은 카드를 찾았지만 쿠팡 대상과 율 근거가 누락돼 답변을 유보했다. 부분 정답 목록 문제가 아닌 혜택 근거 회수 차이다. cf3d2d224f745a60a107b45785e1e72e,2쪽.',
    'E19': 'benefit의 네 방식은 신세계5%,월2만원,전월40만원을 함께 회수했다. parent-child는 전월실적/제외 안내만 회수해 할인율을 확인하지 못했다. ea8817db43c357c3c75579baeaf1e932,1쪽의 혜택 표와 조건을 확인했다. 운영 청킹 계층 미완성과 재정렬 프로필 차이가 모두 남아 있다.',
    'E31': '기본 국내외1.2% 할인과 전월50만원/월300만원 조건은 모든 방식에서 맞다. benefit 보강의 수치 경고는 value1.3을 미등록 합산으로 잡은 것이다. 자기 인용531af9aa61f25c889bffe35b2370f96b,2쪽의 국제브랜드1%와 해외서비스0.3% 및 동일 기준 금액의 수수료 계산식을 확인했다. 새 혜택 수치를 만든 것이 아니다. 자동0점은 보존한다.',
    'E34': '카페 적립 답변은 정답 목록 밖 IBK I-어디로든 그린10%,신한 처음5%,가온 기본0.5%+커피추가0.3%를 제시했다. 소유 인용의 대상/율이 확인된다. benefit 융합의 미지원0.8은 합산 등록 누락이다. 34ec31aa9b8308dc92c52b2114dbc4fd,1쪽의 가온 기본/추가 적립 관계를 확인했다. parent-child 순차는 그린의 구체 율이 누락됐다. 모두 자동0점이지만 모두 무효 추천이라는 뜻은 아니다.',
    'E35': '여행 답변의 일반 검색/순차/융합은 라운지·보험·패키지 근거를 회수했다. parent-child 보강도 트래블로그 항공/여행3%,BLISS.5 예약할인,ALL 우리 Infinite 라운지/보험의 소유 근거를 갖지만 부분 정답 카드 목록 밖이라0점이다. shc_7f50f0f17d8a0ce0cbea77f2,1쪽 여행 표를 확인했다. 실제 의미 퇴행으로 단정하지 않는다.',
    'E38': 'benefit 순차는 Point Plan+ 및KaPick 등 부분 정답 카드를 회수해 통과했다. benefit 융합은 목록 밖 Point Plan의 주말배달 건당2만원 이상1천포인트를 정확히 제시했다(a8752251cd2acb284859fc967eb03c1f,1쪽;774dec613688549bddc8e1dbf4f3c194,2쪽). 융합의 자동 패배는 실제 무효 추천이 아니다. benefit 보강과 parent-child 일반/보강은 배달 적용을 유보한 범용 적립/마일리지만 제시해 근거 구체성이 낮다. parent-child 순차/융합은 Point Plan의 결제 조건을 찾았지만 건당 적립 규모가 빠졌다.',
    'E39': '영화 관련 유효 카드가 정답 목록 밖이거나 영화관 이름으로만 설명되어 자동 실패가 발생했다. benefit 순차와 parent-child 순차의 LOCA LIKIT Play60% CGV/롯데시네마/메가박스 설명은 소유 근거에 맞지만 claim에 영화라는 단어가 없어0점이다. 융합이 영화라는 단어를 넣어 통과한 차이는 실제 신규 혜택 회수 개선과 구분한다. 삼성5v4/iD SIMPLE와신한 Discount Plan+도 소유 인용의 정액 할인 근거를 확인했다.',
    'E41': '통신 적립을 설명한 그린v2(5%),가온(0.3%추가),Point Plan+(2만원당1천포인트) 등은 부분 정답 목록 밖이라 다수0점이다. parent-child 순차는 정답 zgm living의 최대15%를 포함해 통과했다(shc_7a9ff9735fad7f3e24f05a3c,2쪽). parent-child 융합은 같은 카드를 중복 추천해 운영 grounding 검사에서 두 시도 모두 거절됐다. 빈 답변 때문에 소유권/범위0인 실행 실패이며 실제 타 카드 인용 또는 명시 카드 이탈로 판정한 것은 아니다.',
    'E44': '마트 답변은 현대S10%,NH올원더풀5%,zgm shopping5~15% 등 소유 근거에 맞다. 현대S/zgm은 부분 정답 목록 밖이고,NH올원더풀을 하나로마트·이마트·롯데마트 또는 지정마트라고 표현하면 대형마트 문자열 검사에 실패한다. benefit 보강/parent-child 순차·융합의 자동 개선은 이런 표현/정답 카드 선택 영향도 포함한다. 1866d86322b81f00f16fa76e68d0978c,2쪽;shc_8295f646a07e71235cf1e083,2쪽을 확인했다.',
    'E45': '모든 방식은 적용처·실적·한도·포인트 사용 기준을 비교하라는 일반 조언을 제공한다. 예시 신한 처음 적립/즉시결제와디지로카PARIS 최초발급 실적유예는 자기 인용에 있다. 근거가 없는 카드 간 절대 우열을 단정하지 않았다.',
    'E46': '모든 방식은 생활비40만원만으로 무조건 최적카드를 확정할 수 없다고 답하고,총예산과적격전월실적을 구분했다. Point Plan 조건부 예시의 금액 구간/한도는 자기 인용 표에서 확인됐다. 무조건 최적 카드 확정 없음.',
    'E47': '모든 방식은 존재하지않는평가상점ABC의 적용 여부를 근거에서 확인할 수 없다고 유보했다. 그 상점의 할인 카드가 있다고 단정한 답변 없음. 현실에서 혜택이 전혀 없다는 보편 부정으로도 확대하지 않았다.',
    'E48': '모든 방식은499%를 확인할 수 없다고 부정했다. benefit의KB Star B10%와아코르멤버십최대50%,parent-child의PARIS호텔제외/LOCA LIKIT1.2%는 자기 인용에서 확인됐다. 아코르50%를 모든 호텔 카드 결제 할인으로 확대하지 않았다.499%긍정 추천 없음.',
}

def main_records():
    path = R.STAGES[42]
    combined = R.read(path / 'combined_results.json')
    retrievals = {(r['query_id'], r['profile'], r['mode']): r for r in combined['retrievals']}
    dataset = {q['query_id']: q for q in R.read(path / 'dataset.json')['queries']}
    M = R.load('final42_rubric', '39_answer_rubric.py')
    records = []
    for answer in combined['answers']:
        qid = answer['query_id']
        if qid not in MAIN_NOTES:
            continue
        key = qid, answer['profile'], answer['mode']
        result = retrievals[key]['result']
        output = answer.get('answer', {})
        chosen = {c['card_key'] for c in output.get('claims' if dataset[qid]['task']=='detail' else 'recommendations', [])}
        valid = chosen & set(dataset[qid]['gold_cards'])
        witnesses = [e for e in result['evidence'] if any(e['chunk_id'] in cl['citations'] for cl in output.get('claims', []) + output.get('recommendations', []))]
        records.append({'query_id': qid, 'profile': answer['profile'], 'mode': answer['mode'],
            'reviewer': 'main Codex', 'frozen_score': answer['scores']['known_positive_complete'],
            'answer_status': answer['status'], 'finding_ko': MAIN_NOTES[qid],
            'chosen_anchor_cards': sorted(valid), 'chosen_outside_anchor_cards': sorted(chosen-set(dataset[qid]['gold_cards'])),
            'source_references': [{'card_key':e['card_key'],'page_num':e['page_num'],'chunk_id':e['chunk_id']} for e in witnesses],
            'row_sha256': R.digest(answer), 'confirmed_wrong_benefit_in_reviewed_difference': False,
            'inspection_scope': '질문의 핵심 혜택·승패 원인·검증 경고 확인. 모든 부수 문장의 완전한 의미 검증 또는 PDF 재검증이 아님'})
    R.write(path / 'main_quality_review.json', {'records':records, 'scores_changed':False,
        'diagnostic_expectations_passed':32, 'limitations':'OCR 검증 통과 가정 아래 저장 OCR/검색 근거를 읽기 전용으로 대조한 Codex 검토. 사람의 완전한 사실 평가가 아님'})

def resources():
    path = R.STAGES[42]
    combined = R.read(path / 'combined_results.json')
    counts = Counter()
    vectors = 0
    for file in (path/'api_cache').glob('*.json'):
        row = R.read(file)
        request = row['request']
        kind = 'embedding' if request.get('model')=='text-embedding-3-small' else 'parser' if 'groups는 OR' in request.get('instructions','') else 'answer'
        counts[kind,row['status']] += 1
        if kind == 'embedding':
            vectors += len(request['input'])
    expansions = R.read(path/'expansions.json')['records']
    value = {'physical_requests_by_kind_status': [{'kind':k,'status':s,'requests':n} for (k,s),n in sorted(counts.items())],
        'new_embedding_vectors':vectors,
        'answer_cache_hit_events':sum(bool(e.get('cache_hit')) for a in combined['answers'] for e in a.get('events',[])),
        'cache_origin_label':'followup35는 상속된 helper 이름; 이번 stage42 동일 payload cache 재사용. old_cache40 설정은 execution 경로 참조',
        'storage_bytes_excluding_private_indexes_and_execution_cache':sum(f.stat().st_size for f in path.rglob('*') if f.is_file() and 'indexes' not in f.parts and 'execution_cache' not in f.parts),
        'storage_measurement':'최종 노트북/보고서 감사 산출물 생성 전 stage42 저장량 스냅샷',
        'query_expansion_counts':{'original_fallback':sum(bool(e.get('fallback_reason')) for e in expansions.values()), 'applied':sum(not e.get('fallback_reason') for e in expansions.values())},
        'chunks':{'benefit':3183,'parent_child':1834}, 'condition_depth':50,'candidate_depth':20,'top_k':3,
        'model_or_index_configuration_changes':0,
        'same_question_independent_repeats':0,'repeat_variance':'미측정','billed_cost':'미측정','network_transmitted_token_count':'미측정'}
    R.write(path/'resource_details.json',value)

if __name__ == '__main__':
    main_records()
    resources()
