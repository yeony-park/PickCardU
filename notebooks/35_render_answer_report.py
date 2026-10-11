"""Budget matrix, faithful retry diagnosis and measured request accounting."""
from collections import defaultdict
from contextlib import redirect_stdout
import html
import importlib.util
import io
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('budget_report_common', Path(__file__).with_name('34_followup_common.py'))
F = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = F
spec.loader.exec_module(F)
R = F.load_module('bundle_report_helpers', F.ROOT / 'notebooks/34_render_bundle_report.py')
LABELS = {'2400': '기본2400', 'adaptive': '잘림 확인 시2400→4800', '4800': '처음부터4800',
          'card_page_section_benefit': 'benefit / 혜택 청킹', 'parent_child_bundle': 'parent-child / 관계 묶음'}


def guide():
    rows = [
        ['Answer Success / 답변 실행 성공', '전체 질문 시도 중 schema·원래 검증을 통과해 답변 또는 근거 부족 응답이 반환된 비율. 높을수록 좋음.', '편의점 질문에서 잘리지 않은 JSON 응답을 반환하면 실행 통과.', '사실·조건 정확도를 의미하지 않음.'],
        ['Known Positive Complete / 정답 카드와 조건 동시 충족', '알려진 양성 질문이 분모. 같은 정답 추천 카드의 조건 구성요소·인용 소유권·비율값 확인을 모두 통과하면1. 실패0, 높을수록 좋음.', '편의점10%와 실적30만원 조건을 같은 정답 카드에 설명해야 통과.', '문자열 기반·부분 정답 목록. 상세 질문의 정상 빈 추천 목록도0점이며 실제 의미적 정확도가 아님.'],
        ['Constraint Coverage / 조건 설명 구성요소', '구조화 조건 질문에서 추천 카드별 대상·행동·숫자·실적·한도 구성요소 통과 비율을 질문별 평균. 높을수록 좋음.', '할인율만 맞고 실적이 빠지면1 미만.', '다른 문장의 조건·계산·바꿔 말하기를 놓칠 수 있음.'],
        ['Unsupported Percent / 인용 비율 미확인', '비율을 말한 claim 중 같은 카드 인용에서 비율값을 못 찾은 claim 수. 낮을수록 좋고0이면 모두 숫자 확인.', '인용10%만 있는데 답변15%면 미확인.', '기본1%+추가0.5%의 계산값1.5%도 미확인으로 잡힐 수 있어 허위 사실 판정과 다름.'],
        ['Citation Ownership / 인용 소유권', '정상 답변에서 제공된 같은 카드 근거만 인용한 비율. 높을수록 좋고 모두 맞아야1.', 'A카드 설명에 B카드 근거를 붙이면 실패.', '인용 내용을 정확하게 해석했는지까지 검증하지 않음.'],
        ['Provider Requests / 새 API 요청', '현재35 캐시에 저장한 신규 실제 요청의 고유 key 수. 실패도 포함하며 낮을수록 자원 부담 작음.', '동일 요청을6조건에서 캐시 재사용하면 신규 요청은1회.', '논리 답변 행 수·과거 캐시 요청 수와 다름.'],
        ['Tokens / 사용 토큰', 'provider가 반환한 input/output/total, input의 cached 및 output의 reasoning 세부값. 실제 신규 요청과 논리 재사용 합계를 구분.', '최대4800이어도 실제 output1200이면 사용량1200을 기록.', '과거 실패 usage는 미측정이고 최대 한도는 실제 소비량·청구액이 아님.'],
        ['Wall Time / 경과 시간', '현재 단일 runner/API 요청의 실제 경과 초. 품질이 같을 때 낮을수록 좋음.', '캐시 재사용0.01초와 신규API10초를 분리해 봄.', '캐시가 섞인 합계이며 실서비스 지연·반복 평균이 아님.'],
    ]
    return '<div class="guide">' + R.table(['지표', '분모·방향·통과', '카드 혜택 예', '한계'], rows) + '</div>'


