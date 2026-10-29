"""Four linked, Korean experiment reports; only measured artifacts are read."""
from __future__ import annotations
from pathlib import Path
import importlib.util
import sys
import html
from collections import defaultdict,Counter
import statistics

s=importlib.util.spec_from_file_location('structured_common',Path(__file__).with_name('30_structured_search_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)
ESC=lambda v:html.escape(str(v))
STYLE='''body{font:16px/1.7 system-ui,sans-serif;background:#f6f8fb;color:#17233b;margin:0}main{max-width:1180px;margin:40px auto;padding:30px;background:white;border-radius:16px}h1,h2,h3{line-height:1.35}table{border-collapse:collapse;width:100%;margin:20px 0;font-size:14px}td,th{border:1px solid #ccd5e2;padding:10px;text-align:left;vertical-align:top}th{background:#eaf0f8}.note{border-left:5px solid #d89b17;background:#fff7df;padding:15px;margin:18px 0}.guide{background:#eef6ff;padding:16px;border-radius:8px}a{color:#1458aa}code{background:#edf1f6;padding:2px 5px}pre{white-space:pre-wrap;font-size:13px}nav a{margin-right:20px}.num{font-variant-numeric:tabular-nums}footer{margin-top:30px;color:#526078} .best{background:#dcf5e7;font-weight:700}.runner{background:#fff2cc}caption{text-align:left;font-weight:700;padding:8px}'''
NAMES={30:'structured_benefit_db',31:'operational_chunking_comparison',32:'structured_search_comparison',33:'query_routing'}
TITLES={30:'기존 Upstage JSON → 구조화 검색 DB',31:'현재 운영 benefit·parent-child 청킹',32:'두 운영 프로필 × 세 검색 방식',33:'GPT-6 Luna 조건 추출·경로 선택'}

def table(headers,rows,caption=''):
    context=' '.join(headers)
    note=''
    if any(k in context for k in ['Answer Anchor Hit','Known Positive Complete','Constraint Coverage']):
        note='답변 지표는 추천 목록과 claim / 근거를 든 사실 문장을 사용합니다. Answer Anchor Hit는 양성 질문 중 알려진 정답을 추천한 비율입니다. Known Positive Complete는 같은 정답 카드의 요구 조건·인용 소유권·비율 확인까지 통과한 비율이며 양성 질문을 분모로 합니다. Constraint Coverage는 구조화 조건이 있는 질문의 지원 구성요소 비율 평균입니다. 각 분모는 표에 표시하며 실패는0입니다. 높을수록 좋지만 상세 질문에 추천 없이 정확히 답하거나 합산 수치를 쓰면 미탐할 수 있습니다. Citation Ownership은 완료 답변의 인용 소유권만, Negative Abstention은 음성 질문의 근거 부족 상태만 검사합니다. 카드·금융 사실의 의미 정확도는 별도 검증이 필요합니다.'
    elif any(k in context for k in ['Hit','MRR','Witness','Recall']):
        note='Hit/MRR/Anchor Recall의 평균 분모는 알려진 양성 질문 수입니다. Witness 평균 분모는 그중 필수 근거 구성이 주석된 질문 수로, 표의 Witness 분모를 사용합니다. 전체는 각각 27개와 24개이며 부분집합에서는 표의 분모를 확인하세요. 실행 실패는 0입니다. Hit는 정답 카드 포함 여부, MRR은 첫 정답 순위의 역수입니다. Witness는 질문별 알려진 정답 카드 중 필수 원문 구성요소를 한 근거에서 찾은 비율입니다. 높을수록 좋지만 부분 정답 라벨·문자열 진단입니다. 편의점 10% 정답이 2위이면 Hit=1, MRR=0.5이며 실적 근거가 빠지면 Witness를 통과하지 못합니다.'
    elif '승' in headers:
        note='같은 양성 질문의 두 조건을 비교합니다. 새 조건 점수가 높으면 승, 낮으면 패, 같으면 동률입니다. 승이 많아도 다른 질문 유형의 회귀를 함께 확인해야 합니다.'
    elif any(k in context for k in ['분모','일치','오류']):
        note='조건·경로·후보 일치율은 전체 질문 수를 분모로 하며 실패는 불일치입니다. 높을수록 좋습니다. 예를 들어 OR를 AND로 잘못 추출하면 카드 선별 집합이 바뀔 수 있습니다. 동결된 사람 조건도 유일한 정답 해석은 아닙니다.'
    elif any(k in context for k in ['지원율','연결률']):
        note='전체 원본 사실 3,403개 중 확인 가능한 필드 또는 연결된 사실의 비율입니다. 높을수록 좋습니다. 단일 10%가 원문·typed 값과 같으면 known이지만 3~5%는 unknown입니다. OCR 사실 정확도를 검증한 값은 아닙니다.'
    elif any(k in context for k in ['wall','bytes','shape']):
        note='wall은 해당 단계의 실제 경과 초, bytes는 저장 파일 크기입니다. 한 번 실행한 값이며 캐시·CPU 부하가 섞여 실서비스 지연 비교로 쓸 수 없습니다. 품질이 같을 때 적을수록 유리합니다.'
    def html_row(row):
        rank='best' if any(str(v).startswith('관측 1위') for v in row) else 'runner' if any(str(v).startswith('관측 2위') for v in row) else ''
        def display(value):
            if value is None:return '미측정'
            if isinstance(value,bool):return 'True / 예' if value else 'False / 아니오'
            return {'ok':'ok / 성공','failed':'failed / 실패','started':'started / 진행 중'}.get(str(value),value)
        return '<tr class="'+rank+'">'+''.join('<td>'+ESC(display(v))+'</td>' for v in row)+'</tr>'
    return '<table><caption>'+ESC(caption)+'</caption><thead><tr>'+''.join('<th>'+ESC(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(html_row(row) for row in rows)+'</tbody></table>'+('<p class="guide">'+note+'</p>' if note else '')

def percent(value):return '미측정' if value is None else f'{100*value:.2f}%'
def number(value):return '미측정' if value is None else f'{value:.4f}'

def metric_guide():
    return '<div class="guide"><h3>지표 안내 — 분모·방향·통과 예·한계</h3>'+table(['English / 쉬운 뜻','데이터·분모와 방향','통과 예시','핵심 한계'],[
      ['Anchor Hit@3 / 알려진 정답 카드 포함','원문 기준 정답 카드가 하나 이상 있는 평가 질문 수. 실패 실행은 0. 높을수록 좋음.','편의점 10% 질문의 알려진 정답 카드가 Top3에 있으면 1.','정답 카드 목록은 보수적 부분 라벨이다. 추가 정답 카드를 오답으로 판정하지 않음.'],
      ['Anchor MRR / 첫 정답의 순위','같은 양성 질문 수. 첫 정답 순위의 역수, 없으면 0. 높을수록 좋음.','정답 카드가 2위이면 0.5.','정답이 몇 개 더 있는지와 근거 정확도를 말하지 않음.'],
      ['Anchor Recall@3 / 알려진 정답 카드 회수','질문별 알려진 정답 카드 중 Top3에 든 비율을 양성 질문에서 평균. 높을수록 좋음.','알려진 정답 2개 중 1개 검색이면 0.5.','전수 정답 recall이 아니며 많은 정답이 있는 질문은 Top3 상한이 있음.'],
      ['Witness Recall@3 / 필수 원문 구성요소 회수','질문별 알려진 정답 카드 중 필수 원문 필드를 같은 카드의 한 근거에서 찾은 비율을 근거 구성 주석이 있는 양성 질문에서 평균. 전체 평균 분모 24개. 높을수록 좋음.','편의점 10%와 요구한 전월 실적 조건까지 근거에 있어야 통과.','문자열 기반 보수적 진단. 표/문단 분할·바꿔 말하기를 미탐할 수 있고 의미적 관계 정확성을 증명하지 않음.'],
      ['Strict Prefilter Retention / 선별 전 정답 보존','구조화 질의의 알려진 정답 카드 중 strict 선별 집합에 남은 비율. 높을수록 좋음, 1이 손실 없음.','정답 2개 중 1개를 DB 단계에서 제외하면 0.5.','unknown fallback 이후 검색 결과와 다르며 OCR/필드 누락에 영향받음.'],
      ['Answer Anchor Hit / 답변 정답 카드 포함','양성 질문 수. 답변 추천 목록에 알려진 정답이 있으면 1, 실패는 0. 높을수록 좋음.','편의점 혜택의 정답 카드를 답변에서 추천하면 통과.','조건을 잘못 설명한 답변도 카드 포함만으로 통과할 수 있음.'],
      ['Constraint Coverage / 지원하는 요구조건 구성요소','구조화 질문 수. 같은 추천 카드의 대상·행동·비율·실적·한도 구성요소 통과율을 질문별 평균. AND는 요구조건 평균, OR는 최선 그룹. 실패는 0, 높을수록 좋음.','편의점 10%와 실적 30만원 요구에서 비율은 맞지만 실적을 설명하지 않으면 1 미만.','source-relative 문자열 진단. 지역·선택형 팩 동시 이용 등 스키마 밖 조건과 문맥 관계를 입증하지 못함.'],
      ['Known Positive Complete / 알려진 정답과 조건 동시 충족','양성 질문 수. 같은 알려진 정답 추천 카드의 Coverage=1, 인용 소유권 통과, 비율값 근거 확인을 모두 만족하면 1. 실패 0, 높을수록 좋음.','정답 A카드를 추천했어도 B카드만 요구조건을 설명하면 실패.','답변 전체 사실 정확도가 아니며 독립 PDF·LLM 심판 검증 없음.'],
      ['Unsupported Percent Claims / 인용 비율값 미확인','비율을 포함한 claim 수 중 동일 카드 인용에서 비율값이 없는 claim 수. 낮을수록 좋고 0은 모두 숫자 확인됨.','답변 15%가 인용 근거 10%에서만 나왔다면 미확인.','큰 근거의 다른 혜택 숫자가 우연히 일치할 수 있어 의미 검증을 대체하지 못함.'],
      ['Citation Ownership / 인용 소유권','완료된 답변의 claim·recommendation 인용이 모두 제공된 같은 카드의 근거인지 확인. 모두 맞으면 1. 높을수록 좋음.','A카드 claim이 B카드 청크를 인용하면 실패.','의미적 사실 일치를 판정하지 않음. 근거 부족 답변은 인용이 없어 별도 표시.'],
      ['Negative Abstention / 음성 질문 보류','의도적 원문 기준 음성 대조 질문 2개. 근거 부족 응답이면 1, 추천·실패이면 0. 높을수록 좋음.','존재하지않는실험가맹점XYZ 할인에 추천하지 않아야 통과.','시장 전체의 혜택 부재 증명이 아니라 고정 corpus 상대 대조.'],
      ['Plan / Route / Candidate Match / 조건·경로·선별 집합 일치','전체 33개 질문. 동결된 사람 조건과 alias 정규화된 조건·경로·후보 집합을 각각 비교. 높을수록 좋음.','커피와 카페가 같은 alias 집합이면 조건 의미를 같게 처리.','복수 합리적 해석이 가능하며 동결 조건이 유일한 정답은 아님.'],
      ['Fallback / Failed / 보완 검색·실행 실패','전체 시도 수를 분모로 보완 경로·실패를 각각 셈. 오류는 낮을수록 좋고 fallback 자체는 품질 평가가 필요.','실적 필드 확인불가이면 U 보완 사용과 strict 손실을 같이 기록.','실패를 뺀 평균은 성능을 과대평가하므로 전체 분모 결과도 함께 표시.'],
      ['Support / Coverage / 필드 지원·근거 연결','3403개 원본 facts 중 파생값이 known이거나 해당 프로필에서 대상·값 근거를 찾은 비율. 높을수록 좋음.','단일 10%가 typed 값과 같으면 known.','OCR 정확도가 아니고 일반 안내·연회비 facts도 분모에 포함.'],
      ['API Requests / Tokens / 외부 요청·처리 토큰','공유 캐시에 저장한 실제 성공 payload·provider usage의 합계. cached input/reasoning은 그 세부 항목. 품질이 같을 때 적을수록 좋음.','같은 카페 질문과 근거 payload를 재사용하면 신규 요청을 줄임.','단계별 자원량이 아니며 cache-hit 처리와 실패 비용을 완전히 포함하지 않음.'],
      ['Wall Time / Storage / 실행 시간·저장 공간','해당 단계 또는 기록 요청의 실제 경과 초·파일 bytes. 단일 실행이며 품질이 같을 때 낮을수록 좋음.','같은 106문서에서 두 인덱스 파일 크기를 비교.','cold/warm·캐시 재사용이 섞일 수 있고 기록 시간 합계는 사용자 요청 지연과 다름.']
    ])+'</div>'

def api_resources():
    cache=C.STAGES[30]/'api_cache';records=[]
    for path in cache.rglob('*.json'):
        row=C.read_json(path)
        if row.get('request',{}).get('instructions')=='test':continue
        records.append(row)
    successes=[r for r in records if r.get('status')=='ok'];failed=[r for r in records if r.get('status')=='failed']
    tokens=Counter()
    for r in successes:
        u=r.get('usage',{})
        for key in ['input_tokens','output_tokens','total_tokens','prompt_tokens']:tokens[key]+=u.get(key,0) or 0
        tokens['cached_input_tokens']+=(u.get('input_tokens_details') or {}).get('cached_tokens',0) or 0
        tokens['reasoning_tokens']+=(u.get('output_tokens_details') or {}).get('reasoning_tokens',0) or 0
    return {'successful_request_payloads':len(successes),'failure_records_including_archived_recovery':len(failed),'tokens':dict(tokens),
      'ledger_scope':'전체 실험 공유 캐시 누적. 이 보고서 단계의 비용·토큰·시간으로 해석하지 않음.',
      'stage_specific_api_tokens':'미측정','stage_specific_api_cost':'미측정',
      'saved_api_response_wall_seconds':sum(r.get('wall_seconds',0) for r in successes),
      'new_unique_embedding_texts':len(list((cache/'embeddings').glob('*.npy'))) if (cache/'embeddings').exists() else 0,
      'provider_billed_cost':'미측정','gpu':'미사용: skn25 PyTorch CPU','cpu_peak_memory':'미측정',
      'warning':'정확한 payload 캐시는 여러 조건에서 재사용. 조건별 logical 작업량과 신규 API 호출량은 다르며 stage 시간이 전체 요청 latency와 같지는 않음. 실패 응답의 usage가 없는 경우 그 요청의 토큰·비용은 미측정.'}

def aggregate(records,cases):
    grouped=defaultdict(list)
    for r in records:grouped[(r['profile'],r['mode'])].append(r)
    out=[]
    for (profile,mode),rows in sorted(grouped.items()):
        positive=[r for r in rows if cases[r['query_id']]['gold_cards']]
        witnesses=[r for r in positive if cases[r['query_id']].get('witness_groups')]
        def avg(metric,items):
            if not items:return None
            return sum((r.get('metrics',{}).get(metric) or 0) if r['status']=='ok' else 0 for r in items)/len(items)
        out.append({'profile':profile,'mode':mode,'attempted':len(rows),'ok':sum(r['status']=='ok' for r in rows),
                    'failed':sum(r['status']!='ok' for r in rows),'positive_denominator':len(positive),'witness_denominator':len(witnesses),
                    'hit':avg('anchor_hit_at_3',positive),'mrr':avg('anchor_mrr',positive),
                    'recall':avg('anchor_recall_at_3',positive),'witness':avg('witness_recall_at_3',witnesses),
                    'fallback':sum(r.get('fallback',False) for r in rows),
                    'wall':sum(r.get('wall_seconds',0) for r in rows)})
    return out

def search_tables(records,freeze):
    cases={q['query_id']:q for q in freeze['queries']};rows=aggregate(records,cases)
    ranks=sorted({(r['hit'] or 0,r['mrr'] or 0) for r in rows},reverse=True)
    ranktext=lambda r:'관측 '+str(ranks.index((r['hit'] or 0,r['mrr'] or 0))+1)+'위 / 채택 아님'
    body=table(['프로필','검색 방식','관측 Hit·MRR 순위','시도/성공/실패','양성/Witness 분모','Anchor Hit@3','MRR','Anchor Recall@3','Witness Recall@3','Fallback','기록 wall초'],
               [[r['profile'],r['mode'],ranktext(r),f"{r['attempted']}/{r['ok']}/{r['failed']}",f"{r['positive_denominator']}/{r['witness_denominator']}",percent(r['hit']),number(r['mrr']),percent(r['recall']),percent(r['witness']),r['fallback'],round(r['wall'],2)] for r in rows],
               '전체 6조건 — 실패를 0으로 포함한 양성 질문 평균')
    paired=[];lookup={(r['query_id'],r['profile'],r['mode']):r for r in records}
    for profile in ['card_page_section_benefit','parent_child_bundle']:
        for mode in ['S_THEN_U','S_PLUS_U']:
            wins=losses=ties=0
            for q in cases.values():
                if not q['gold_cards']:continue
                a=lookup.get((q['query_id'],profile,'U'),{});b=lookup.get((q['query_id'],profile,mode),{})
                av=a.get('metrics',{}).get('anchor_hit_at_3') or 0;bv=b.get('metrics',{}).get('anchor_hit_at_3') or 0
                wins+=bv>av;losses+=bv<av;ties+=bv==av
            paired.append([profile,mode,wins,losses,ties])
    body+=table(['프로필','U 대비','승','패','동률'],paired,'질문별 Anchor Hit 승·패·동률')
    pairs=[]
    for q in cases.values():
        if not q['gold_cards']:continue
        for profile in ['card_page_section_benefit','parent_child_bundle']:
            vals=[]
            for mode in ['U','S_THEN_U','S_PLUS_U']:
                row=lookup.get((q['query_id'],profile,mode),{})
                vals.append(f"{row.get('metrics',{}).get('anchor_hit_at_3') or 0}/{number(row.get('metrics',{}).get('anchor_mrr') or 0)}")
            pairs.append([q['query_id'],profile,*vals])
    body+=table(['질의','프로필','U Hit/MRR','선행 Hit/MRR','융합 Hit/MRR'],pairs,'양성 질문별 카드 포함·순위 — 개별 회귀 확인')
    for split in ['development','confirmatory']:
        subset=[r for r in records if cases[r['query_id']]['split']==split]
        split_rows=aggregate(subset,cases)
        body+=table(['프로필','방식','시도/실패','양성 분모','Witness 분모','Hit@3','Witness'],[[r['profile'],r['mode'],f"{r['attempted']}/{r['failed']}",r['positive_denominator'],r['witness_denominator'],percent(r['hit']),percent(r['witness'])] for r in split_rows],split+' / '+('개발' if split=='development' else '고정 설정 확증: 독립 PDF gold 아님'))
    subgroups=sorted({q['subgroup'] for q in cases.values()})
    subgroup_rows=[]
    for subgroup in subgroups:
        subset=[r for r in records if cases[r['query_id']]['subgroup']==subgroup]
        for r in aggregate(subset,cases):subgroup_rows.append([subgroup,r['profile'],r['mode'],r['attempted'],r['failed'],r['positive_denominator'],r['witness_denominator'],percent(r['hit']),percent(r['witness'])])
    body+=table(['질문 유형','프로필','방식','시도','실패','양성 분모','Witness 분모','Hit@3','Witness'],subgroup_rows,'유형별 결과 — 숫자/조건/AND/OR/여행/고유명사/범용/음성')
    body+=table(['질의','유형','split','질문','알려진 정답 수'],[[q['query_id'],q['subgroup'],q['split'],q['question'],len(q['gold_cards'])] for q in cases.values()],'동결 질의 목록')
    failures=[r for r in records if r['status']!='ok']
    if failures:body+=table(['질의','프로필','방식','실패 사유'],[[r['query_id'],r['profile'],r['mode'],r['error']] for r in failures],'실패 레코드 전체')
    return body

def final_decision(records,freeze):
    cases={q['query_id']:q for q in freeze['queries']};rows=aggregate(records,cases)
    bykey={(r['profile'],r['mode']):r for r in rows};statements=[]
    for profile in ['card_page_section_benefit','parent_child_bundle']:
        baseline=bykey[(profile,'U')]
        for mode in ['S_THEN_U','S_PLUS_U']:
            candidate=bykey[(profile,mode)]
            statements.append(f"{profile} / {mode}: U 대비 Hit {100*((candidate['hit'] or 0)-(baseline['hit'] or 0)):+.2f}%p, MRR {(candidate['mrr'] or 0)-(baseline['mrr'] or 0):+.4f}, Witness {100*((candidate['witness'] or 0)-(baseline['witness'] or 0)):+.2f}%p. 실행 실패 {baseline['failed']}→{candidate['failed']}건.")
    return '<h2>최종 정리·채택 판단</h2><ul>'+''.join('<li>'+ESC(s)+'</li>' for s in statements)+'''</ul><p>위 수치는 단일 고정 실행의 관측 결과입니다. 관측 최고값과 차순위는 품질 우승 후보이며 채택 설정이 아닙니다. 운영 채택은 없음. 검색 후속 검증의 우선 후보는 S_THEN_U / 구조화 선행이며 두 프로필을 함께 유지합니다. 융합은 답변 대용 지표에서 더 높은 값이 있어 답변 품질 비교 후보로 보존합니다. 검색·답변 모든 면에서 하나가 우세하다고 확정하지 않습니다. unknown을0으로 취급하거나 범위의 최대값을 보장 할인율로 취급하는 설정은 배제했습니다. 청킹 프로필 비교는 청킹·재정렬·근거 묶음 전체의 비교이므로 순수 청킹 효과로 해석하지 않습니다.</p><p>실적·한도 원문 필드 부족, 보수적 부분 라벨, OCR 통과 가정과 미완성 제목 계층이 남아 있습니다. 기록 wall은 CPU·warm cache가 섞이며 비교 지연을 뜻하지 않습니다. 실제 청구 비용은 미측정입니다. 답변 캐시의 예외 변환과 상태·문자열 대용 지표 한계를 위 후속 진단으로 분리했습니다. 다음 검증은 PDF 근거로 만든 독립 정답·조건 관계 검증 및 수정된 제목 계층의 새 버전 비교입니다. 질문 보강 후 전체 검색은 요청대로 후속 실험에 남깁니다.</p>'''

def diagnostics_table(directory):
    p=directory/'failure_diagnostics.json'
    if not p.exists():return ''
    d=C.read_json(p)
    body='<h2>추가 진단 — 실패 경로·unknown 후보 보존</h2><p>일차 실행에서 오류와 필드 미상을 관찰해 추가한 사후 개발 진단입니다. 새 검색·LLM 호출 없이 저장된 결과를 확인합니다. 후보 보존율은 실제 순위·답변 개선이 아니며 운영 채택 근거가 아닙니다.</p>'
    body+=table(['오류','건수'],list(d['error_counts'].items()),'원인별 실제 검색 실패')
    body+=table(['질의','프로필','살아남은 검색','투영 Hit','투영 MRR'],[[r['query_id'],r['profile'],r['surviving_mode'],r['projected_metrics']['anchor_hit_at_3'],r['projected_metrics']['anchor_mrr']] for r in d['degraded_fusion_projection']],
                '융합 한 경로 실패 시 남은 경로 사용 가정: 사후 투영, 새로운 검색 성능 아님')
    body+=table(['질의','알려진 정답 수','strict 후보 수','unknown 합집합 후보 수','strict 보존 정답','합집합 보존 정답','U fallback'],[[r[k] for k in ['query_id','gold_anchors','strict_cards','unknown_union_cards','strict_retained_anchors','union_retained_anchors','fallback']] for r in d['selection_diagnostics']],
                '구조화 선별 손실: unknown 합집합은 잠재적 보존량이며 실제 검색하지 않음')
    body+='<p class="guide">후보 정답 보존은 알려진 정답 카드 중 선별 집합에 남은 카드 수입니다. 전체 알려진 정답 수로 나누면 비율이며 높을수록 좋습니다. 편의점 정답 2개 중 1개가 unknown이면 strict는 1개, 합집합은 2개를 보존할 수 있지만 후보가 너무 늘어 순위가 나빠질 수 있습니다.</p>'
    probe=directory/'backend_probe_results.json'
    if probe.exists():
        records=C.read_json(probe)['records']
        body+='<h3>전체 카드 범위 고정: 검색 backend / 구현 경로 비교</h3><p>구조화 필터를 적용하지 않고 106개 전체 카드를 명시한 정확 L2 결과를 원래 전체 근사 L2 결과와 비교했습니다. 첫 대표 질문 6개씩 두 프로필, 총 12조건의 사후 개발 진단입니다. BM25도 운영 카드범위 WHERE 경로를 사용합니다. 신규 API·임베딩은 0이며 품질 우승을 선정하는 실험이 아닙니다.</p>'
        body+=table(['질의','프로필','전체 근사 상태','전체 범위 정확 상태','카드 순위 동일','근사 Hit/MRR','정확 Hit/MRR'],[[r['query_id'],r['profile'],r['global_status'],r['status'],r.get('same_card_ranking_as_global'),str((r.get('global_metrics') or {}).get('anchor_hit_at_3'))+'/'+number((r.get('global_metrics') or {}).get('anchor_mrr')),str(r.get('metrics',{}).get('anchor_hit_at_3'))+'/'+number(r.get('metrics',{}).get('anchor_mrr'))] for r in records], '후보 범위가 같을 때 근사/정확 경로의 카드 순위 변화')
    return body

def answers_table(path,freeze):
    if not path.exists():return '<p>답변 생성: 미실행.</p>'
    records=C.read_json(path)['records'];cases={q['query_id']:q for q in freeze['queries']};groups=defaultdict(list)
    for r in records:groups[(r['profile'],r['mode'])].append(r)
    rows=[]
    for key,items in sorted(groups.items()):
        ok=[r for r in items if r['status']=='ok'];positive=[r for r in items if cases[r['query_id']]['gold_cards']];neg=[r for r in items if cases[r['query_id']]['negative_control']]
        rows.append([*key,len(items),len(ok),len(items)-len(ok),
          percent(sum(r.get('metrics',{}).get('answer_anchor_hit') or 0 for r in positive)/len(positive) if positive else None),
          percent(sum(r['metrics']['citation_ownership'] for r in ok)/len(ok) if ok else None),
          percent(sum(r.get('metrics',{}).get('negative_abstention') or 0 for r in neg)/len(neg) if neg else None),
          sum(r['metrics']['insufficient'] for r in ok)])
    body=table(['프로필','방식','시도','완료','실패','Answer Anchor Hit','Citation Ownership (완료 분모)','Negative Abstention','근거 부족'],rows,'GPT-6 Luna / reasoning medium: 답변 결과')
    scorepath=path.with_name('answer_constraint_scores.json')
    if scorepath.exists():
        scored=C.read_json(scorepath)['records'];bygroup=defaultdict(list)
        for row in scored:bygroup[(row['profile'],row['mode'])].append(row)
        stats=[]
        for key,items in sorted(bygroup.items()):
            coverage=[r['constraint_coverage'] for r in items if r.get('constraint_coverage') is not None]
            complete=[r['known_positive_complete'] for r in items if r.get('known_positive_complete') is not None]
            stats.append([*key,len(coverage),percent(sum(coverage)/len(coverage) if coverage else None),len(complete),
                percent(sum(complete)/len(complete) if complete else None),sum(r.get('unsupported_percent_claims',0) for r in items),sum(r.get('numeric_percent_claims',0) for r in items)])
        body+=table(['프로필','방식','구조화 분모','Constraint Coverage','양성 분모','Known Positive Complete','비율 미확인 claim','비율 claim 분모'],stats,'조건·인용 숫자 진단 — 의미 검증이 아닌 보수적 대용 지표')
        if any(r.get('unsupported_percent_claims',0) for r in scored):
            body+='<p class="guide">이번 미확인 비율 사례는 Q10 benefit 구조화 선행 답변의 value=1.5입니다. 답변은 기본1%와 커피 추가0.5%를 함께 설명했지만, 동결된 규칙은 인용문에1.5%가 문자 그대로 없으면 미확인으로 표시합니다. 합산 수치가 의미적으로 정당한지와 두 혜택의 동시 적용은 이번 규칙에서 검증하지 않았습니다. 따라서 이1건을 곧바로 허위 수치라고 판정하지 않으며, 점수 규칙은 사후 변경하지 않았습니다.</p>'
    failures=[r for r in records if r['status']!='ok']
    if failures:body+=table(['질의','프로필','방식','실패 사유'],[[r['query_id'],r['profile'],r['mode'],r['error']] for r in failures],'답변 실패: 검색 실패·provider incomplete 등 원인별 기록')
    negatives=[r for r in records if cases[r['query_id']]['negative_control'] and r['mode']=='U' and r['status']=='ok']
    if negatives:
        body+=table(['질의','프로필','답변 상태','추천 수','Negative Abstention','실제 답변 발췌'],
          [[r['query_id'],r['profile'],r['answer']['answer_status'],len(r['answer']['recommendations']),r['metrics']['negative_abstention'],
            r['answer']['answer_text'][:600]] for r in negatives],'음성 대조 실제 답변: 상태 지표와 문장 내용은 다를 수 있음')
        body+='<p class="guide">Negative Abstention / 음성 질문의 상태 기반 보류율은 insufficient_evidence / 근거 부족 상태만 통과시킵니다. “1000% 할인은 확인되지 않는다”고 명시하고 추천 목록을 비워도 answered / 답변 완료 상태이면 0입니다. 이 엄격한 상태 진단의 낮은 값만으로 허위 혜택 추천이나 실제 위험 답변이라고 확정할 수 없습니다. 점수 규칙은 답변 생성 전에 동결한 그대로 유지하고 문장 내용을 별도로 공개합니다.</p>'
    return body

def source_comparison(freeze,parser):
    """Compare actual query plans against the frozen developer interpretation."""
    oracle_path=C.STAGES[32]/'retrieval_results.json';llm_path=C.STAGES[33]/'retrieval_results.json'
    if not oracle_path.exists() or not llm_path.exists():return ''
    cases={q['query_id']:q for q in freeze['queries']}
    oracle=C.read_json(oracle_path)['records'];llm=C.read_json(llm_path)['records']
    human={(r['profile'],r['mode']):r for r in aggregate(oracle,cases)}
    actual=aggregate(llm,cases);rows=[]
    for r in actual:
        h=human[(r['profile'],r['mode'])]
        rows.append([r['profile'],r['mode'],r['positive_denominator'],percent(h['hit']),percent(r['hit']),
          f"{100*((r['hit'] or 0)-(h['hit'] or 0)):+.2f}%p",number(h['mrr']),number(r['mrr']),
          f"{(r['mrr'] or 0)-(h['mrr'] or 0):+.4f}",h['failed'],r['failed']])
    body='<h2>사람 조건 → 실제 LLM 조건의 검색 차이</h2><p>Oracle / 사람 기준은 개발자가 동결한 지원 가능한 조건 해석이며 이상적인 전수 정답은 아닙니다. 질문·DB·청킹·검색 설정을 고정한 두 실행을 비교합니다. LLM의 다른 조건 해석과 카드 범위·fallback 변화가 포함되므로 조건식 일치율만으로 검색 품질을 판단하지 않습니다.</p>'
    body+=table(['프로필','방식','양성 분모','사람 Hit','LLM Hit','Hit 차이','사람 MRR','LLM MRR','MRR 차이','사람 실패','LLM 실패'],rows,
                '동일 27개 양성 질문: LLM − 사람 조건, 실패는 0')
    human_lookup={(r['query_id'],r['profile'],r['mode']):r for r in oracle}
    changes=[]
    for row in llm:
        if not cases[row['query_id']]['gold_cards']:continue
        before=human_lookup[(row['query_id'],row['profile'],row['mode'])]
        bm=before.get('metrics',{});am=row.get('metrics',{})
        if (bm.get('anchor_hit_at_3'),bm.get('anchor_mrr'))!=(am.get('anchor_hit_at_3'),am.get('anchor_mrr')):
            changes.append([row['query_id'],row['profile'],row['mode'],bm.get('anchor_hit_at_3',0),am.get('anchor_hit_at_3',0),
                            number(bm.get('anchor_mrr') or 0),number(am.get('anchor_mrr') or 0)])
    body+=table(['질의','프로필','방식','사람 Hit','LLM Hit','사람 MRR','LLM MRR'],changes,
                '조건 추출 후 카드 포함 또는 첫 정답 순위가 바뀐 질문 전체')
    examples=[]
    for row in parser:
        if row['query_id'] not in ['Q01','Q20','Q22']:continue
        examples.append([row['query_id'],cases[row['query_id']]['question'],
            C.canonical(row.get('oracle_plan',cases[row['query_id']]['plan'])),
            C.canonical(row.get('plan',{'error':row.get('error')}))])
    body+=table(['질의','입력 질문','사람 조건 JSON','실제 LLM 출력 JSON'],examples,
                '숫자 조건·OR·여행 추천의 실제 출력 예시: LLM이 고정 스키마의 값을 생성')
    return body

def answer_failure_followup_table(stage,freeze):
    path=C.STAGES[32]/'answer_failure_followup_results.json'
    if not path.exists():return ''
    result=C.read_json(path);records=[r for r in result['records'] if r['stage']==stage]
    body='<h2>답변 실패 후속 진단 — 원래 결과 보존</h2><p>원래 캐시는 저장된 ValidationError를 재사용할 때 RuntimeError로 바꾸어 운영 answer의 두 번째 시도를 생략하는 경우가 있었습니다. 이 영향이 있는 주 답변 점수는 운영 방식의 순수 품질 비교로 사용할 수 없습니다. 먼저 같은 2400 토큰으로 원래 validation 재시도를 캐시만으로 재현했습니다. 아직 실패한 행만 같은 질문·근거·스키마·prompt·gpt-6-luna/medium으로 4800 토큰을 시험했습니다. 토큰 부족은 실패 응답 상태/usage를 보존하지 못해 확정된 원인이 아닌 검증 가설입니다. 원래 검색·답변 표에 후속 값을 대입하지 않았습니다.</p>'
    body+=table(['질의','프로필','방식','후속 조건','상태','출력 토큰 상한','신규 provider 시도','신규 성공','Known Positive Complete','실패 사유'],
      [[r['query_id'],r['profile'],r['mode'],'캐시 재시도' if r['kind']=='offline_retry' else '4800 토큰',r['status'],r['max_output_tokens'],r['new_provider_attempts'],r['new_successful_api_requests'],r.get('condition_scores',{}).get('known_positive_complete'),r.get('error','')] for r in records],
      '실패 행에 한정한 사후 개발 진단: 조건별 성공/실패, 주 실험 채택 아님')
    body+='<p class="guide">Known Positive Complete / 알려진 정답의 조건·인용 완결은 주 지표와 동일한 엄격한 추천 목록 기반 대용 점수입니다. 같은 알려진 정답 카드의 요구 조건과 소유권·비율을 충족하면1이며 높을수록 좋습니다. 카드 상세 설명이 정확해도 추천 목록이 비면0일 수 있으므로 전체 답변 정확도가 아닙니다. Provider 시도는 캐시 미사용 SDK 호출 경계 횟수이며 성공은 파싱까지 완료한 신규 응답 수입니다. 캐시 재시도는 신규 API0이 통과 조건입니다. 실패 시 청구 토큰/비용은 미측정입니다.</p>'
    primary=C.read_json(C.STAGES[stage]/'answer_results.json')['records']
    scored=C.read_json(C.STAGES[stage]/'answer_constraint_scores.json')['records']
    key=lambda r:(r['query_id'],r['profile'],r['mode'])
    scores={key(r):r for r in scored};cases={q['query_id']:q for q in freeze['queries']}
    follow={(key(r),r['kind']):r for r in records};rows=[]
    for policy in ['offline_retry','larger_budget']:
        groups=defaultdict(list)
        for r in primary:
            chosen=follow.get((key(r),'offline_retry'))
            if chosen is None or chosen['status']!='ok':
                chosen=follow.get((key(r),'larger_budget')) if policy=='larger_budget' else None
            selected=chosen if chosen is not None and chosen['status']=='ok' else r
            score=chosen['condition_scores'] if chosen is not None and chosen['status']=='ok' else scores[key(r)]
            groups[(r['profile'],r['mode'])].append((r,selected,score))
        for (profile,mode),items in sorted(groups.items()):
            positive=[i for i in items if cases[i[0]['query_id']]['gold_cards']]
            rows.append(['캐시 재시도만' if policy=='offline_retry' else '캐시 재시도+남은 실패4800',profile,mode,
              sum(r['status']!='ok' for r,_,_ in items),sum(a['status']!='ok' for _,a,_ in items),len(positive),
              percent(sum(a.get('metrics',{}).get('answer_anchor_hit') or 0 for _,a,_ in positive)/len(positive)),
              percent(sum(s.get('known_positive_complete') or 0 for _,_,s in positive)/len(positive))])
    body+=table(['사후 정책','프로필','방식','주 답변 실패','정책 적용 후 실패','양성 분모','Answer Anchor Hit','Known Positive Complete'],rows,
                '전체33질문으로 합산한 후속 정책: 성공 답변 유지, 선택된 실패 행만 후속 결과로 계산')
    body+='<p class="guide">양성 분모는 동일한27개입니다. Answer Anchor Hit는 추천 목록의 알려진 정답 포함, Known Positive Complete는 그 정답의 지원 조건·인용 완결 대용 점수입니다. 높을수록 좋고 실패는0입니다. 예를 들어 편의점10% 정답을 추천하고 요구 실적을 인용하면 두 지표를 통과할 수 있습니다. 상세 설명 질문은 추천 없이 답할 수 있으며 이 지표에서는 낮게 잡힐 수 있습니다. 이 합산은 사후 정책 비교이며 기존 주 실험표를 대체하거나 독립 확증이 되지 않습니다.</p>'
    body+=f'<p><a href="../../notebooks/data/{C.STAGES[32].name}/answer_failure_followup_results.json">전체 실패 진단 결과</a></p>'
    return body

def measured_orchestration_records():
    path=C.STAGES[30]/'orchestration_progress.json'
    if not path.exists():return []
    return [r for r in C.read_json(path)['records'] if not r['label'].startswith('notebook_')
            and r['label'] not in {'reports_after_all_notebooks','original_preservation','compact_summaries','final_audit'}]

def orchestration_resources():
    records=measured_orchestration_records()
    if not records:return ''
    rows=[[r['label'],r['status'],round(r['wall_seconds'],2) if 'wall_seconds' in r else '미측정',
           round(r['child_cpu_seconds'],2) if 'child_cpu_seconds' in r else '미측정',
           r.get('cumulative_child_peak_rss_kib','미측정')] for r in records]
    return '<h2>순차 실행 runner의 시간·메모리 기록</h2>'+table(
        ['단계 코드','상태','wall초','child CPU초','누적 child 최대 RSS KiB'],rows,
        '완료 단계의 실제 단일 실행값; 최초 원본 검색의 cold 실행과 별개')+'''<p class="guide">Wall time / 경과 시간은 단계 시작부터 종료까지의 초입니다. Child CPU / 자식 프로세서 시간은 자식 프로세스의 user+system 시간 증가량입니다. Peak RSS / 최대 상주 메모리는 Linux 자식 프로세스의 누적 최대값이며 해당 단계만의 최대 메모리가 아닙니다. 동일 품질·동일 캐시·동일 부하에서 작을수록 유리합니다. 예를 들어 198개 검색의 캐시 재실행 40초는 신규 BGE 추론의 지연을 뜻하지 않습니다. 실행 중 단계 값과 단계별 독립 최대 메모리는 미측정입니다. 여러 반복의 평균·표준편차가 아닙니다.</p>'''

def artifact_inputs(stage):
    paths=[C.STAGES[30]/'db_build_summary.json',C.STAGES[31]/'chunking_summary.json',
           C.STAGES[32]/'dataset_freeze.json',Path(__file__),Path(__file__).with_name('30_execute_experiment_notebooks.py')]
    if stage==30:paths+=[C.STAGES[30]/'preservation_verification.json']
    if stage==31:
        paths+=[C.STAGES[31]/'fact_chunk_map.jsonl',*sorted((C.STAGES[31]/'hierarchy').glob('*.json'))]
        paths+=[C.STAGES[31]/p/'index_summary.json' for p in ['card_page_section_benefit','parent_child_bundle']]
    if stage in [32,33]:
        paths+=[C.STAGES[stage]/name for name in ['retrieval_results.json','answer_results.json',
                'answer_constraint_scores.json','failure_diagnostics.json','backend_probe_results.json',
                'parser_results.json','repeat_results.json','evaluation_summary.json']]
        if stage==33:paths+=[C.STAGES[32]/'retrieval_results.json']
        paths+=[C.STAGES[32]/'answer_failure_followup_results.json']
    return {str(p.relative_to(C.ROOT)):C.digest(p.read_bytes()) if p.exists() else None for p in paths}

def report_inputs(stage):
    return {'artifacts':artifact_inputs(stage),'api_resources':api_resources(),
        'measured_orchestration_records':measured_orchestration_records(),
        'notebooks':{str(n):C.digest((C.ROOT/'notebooks'/f'{n}_{NAMES[n]}.ipynb').read_bytes()) for n in NAMES}}

def render(stage):
    nav='<nav>'+''.join(f'<a href="{n}_{NAMES[n]}_report.html">{n}번</a>' for n in NAMES)+'</nav>'
    body=nav+f'<h1>{stage}. {TITLES[stage]}</h1>'
    body+='<div class="note">OCR 검증기를 수정하지 않았습니다. Luna·Upstage OCR 통과를 가정하며 현재 Upstage 계층과 운영 청킹/검색을 사용합니다. 운영 원본·기존 산출물은 읽기 전용입니다. 이 결과는 구조화 검색 실험이며 PDF 사실 정확도나 운영 채택의 증거가 아닙니다.</div>'
    body+='<h2>실험 목적과 고정 조건</h2><p>새 PC에서 구조화 선행 효과를 재현하고, benefit·parent-child 운영 프로필을 비교합니다. 기존 JSON을 DB에 적재하며 새 OCR 추출은 없습니다. 비구조화는 Vector:FTS5 BM25=0.4:0.6, 각 Top50, D20, 출력 Top3입니다. benefit는 selective BGE, parent-child는 모든 질문의 bundle BGE를 유지합니다. 프레임워크 없이 OpenAI SDK/Pydantic을 사용하고 새 LLM 호출은 gpt-6-luna/medium입니다.</p>'
    body+='<p>Baseline / 이번 기준선 U는 기존 운영 알고리즘을 고정 Upstage corpus로 새로 구성한 개발 기준선입니다. 활성 운영 release의 기존 품질 수치를 가져온 것이 아닙니다. Candidate / 새 후보는 구조화 선행·카드 순위 융합이며 Production adopted / 운영 채택 설정은 없습니다. 이전 PC의 개선 관측은 사용자 기억으로만 남기고 이번 점수로 전용하지 않습니다.</p>'
    body+='<p>운영 벡터 구현은 전체 검색에서 Chroma 근사 L2, 카드 범위 검색에서 메모리의 정확 L2를 사용합니다. 구조화 선행의 효과에는 카드 선별과 이 운영 범위 검색 방식이 함께 포함됩니다. 순수 DB 선별 효과와 동일하게 해석하지 않습니다.</p>'
    body+=table(['코드','쉬운 설명'],[['U','현재 운영 비구조화: 벡터+BM25 검색'],['S_THEN_U','구조화로 카드 선별 → 그 카드 안에서 운영 비구조화 검색'],['S_PLUS_U','전체 비구조화 결과와 구조화로 선별 후 검색한 카드 순위 융합'],['card_page_section_benefit','카드·페이지·섹션·혜택 청크, 선택적 BGE 재정렬'],['parent_child_bundle','상위·하위 청크를 묶어 모든 질문에서 BGE 재정렬']], '검색 방식·프로필 코드 안내')
    body+=metric_guide()
    if stage==30:
        summary=C.read_json(C.STAGES[30]/'db_build_summary.json')
        body+='<h2>무엇을 준비했는가</h2><p>기존 Upstage normalized JSON의 사실·근거·원문을 그대로 보존하고 보수적인 수치 파생값만 추가했습니다. 구조화 검색은 SQLite 읽기 전용 SELECT로 읽은 facts에 같은 혜택 관계의 조건을 적용합니다. unknown은 false나 0이 아닙니다.</p>'
        body+='<p>과정은 기존 OCR txt → 이미 저장된 normalized JSON → 새 SQLite facts 저장 → 질문을 고정 QueryPlan JSON으로 해석 → 같은 fact의 대상·할인율·실적·한도 조건 평가 → 후보 카드 안의 벡터+BM25 검색 → 근거를 전달한 별도 답변 생성입니다. 새 실험에서 txt를 다시 LLM으로 구조화하지 않았습니다. LLM은 SQL 문자열을 생성하지 않고 스키마의 값만 출력합니다. 이 소표본 실험은 읽기 전용 SQLite에서 읽은 사실을 Python의 AND/OR·unknown 판정기로 선별합니다.</p>'
        body+=table(['항목','값'],list(summary['counts'].items()),'입력·DB 행 수')
        body+=table(['파생 필드','지원율'],[[k,percent(v)] for k,v in summary['support_rates'].items()],'known 필드 지원율: 분모 3403 facts')
        body+='<h2>주의점</h2><p>원본 JSON provenance의 이전 모델·reasoning 기록은 보존했습니다. 새 실험 모델 설정과 혼동하지 않습니다. 다중 비율·금액·실적 구간·범위가 모호하면 unknown으로 남습니다. 범위·상한 비율 37건과 지출 상한을 혜택 한도로 오해한 90건은 독립 리뷰와 회귀 검증으로 unknown 처리했습니다. 월/일 한도 기간까지 확인된 사실은 66개입니다. 여행·커피 alias는 개발자가 고정한 사전이며 평가 결과를 보고 확장하지 않았습니다.</p>'
        body+='<h2>최종 정리</h2><p>DB 변환과 읽기 전용 조회 기반을 준비했습니다. 구조화 검색 성능 개선은 후속 검색 결과로 판단합니다. OCR 승인·검증기 개선·운영 DB 교체는 하지 않았습니다.</p>'
    elif stage==31:
        summary=C.read_json(C.STAGES[31]/'chunking_summary.json')
        body+='<h2>입력과 운영 구현</h2><p>benefit는 운영 경로와 같은 원본 Upstage ocr.txt/해시를 사용하고, parent-child는 같은 lane pages의 render_pages 결과를 사용합니다. 원본 build_benefit_chunks/build_structural_chunks를 수정하지 않았습니다. 제목 #/## 계층은 현재 상태로 유지했습니다.</p>'
        body+=table(['프로필','문서','청크'],[[p,v['documents'],v['chunks']] for p,v in summary['profiles'].items()],'운영 청킹 생성 결과')
        maps=C.read_jsonl(C.STAGES[31]/'fact_chunk_map.jsonl');counts=defaultdict(lambda:[0,0])
        for r in maps:counts[r['profile']][0]+=1;counts[r['profile']][1]+=bool(r['chunk_ids'])
        body+=table(['프로필','fact 수','대상·값 근거 연결','연결률'],[[p,a,b,percent(b/a)] for p,(a,b) in counts.items()],'청킹 연결 진단 — 조건 전체를 입증하는 지표는 아님')
        audits=summary['audits'];depths=Counter();missing_parents=0
        for p in (C.STAGES[31]/'hierarchy').glob('*.json'):
            for r in C.read_json(p):depths[len(r.get('heading_path',[]))]+=1
        body+=table(['제목 경로 깊이','node 수'],sorted(depths.items()),'현재 계층의 깊이 분포')
        index_rows=[]
        for p in summary['profiles']:
            path=C.STAGES[31]/p/'index_summary.json'
            if path.exists():v=C.read_json(path);index_rows.append([p,v['embedding_shape'],round(v['wall_seconds'],2),v['index_bytes']])
        body+=table(['프로필','벡터 shape','인덱스 단계 wall초','파일 bytes'],index_rows,'새 인덱스 자원 — 생성·검증 단계 시간, 요청 latency 아님')
        body+='<h2>최종 정리</h2><p>두 운영 프로필의 입력·청크·근거 연결·인덱스를 분리했습니다. parent-child 완성이나 우세를 주장하지 않습니다. 계층 개선은 다른 세션 결과를 별도 버전으로 검증해야 합니다.</p>'
    else:
        freeze=C.read_json(C.STAGES[32]/'dataset_freeze.json')
        body+='<h2>평가 범위·주의점</h2><p>'+ESC(freeze['gold_limit'])+'</p><p>개발/확증을 결과 전 고정했습니다. 확증도 같은 OCR source에서 만든 라벨이며 독립 PDF 검증·새 사용자 모집단을 뜻하지 않습니다. 일반 질문에는 전수 정답 라벨이 없어 인용·실행 상태만 확인합니다. 기존 sealed holdout은 사용하지 않았습니다.</p>'
        body+='<p>질문과 split은 최초 실행 전에 동결했으나, provisional / 임시 실행 도중 독립 리뷰가 범위·한도 의미와 Q12 라벨 파서 오류를 발견했습니다. 이를 회귀 테스트로 보완하고 임시 기록을 보존한 뒤 주 실행을 다시 수행했습니다. 성능을 보고 고른 alias·검색 파라미터 변경은 없으며, 이 확증 결과를 독립 holdout으로 부르지 않습니다.</p>'
        body+='<p>여행·라운지 등 action=any / 혜택 종류 제한 없음 질문의 라벨은 관련 대상 필드가 있다는 원문 상대 기준입니다. 유료 이용 안내·예외가 함께 있을 수 있으므로 실제 무료 혜택이나 여행에서의 유용성을 증명하지 않습니다. 조건식 일치는 고정한 해석과의 일치이며, 다른 합리적 질문 해석을 전부 오류로 확정하지 않습니다.</p>'
        body+='<p>조건 평가의 범위는 고정 QueryPlan이 지원하는 항목입니다. Q29의 국내 지역 제한, 선택형 서비스팩 사이의 상호 배타성, 혜택 예외 전체의 의미 해석은 이번 DB 판정·정답 라벨에서 검증하지 않습니다. AND는 동일 카드에서 각각 대응 사실이 있다는 기준이며 동시에 선택·이용 가능한 서비스라는 증명은 아닙니다. 따라서 Known Positive Complete도 질문 전체의 실제 금융 적합성이나 사실 정확도를 뜻하지 않습니다.</p>'
        directory=C.STAGES[stage]
        if stage==33:
            path=directory/'parser_results.json'
            if path.exists():
                parser=C.read_json(path)['records'];ok=[r for r in parser if r['status']=='ok']
                rows=[]
                labels={'route_match':'경로 일치','condition_exact_match':'조건식 전체 일치','card_scope_match':'카드 범위 일치',
                        'candidate_set_match':'선별 카드 집합 일치','boolean_group_match':'AND/OR 그룹 크기 일치: 구성원은 조건식 전체로 검증'}
                for metric,label in labels.items():
                    rows.append([metric+' / '+label,len(parser),sum(r.get('metrics',{}).get(metric,0) for r in parser),percent(sum(r.get('metrics',{}).get(metric,0) for r in parser)/len(parser))])
                body+=table(['지표','전체 분모','일치','비율'],rows,'질문 조건·경로 정확도: 실패를 0으로 포함')
                body+=table(['질의','상태','조건 일치','후보 일치','필드 오류'],[[r['query_id'],r['status'],r.get('metrics',{}).get('condition_exact_match'),r.get('metrics',{}).get('candidate_set_match'),r.get('metrics',{}).get('field_errors',r.get('error'))] for r in parser],'질의별 parser 오류')
            else:body+='<p>LLM 조건 추출: 미실행.</p>'
            repeat=directory/'repeat_results.json'
            if repeat.exists():
                probe=C.read_json(repeat)
                body+=table(['질의','시도','성공','서로 다른 조건 수','서로 다른 경로 수'],[[r[k] for k in ['query_id','attempts','ok','unique_conditions','unique_routes']] for r in probe['summary']], '조건 출력 반복 안정성: 질문당 3회, 원문 질문·스키마·지시 동일')
                body+='<p class="guide">Unique conditions / 서로 다른 조건 수는 alias·숫자를 정규화한 조건 출력의 종류입니다. 성공 3회 모두 한 종류이면 이번 세 번은 안정적입니다. 편의점 OR 질문이 어떤 실행에서 AND로 바뀌면 종류가 늘어납니다. 낮을수록 안정적이지만 세 번만으로 실제 오류 확률이나 전체 재현성을 추정할 수 없습니다.</p>'
        path=directory/'retrieval_results.json'
        if path.exists():body+=search_tables(C.read_json(path)['records'],freeze)
        else:body+='<p>검색 결과 집계: 실행 중 또는 미실행. 완료로 판정하지 않습니다.</p>'
        body+=answers_table(directory/'answer_results.json',freeze)
        body+=answer_failure_followup_table(stage,freeze)
        if stage==33 and (directory/'parser_results.json').exists():
            body+=source_comparison(freeze,C.read_json(directory/'parser_results.json')['records'])
        body+=diagnostics_table(directory)
        if path.exists():body+=final_decision(C.read_json(path)['records'],freeze)
    body+='<h2>공유 캐시 누적 자원 — 단계별 비용·토큰 아님</h2><p>아래는 렌더 시점의 30~33번 공유 API 캐시 전체 합계입니다. 단계별 API 토큰·비용은 미측정이며, 이 수치를 DB 변환이나 청킹 단계의 자원량으로 사용하지 않습니다. 단계별로 직접 측정한 시간·파일 크기는 해당 표에 따로 표시합니다. 30번 기존 JSON의 DB 변환은 외부 LLM을 호출하지 않았습니다.</p><pre>'+ESC(C.canonical(api_resources()))+'</pre>'
    body+=orchestration_resources()
    artifact=f'../../notebooks/data/{C.STAGES[stage].name}/'
    if stage in [32,33]:
        body+='<p>전체 결과 파일: '+''.join(f'<a href="{artifact}{name}">{ESC(label)}</a> · ' for name,label in [('evaluation_summary.json','버전·평가 요약'),('retrieval_results.json','검색 원문 결과'),('answer_results.json','답변 원문 결과'),('answer_constraint_scores.json','조건 평가'),('failure_diagnostics.json','추가 진단')])+'</p>'
        body+='<p>버전·평가 요약은 전체 198조건의 카드·근거 ID·검색/답변/조건 지표·실패, 33질문의 선별 trace를 보존한 Git 저장용 결과입니다. 33번은 실제 parser 조건과 오류도 포함합니다. 원문 근거·답변과 provider 응답 캐시는 이 worktree의 로컬 산출물로 보존하며 Git에 넣지 않습니다. 입력 OCR·인덱스·캐시가 없는 다른 checkout에서는 원문 결과 링크와 재현 입력을 별도로 준비해야 합니다.</p>'
    notebooks=[C.read_json(C.ROOT/'notebooks'/f'{n}_{NAMES[n]}.ipynb') for n in NAMES]
    complete=all(cell.get('execution_count') is not None and not any(o.get('output_type')=='error' for o in cell.get('outputs',[]))
                 for nb in notebooks for cell in nb['cells'] if cell['cell_type']=='code')
    body+='<p>'+('30~33번 노트북의 모든 코드 셀을 skn25에서 실제 결과 검증·보고서 생성까지 실행했습니다.' if complete else '30~33번 노트북 전체 셀 실행 검증은 아직 완료되지 않았습니다.')+' 장시간 검색·답변·DB 생성은 별도의 재개 가능한 runner에서 실행하며, 노트북 셀만으로 전체 유료·모델 작업을 처음부터 재실행한 것은 아닙니다.</p>'
    body+=f'<footer>노트북: <a href="../../notebooks/{stage}_{NAMES[stage]}.ipynb">{stage}번</a> · <a href="{artifact}">기계 판독 산출물 폴더</a> · 전체 노트북 재실행 여부와 원본 보존 검증은 실행 감사 산출물에서 확인합니다.</footer>'
    target=C.ROOT/'docs/report'/f'{stage}_{NAMES[stage]}_report.html'
    target.write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="experiment-input-fingerprint" content="'+C.digest(report_inputs(stage))+'"><title>'+ESC(TITLES[stage])+'</title><style>'+STYLE+'</style><main>'+body+'</main></html>',encoding='utf-8')
    return target

if __name__=='__main__':
    for stage in NAMES:print(render(stage))