def aggregate(rows, cases):
    positive = {c['query_id'] for c in cases if c['gold_cards']}
    structured = {c['query_id'] for c in cases if c['plan']['groups']}
    positives = [r for r in rows if r['query_id'] in positive]
    conditions = [r for r in rows if r['query_id'] in structured]
    return {'attempts': len(rows), 'successes': sum(r['status']=='ok' for r in rows),
        'positive_n': len(positives), 'structured_n': len(conditions),
        'complete': sum(r['scores'].get('known_positive_complete') or 0 for r in positives)/len(positives) if positives else None,
        'coverage': sum(r['scores'].get('constraint_coverage') or 0 for r in conditions)/len(conditions) if conditions else None,
        'unsupported': sum(r['scores'].get('unsupported_percent_claims',0) for r in rows),
        'logical_tokens_known': sum(e.get('usage',{}).get('total_tokens',0) for r in rows for e in r['events']),
        'logical_unknown_usage': sum(not e.get('usage') for r in rows for e in r['events'])}


def token_totals(records):
    totals = defaultdict(int)
    unknown = 0
    for r in records:
        usage = r.get('usage') or {}
        if not usage:
            unknown += 1
        for name in ['input_tokens','output_tokens','total_tokens']:
            totals[name] += usage.get(name,0)
        totals['cached_input_tokens'] += (usage.get('input_tokens_details') or {}).get('cached_tokens',0)
        totals['reasoning_tokens'] += (usage.get('output_tokens_details') or {}).get('reasoning_tokens',0)
    return {**dict(totals),'usage_missing_requests':unknown}


def render():
    base = F.STAGES[35]
    result = F.read_json(base/'answer_results.json')
    if len(result['records']) != 594:
        raise RuntimeError('Incomplete answer budget grid')
    cases = F.read_json(F.OLD[32]/'dataset_freeze.json')['queries']
    protocol = F.read_json(base/'protocol.json')
    selected = result['selection']['selected_budget_policy']
    replay = F.read_json(base/'offline_retry_replay.json')
    content = '<h1>35. 답변 재시도와 출력 토큰 예산 비교</h1><p class="note">34에서 선택한 토큰 확인·최대5개 parent-child와 기존 benefit의 검색 근거를 고정했습니다. 운영 프롬프트·스키마·소스는 그대로이며 새 요청은 gpt-6-luna / reasoning medium입니다. OCR 통과 가정과 현재 제목 계층을 유지합니다.</p>'
    content += '<h2>왜 실행했는가·확인할 질문</h2><p>기존 답변 실패 중 캐시가 ValidationError / JSON 검증 오류를 RuntimeError / 일반 실행 오류로 바꿔 원래 두 번째 시도를 생략한 영향을 먼저 분리합니다. 이어 기본2400, 잘림 확인 시2400→4800, 처음부터4800을 비교하여 추가 예산이 품질·실행·자원에 미치는 영향을 확인합니다.</p>'
    content += '<h2>변경·고정 조건과 주의점</h2><p>과거 실패행의 원래 재시도를 먼저 같은2400으로 오프라인 재현했습니다. 새로운 예산 비교에서 raw status / 원래 응답 상태가 없는 실패 요청은 같은2400으로 한 번 다시 호출해35 캐시에 저장합니다. 기본2400과 조건부 재시도는 동일 첫 응답을 공유합니다. 처음부터4800은 별도 요청·응답이므로 단일 실행의 응답 변동이 섞이며 순수 토큰 증가의 인과 효과가 아닙니다. 같은2400의 새 호출만으로 성공한 경우도 예산 증가 효과로 해석하지 않습니다.</p>'
    content += '<p>조건부 재시도는 status=incomplete / 미완료와 incomplete_details.reason=max_output_tokens / 출력 예산 도달이 실제 원래 응답에 있는 경우만 원래 두 번째 프롬프트에서4800을 사용합니다. 원인 미확정 EOF / JSON 끝 잘림, 거절·필터·네트워크 오류 때문에 한도를 올리지 않습니다. 확인된 잘림인데 JSON이 비어 있는 경우는 실험 프록시에서 검증 오류로 연결해 원래 재시도 경로를 사용합니다. 운영 소스는 수정하지 않았습니다. 응답 상태·토큰은 SDK JSON 해석 전에 저장했습니다. <a href="'+protocol['official_source']+'">OpenAI 공식 구조화 출력 안내</a>를 참고했습니다.</p>'
    content += '<p>개발20질문×6검색 조건에서 설정을 선택·해시 동결한 뒤 나머지13개를 확인했습니다. 모두 이미 본 질문이며 독립 확증·holdout이 아닙니다. 출력 한도는 실제 사용량과 다릅니다. 서비스의 하드코딩된2400 메타데이터와 실험 실제 시도별 한도는 원문에서 별도로 기록합니다.</p>'
    content += '<h2>동일2400에서 과거 캐시 재시도 복구</h2>'
    content += R.table(['질문','프로필','검색','원래 결과','faithful retry / 원래 재시도 복원','논리 시도','새 API'],
        [[r['query_id'],LABELS[r['profile']],R.LABELS[r['mode']],'실패','성공' if r['status']=='ok' else '실패',len(r['events']),0] for r in replay['records']])
    content += '<p class="guide">분모는 과거 답변이 실패했고 검색은 성공한6행입니다. 논리 시도는 해당 답변에서 캐시 parse를 호출한 횟수이며 새API는0입니다. Q31이 재시도로 복구돼도 카드 상세 질문에서 추천 목록이 비면 아래 정답 추천 지표는0일 수 있습니다. Q33의 과거 실패 원인은 raw 상태가 없어 미확정입니다.</p>'
    content += guide()
    summaries = []
    for split in ['all','development','confirmatory']:
        cohort = cases if split=='all' else [c for c in cases if c['split']==split]
        ids = {c['query_id'] for c in cohort}
        label = {'all':'전체·사후 진단','development':'설정 선택20개','confirmatory':'선택 후 확인13개'}[split]
        for profile in ['card_page_section_benefit','parent_child_bundle']:
            for mode in ['U','S_THEN_U','S_PLUS_U']:
                for budget in ['2400','adaptive','4800']:
                    rows = [r for r in result['records'] if r['profile']==profile and r['mode']==mode and r['budget_policy']==budget and r['query_id'] in ids]
                    summaries.append({'split':label,'profile':profile,'mode':mode,'budget':budget,**aggregate(rows,cohort)})
    complete_values = sorted({r['complete'] for r in summaries if r['split']=='전체·사후 진단' and r['complete'] is not None},reverse=True)
    classes = ['best' if r['split']=='전체·사후 진단' and r['complete']==complete_values[0] else
               'runner' if len(complete_values)>1 and r['split']=='전체·사후 진단' and r['complete']==complete_values[1] else '' for r in summaries]
    pct = lambda v: '미평가' if v is None else f'{100*v:.2f}%'
    content += '<h2>모든 예산·프로필·검색 조건</h2><p>초록은 전체 조건 동시 충족의 관측 최고, 노랑은 다음 서로 다른 값입니다. 개발 선택이나 운영 채택을 뜻하지 않습니다.</p>'
    content += R.table(['범위','프로필','검색','출력 정책','성공/실행','양성/조건 분모','조건 동시 충족','조건 설명','비율 미확인','논리 사용 토큰·알려진 합'],
        [[r['split'],LABELS[r['profile']],R.LABELS[r['mode']],LABELS[r['budget']],f'{r["successes"]}/{r["attempts"]}',
          f'{r["positive_n"]}/{r["structured_n"]}',pct(r['complete']),pct(r['coverage']),r['unsupported'],r['logical_tokens_known']] for r in summaries],classes=classes)
    content += '<p class="guide">성공/실행은 전체 시도 분모이며 실패는 조건 동시 충족과 조건 설명에서0입니다. 전체 양성 분모27개, 구조화 조건 질문 분모는 각 행에 표시합니다. 비율 미확인은 claim 수로 낮을수록 좋고 조건 지표는 높을수록 좋습니다. 논리 토큰은 캐시 응답도 재사용 횟수만큼 센 알려진 소계이므로 현재 청구 토큰이나 비용이 아닙니다.</p>'
    candidates = result['selection']['candidates']
    content += '<h2>개발 집합 선택 규칙·탈락 이유</h2><p>기본2400에서 통과한 질문·프로필·검색의 조건 동시 충족이 하락하지 않고 비율 미확인이 늘지 않는 후보에서 성공 수→알려진 논리 토큰 소계→사전 동률 순서로 선택했습니다. 과거 실패 토큰이 없으면 정확한 총비용 비교가 불가능하므로 그 한계를 함께 기록합니다.</p>'
    content += R.table(['정책','성공/120','기존 통과 하락 수','비율 미확인','알려진 논리 총토큰','usage 미확인 시도','통과'],
        [[LABELS[r['budget_policy']],r['successes'],len(r['known_positive_losses']),r['unsupported_percent_claims'],
          r['logical_known_total_tokens'],r['unknown_usage_attempts'],'통과' if r['passes'] else '미통과'] for r in candidates])
    content += '<p class="guide">개발20개×6검색=120행이 분모입니다. 기존 통과 하락은 같은 질문의 정답 카드·조건 동시 충족1→0으로 바뀐 행 수로0이어야 통과합니다. usage 미확인은 해당 시도의 토큰 값이 없는 수이며0으로 추정하지 않습니다. 통과 기준이 품질을 우선하며 작은 토큰 소계만으로 실패 설정을 채택하지 않습니다.</p>'
    lookup = {(r['query_id'],r['profile'],r['mode'],r['budget_policy']):r for r in result['records']}
    deltas = []
    for policy in ['adaptive','4800']:
        for profile in ['card_page_section_benefit','parent_child_bundle']:
            for mode in ['U','S_THEN_U','S_PLUS_U']:
                counts = defaultdict(int)
                for case in cases:
                    if not case['gold_cards']:continue
                    a = lookup[(case['query_id'],profile,mode,'2400')]['scores'].get('known_positive_complete') or 0
                    b = lookup[(case['query_id'],profile,mode,policy)]['scores'].get('known_positive_complete') or 0
                    counts['개선' if b>a else '하락' if b<a else '동률'] += 1
                deltas.append([LABELS[profile],R.LABELS[mode],LABELS[policy],27,counts['개선'],counts['하락'],counts['동률']])
    content += '<h2>기본2400 대비 질문별 개선·하락·동률</h2>' + R.table(['프로필','검색','정책','양성 분모','개선','하락','동률'],deltas)
    content += '<p class="guide">같은 알려진 양성27질문을 짝지어 조건 동시 충족1/0의 변화를 셉니다. 합계27이며 높아진 질문과 낮아진 질문을 함께 보여줍니다. 4800은 별도 모델 응답의 변동도 포함합니다. 전체 평균 상승이 특정 질문 하락을 가리지 않도록 개발 선택 표와 함께 봅니다.</p>'
    subgroups = []
    for subgroup in sorted({c['subgroup'] for c in cases}):
        cohort = [c for c in cases if c['subgroup']==subgroup]
        ids = {c['query_id'] for c in cohort}
        for profile in ['card_page_section_benefit','parent_child_bundle']:
            for budget in ['2400','adaptive','4800']:
                rows = [r for r in result['records'] if r['profile']==profile and r['mode']=='S_THEN_U'
                        and r['budget_policy']==budget and r['query_id'] in ids]
                subgroups.append({'subgroup':subgroup,'profile':profile,'budget':budget,**aggregate(rows,cohort)})
    content += '<h2>구조화 선행의 질문 유형별 결과</h2>' + R.table(
        ['유형','프로필','출력 정책','성공/실행','양성 분모','조건 동시 충족','비율 미확인'],
        [[R.SUBGROUPS[r['subgroup']],LABELS[r['profile']],LABELS[r['budget']],f'{r["successes"]}/{r["attempts"]}',
          r['positive_n'],pct(r['complete']),r['unsupported']] for r in subgroups])
    content += '<p class="guide">이 표는 검색 방식을 구조화→비구조화로 고정합니다. 각 유형의 알려진 양성 질문이 분모이며 없으면 미평가입니다. 상세 카드명 응답의 빈 추천 목록도 기존 지표에서0이 될 수 있습니다. 수치·실적·한도·의미·음성 질문을 분리하며 다른 검색 방식의 전체 질문별 값은 원문594행에 보존합니다.</p>'
    provider = [F.read_json(p) for p in (base/'api_cache').glob('*.json')]
    revalidated=[]
    for record in provider:
        if record.get('revalidated_legacy_failure'):
            payload=json.loads(record['request']['input'][0]['content'])
            usage=record.get('usage') or {}
            revalidated.append([payload['standalone_query'],record['request']['max_output_tokens'],
                '성공' if record['status']=='ok' else '실패',usage.get('output_tokens','미측정'),
                (usage.get('output_tokens_details') or {}).get('reasoning_tokens','미측정')])
    content += '<h2>과거 실패 요청을 같은 한도로 새로 확인</h2>' + R.table(
        ['질문','출력 한도','새 요청 결과','사용 출력 토큰','그중 추론 토큰'],revalidated)
    content += '<p class="guide">이 표는 과거 원래 응답 상태가 없는 실패 요청 중 동일2400토큰으로 새로 보낸 고유 요청2개입니다. 출력 토큰은 실제 사용량이고 추론 토큰은 그 안에 포함돼 더하지 않습니다. 그린카드 v2와 올바른FLEX 상세 질문이 같은2400에서 성공했으므로 과거 실패를 한도 부족으로 확정할 수 없습니다. 모델 응답 변동과 과거 캐시 재시도 문제가 섞일 수 있습니다.</p>'
    resource = {'new_provider_requests':len(provider),'new_provider_successes':sum(r['status']=='ok' for r in provider),
                'new_provider_failures':sum(r['status']=='failed' for r in provider),
                'new_provider_tokens':token_totals(provider),
                'new_provider_wall_sum':sum(r.get('wall_seconds',0) for r in provider),
                'runner_wall_seconds':result['wall_seconds'],'raw_failure_revalidations':sum(bool(r.get('revalidated_legacy_failure')) for r in provider),
                'new_embeddings':0,'billed_cost':'미측정','cpu_time':'미측정','peak_memory':'미측정',
                'stage_bytes':sum(p.stat().st_size for p in base.rglob('*') if p.is_file())}
    content += '<h2>현재 신규 요청·실패 원인·자원</h2><pre>' + html.escape(json.dumps(resource,ensure_ascii=False,indent=2)) + '</pre>'
    content += '<p class="guide">위 값은 현재35에서 실제 새로 보낸 요청만의 합계이며 과거30 캐시를 포함하지 않습니다. input / 입력, output / 출력, cached input / 입력 캐시, reasoning / 추론 토큰을 분리합니다. 실패 응답에도 usage가 있으면 합계에 포함하며 없는 것은 미측정입니다. wall은 단일 실제 경과 초, bytes는 저장 공간입니다. 최대4800은 실제 output 소비량과 다르고 실제 청구 비용은 미측정입니다. 반복 평균·표준편차나 운영 지연으로 해석하지 않습니다.</p>'
    failure_counts = defaultdict(int)
    for record in provider:
        if record['status']=='failed':
            failure_counts[record.get('failure_reason') or '원래 응답 사유 미확정'] += 1
    content += '<p>새 실패 요청의 원래 응답 사유: ' + html.escape(json.dumps(dict(failure_counts),ensure_ascii=False)) + '.</p>'
    content += '<h2>최종 정리</h2><p>선택한 실험 출력 정책은 <strong>' + LABELS[selected] + '</strong>이며 운영 채택은 없습니다. 캐시 예외 복원만으로 Q31의3행을 추가 API 없이 복구했습니다. 출력 예산 변화의 개선·하락·탈락은 전체 행렬과 질문별 표에 공개했습니다. 새2400 실패 재검증과4800 단독 응답 변동을 과거 실패 원인이나 순수 토큰 효과로 단정하지 않습니다. OCR 통과 가정·제목 계층·문자열 점수 한계와 비용 미측정이 남습니다. 다음36번은 선택 출력 정책으로 두 프로필×세 검색을 정리하고 정상 실행 간 근거 차이와 확인 불가 카드 후보 보존을 별도로 검증합니다.</p>'
    content += '<p><a href="../../notebooks/35_answer_budget.ipynb">35번 노트북</a> · <a href="../../notebooks/data/35_answer_budget/answer_results.json">전체594행 원문</a> · <a href="../../notebooks/data/35_answer_budget/selection.json">선택·모든 탈락 질문</a> · <a href="../../notebooks/data/35_answer_budget/offline_retry_replay.json">과거 실패 오프라인 재시도</a> · <a href="../../notebooks/data/35_answer_budget/protocol.json">사전 조건·코드 해시</a></p>'
    path = F.ROOT/'docs/report/35_answer_budget_report.html'
    path.write_text('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>35 답변 출력 예산 비교</title><style>'+R.STYLE+'</style></head><body><main>'+content+'</main></body></html>',encoding='utf-8')
    F.write_json(base/'report_summary.json',{'summaries':summaries,'wins':deltas,'subgroups':subgroups,'resources':resource,
        'selected_budget_policy':selected,'html_sha256':F.digest(path.read_bytes())})
    return path


def execute_notebook():
    cells = ["from pathlib import Path\nimport importlib.util,sys,json\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents] if (p/'packages/rag-core').is_dir())\ns=importlib.util.spec_from_file_location('notebook35_common',ROOT/'notebooks/34_followup_common.py');F=importlib.util.module_from_spec(s);sys.modules[s.name]=F;s.loader.exec_module(F)\nF.initialize(35)\nprint('environment',Path(sys.prefix).name)",
        "replay=F.read_json(F.STAGES[35]/'offline_retry_replay.json')\nassert replay['new_provider_requests']==0\nfor row in replay['records']:print(row['query_id'],row['mode'],row['status'],len(row['events']))",
        "result=F.read_json(F.STAGES[35]/'answer_results.json')\nassert len(result['records'])==594\nprint('selected',result['selection']['selected_budget_policy'])\nfor candidate in result['selection']['candidates']:print(candidate)",
        "R=F.load_module('notebook35_report',ROOT/'notebooks/35_render_answer_report.py')\nprint('report',R.render().relative_to(ROOT))\nprint('preservation',F.read_json(F.STAGES[35]/'preservation_verification.json'))"]
    notebook = {'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'display_name':'Python (skn25)','language':'python','name':'python3'},
        'language_info':{'name':'python'},'experiment':{'full_notebook_cells_executed':True,'runner_executed_separately':True}},'cells':[
        {'cell_type':'markdown','id':'intro35','metadata':{},'source':['35 답변 재시도·출력 예산. 장시간 API 작업·exact cache·실패 raw 저장과 재개는35_answer_experiment.py가 담당합니다. 먼저 승인된 skn25에서 `python -B notebooks/35_answer_experiment.py all`을 실행합니다. 이 노트북의 모든 코드 셀은 저장 결과 검사와 보고서 생성이며 유료 실험 전체를 노트북에서 재실행하지 않습니다.']}]}
    namespace={}
    for index,code in enumerate(cells,1):
        out=io.StringIO()
        with redirect_stdout(out):exec(compile(code,f'35_answer_budget.ipynb-cell{index}','exec'),namespace)
        notebook['cells'].append({'cell_type':'code','id':f'code35{index}','metadata':{},'source':code.splitlines(keepends=True),
            'execution_count':index,'outputs':[{'output_type':'stream','name':'stdout','text':out.getvalue().splitlines(keepends=True)}]})
    path=F.ROOT/'notebooks/35_answer_budget.ipynb'
    path.write_text(json.dumps(notebook,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    F.write_json(F.STAGES[35]/'notebook_execution.json',{'code_cells_executed':4,'full_notebook_rerun':True,
        'paid_runner_rerun_in_notebook':False,'environment':Path(sys.prefix).name,'notebook_sha256':F.digest(path.read_bytes())})


if __name__=='__main__':
    F.initialize(35)
    execute_notebook()
    print('35 notebook/report written')
