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
    if any(k in context for k in ['Hit','MRR','Witness','Recall']):
        note='분모는 표의 알려진 양성 질문 수이며 실행 실패는 0입니다. Hit는 정답 카드 포함 여부, MRR은 첫 정답 순위의 역수입니다. Witness는 필수 원문 구성요소를 한 근거에서 찾은 정답 카드 비율입니다. 높을수록 좋지만 부분 정답 라벨·문자열 진단입니다. 편의점 10% 정답이 2위이면 Hit=1, MRR=0.5이며 실적 근거가 빠지면 Witness를 통과하지 못합니다.'
    elif '승' in headers:
        note='같은 양성 질문의 두 조건을 비교합니다. 새 조건 점수가 높으면 승, 낮으면 패, 같으면 동률입니다. 승이 많아도 다른 질문 유형의 회귀를 함께 확인해야 합니다.'
    elif any(k in context for k in ['분모','일치','오류']):
        note='조건·경로·후보 일치율은 전체 질문 수를 분모로 하며 실패는 불일치입니다. 높을수록 좋습니다. 예를 들어 OR를 AND로 잘못 추출하면 카드 선별 집합이 바뀔 수 있습니다. 동결된 사람 조건도 유일한 정답 해석은 아닙니다.'
    elif any(k in context for k in ['지원율','연결률']):
        note='전체 원본 사실 3,403개 중 확인 가능한 필드 또는 연결된 사실의 비율입니다. 높을수록 좋습니다. 단일 10%가 원문·typed 값과 같으면 known이지만 3~5%는 unknown입니다. OCR 사실 정확도를 검증한 값은 아닙니다.'
    elif any(k in context for k in ['wall','bytes','shape']):
        note='wall은 해당 단계의 실제 경과 초, bytes는 저장 파일 크기입니다. 한 번 실행한 값이며 캐시·CPU 부하가 섞여 실서비스 지연 비교로 쓸 수 없습니다. 품질이 같을 때 적을수록 유리합니다.'
    return '<table><caption>'+ESC(caption)+'</caption><thead><tr>'+''.join('<th>'+ESC(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+ESC(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'+('<p class="guide">'+note+'</p>' if note else '')

def percent(value):return '미측정' if value is None else f'{100*value:.2f}%'
def number(value):return '미측정' if value is None else f'{value:.4f}'

def metric_guide():
    return '<div class="guide"><h3>지표 안내 — 분모·방향·통과 예·한계</h3>'+table(['English / 쉬운 뜻','데이터·분모와 방향','통과 예시','핵심 한계'],[
      ['Anchor Hit@3 / 알려진 정답 카드 포함','원문 기준 정답 카드가 하나 이상 있는 평가 질문 수. 실패 실행은 0. 높을수록 좋음.','편의점 10% 질문의 알려진 정답 카드가 Top3에 있으면 1.','정답 카드 목록은 보수적 부분 라벨이다. 추가 정답 카드를 오답으로 판정하지 않음.'],
      ['Anchor MRR / 첫 정답의 순위','같은 양성 질문 수. 첫 정답 순위의 역수, 없으면 0. 높을수록 좋음.','정답 카드가 2위이면 0.5.','정답이 몇 개 더 있는지와 근거 정확도를 말하지 않음.'],
      ['Anchor Recall@3 / 알려진 정답 카드 회수','질문별 알려진 정답 카드 중 Top3에 든 비율을 양성 질문에서 평균. 높을수록 좋음.','알려진 정답 2개 중 1개 검색이면 0.5.','전수 정답 recall이 아니며 많은 정답이 있는 질문은 Top3 상한이 있음.'],
      ['Witness Recall@3 / 필수 원문 구성요소 회수','질문별 알려진 정답 카드 중 필요 대상·행동·비율·실적·한도 원문 필드가 같은 카드의 한 근거에 있는 비율. 높을수록 좋음.','편의점 10%와 요구한 전월 실적 조건까지 근거에 있어야 통과.','문자열 기반 보수적 진단. 표/문단 분할·바꿔 말하기를 미탐할 수 있고 의미적 관계 정확성을 증명하지 않음.'],
      ['Strict Prefilter Retention / 선별 전 정답 보존','구조화 질의의 알려진 정답 카드 중 strict 선별 집합에 남은 비율. 높을수록 좋음, 1이 손실 없음.','정답 2개 중 1개를 DB 단계에서 제외하면 0.5.','unknown fallback 이후 검색 결과와 다르며 OCR/필드 누락에 영향받음.'],
      ['Answer Anchor Hit / 답변 정답 카드 포함','양성 질문 수. 답변 추천 목록에 알려진 정답이 있으면 1, 실패는 0. 높을수록 좋음.','편의점 혜택의 정답 카드를 답변에서 추천하면 통과.','조건을 잘못 설명한 답변도 카드 포함만으로 통과할 수 있음.'],
      ['Constraint Coverage / 답변 요구조건 구성요소','구조화 질문 수. 같은 추천 카드의 대상·행동·비율·실적·한도 구성요소 통과율을 질문별 평균. AND는 요구조건 평균, OR는 최선 그룹. 실패는 0, 높을수록 좋음.','편의점 10%와 실적 30만원 요구에서 비율은 맞지만 실적을 설명하지 않으면 1 미만.','source-relative 문자열 진단. 분리된 주장·바꿔 말하기를 미탐하고 문맥 관계를 입증하지 못함.'],
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
    for path in cache.glob('*.json'):
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
    return {'successful_request_payloads':len(successes),'failure_records':len(failed),'tokens':dict(tokens),
      'ledger_scope':'전체 실험 공유 캐시 누적. 이 보고서 단계의 비용·토큰·시간으로 해석하지 않음.',
      'stage_specific_api_tokens':'미측정','stage_specific_api_cost':'미측정',
      'saved_api_response_wall_seconds':sum(r.get('wall_seconds',0) for r in successes),
      'new_unique_embedding_texts':len(list((cache/'embeddings').glob('*.npy'))) if (cache/'embeddings').exists() else 0,
      'provider_billed_cost':'미측정','gpu':'미사용: skn25 PyTorch CPU','cpu_peak_memory':'미측정',
      'warning':'정확한 payload 캐시는 여러 조건에서 재사용. 조건별 logical 작업량과 신규 API 호출량은 다르며 stage 시간이 전체 요청 latency와 같지는 않음.'}

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
                    'failed':sum(r['status']!='ok' for r in rows),'positive_denominator':len(positive),
                    'hit':avg('anchor_hit_at_3',positive),'mrr':avg('anchor_mrr',positive),
                    'recall':avg('anchor_recall_at_3',positive),'witness':avg('witness_recall_at_3',witnesses),
                    'fallback':sum(r.get('fallback',False) for r in rows),
                    'wall':sum(r.get('wall_seconds',0) for r in rows)})
    return out

def search_tables(records,freeze):
    cases={q['query_id']:q for q in freeze['queries']};rows=aggregate(records,cases)
    body=table(['프로필','검색 방식','시도/성공/실패','양성 분모','Anchor Hit@3','MRR','Anchor Recall@3','Witness Recall@3','Fallback','기록 wall초'],
               [[r['profile'],r['mode'],f"{r['attempted']}/{r['ok']}/{r['failed']}",r['positive_denominator'],percent(r['hit']),number(r['mrr']),percent(r['recall']),percent(r['witness']),r['fallback'],round(r['wall'],2)] for r in rows],
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
        body+=table(['프로필','방식','시도/실패','양성 분모','Hit@3','Witness'],[[r['profile'],r['mode'],f"{r['attempted']}/{r['failed']}",r['positive_denominator'],percent(r['hit']),percent(r['witness'])] for r in split_rows],split+' / '+('개발' if split=='development' else '고정 설정 확증: 독립 PDF gold 아님'))
    subgroups=sorted({q['subgroup'] for q in cases.values()})
    subgroup_rows=[]
    for subgroup in subgroups:
        subset=[r for r in records if cases[r['query_id']]['subgroup']==subgroup]
        for r in aggregate(subset,cases):subgroup_rows.append([subgroup,r['profile'],r['mode'],r['attempted'],r['failed'],percent(r['hit']),percent(r['witness'])])
    body+=table(['질문 유형','프로필','방식','시도','실패','Hit@3','Witness'],subgroup_rows,'유형별 결과 — 숫자/조건/AND/OR/여행/고유명사/범용/음성')
    body+=table(['질의','유형','split','질문','알려진 정답 수'],[[q['query_id'],q['subgroup'],q['split'],q['question'],len(q['gold_cards'])] for q in cases.values()],'동결 질의 목록')
    failures=[r for r in records if r['status']!='ok']
    if failures:body+=table(['질의','프로필','방식','실패 사유'],[[r['query_id'],r['profile'],r['mode'],r['error']] for r in failures],'실패 레코드 전체')
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
    failures=[r for r in records if r['status']!='ok']
    if failures:body+=table(['질의','프로필','방식','실패 사유'],[[r['query_id'],r['profile'],r['mode'],r['error']] for r in failures],'답변 실패: 검색 실패·provider incomplete 등 원인별 기록')
    return body

def render(stage):
    nav='<nav>'+''.join(f'<a href="{n}_{NAMES[n]}_report.html">{n}번</a>' for n in NAMES)+'</nav>'
    body=nav+f'<h1>{stage}. {TITLES[stage]}</h1>'
    body+='<div class="note">OCR 검증기를 수정하지 않았습니다. Luna·Upstage OCR 통과를 가정하며 현재 Upstage 계층과 운영 청킹/검색을 사용합니다. 운영 원본·기존 산출물은 읽기 전용입니다. 이 결과는 구조화 검색 실험이며 PDF 사실 정확도나 운영 채택의 증거가 아닙니다.</div>'
    body+='<h2>실험 목적과 고정 조건</h2><p>새 PC에서 구조화 선행 효과를 재현하고, benefit·parent-child 운영 프로필을 비교합니다. 기존 JSON을 DB에 적재하며 새 OCR 추출은 없습니다. 비구조화는 Vector:FTS5 BM25=0.4:0.6, 각 Top50, D20, 출력 Top3입니다. benefit는 selective BGE, parent-child는 모든 질문의 bundle BGE를 유지합니다. 프레임워크 없이 OpenAI SDK/Pydantic을 사용하고 새 LLM 호출은 gpt-6-luna/medium입니다.</p>'
    body+=metric_guide()
    if stage==30:
        summary=C.read_json(C.STAGES[30]/'db_build_summary.json')
        body+='<h2>무엇을 준비했는가</h2><p>기존 Upstage normalized JSON의 사실·근거·원문을 그대로 보존하고 보수적인 수치 파생값만 추가했습니다. 구조화 검색은 SQLite 읽기 전용 SELECT로 읽은 facts에 같은 혜택 관계의 조건을 적용합니다. unknown은 false나 0이 아닙니다.</p>'
        body+=table(['항목','값'],list(summary['counts'].items()),'입력·DB 행 수')
        body+=table(['파생 필드','지원율'],[[k,percent(v)] for k,v in summary['support_rates'].items()],'known 필드 지원율: 분모 3403 facts')
        body+='<h2>주의점</h2><p>원본 JSON provenance의 이전 모델·reasoning 기록은 보존했습니다. 새 실험 모델 설정과 혼동하지 않습니다. 다중 비율·금액·실적 구간·범위가 모호하면 unknown으로 남습니다. 여행·커피 alias는 개발자가 고정한 사전이며 평가 결과를 보고 확장하지 않았습니다.</p>'
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
        directory=C.STAGES[stage]
        if stage==33:
            path=directory/'parser_results.json'
            if path.exists():
                parser=C.read_json(path)['records'];ok=[r for r in parser if r['status']=='ok']
                rows=[]
                for metric in ['route_match','condition_exact_match','card_scope_match','candidate_set_match','boolean_group_match']:
                    rows.append([metric,len(parser),sum(r.get('metrics',{}).get(metric,0) for r in parser),percent(sum(r.get('metrics',{}).get(metric,0) for r in parser)/len(parser))])
                body+=table(['지표','전체 분모','일치','비율'],rows,'질문 조건·경로 정확도: 실패를 0으로 포함')
                body+=table(['질의','상태','조건 일치','후보 일치','필드 오류'],[[r['query_id'],r['status'],r.get('metrics',{}).get('condition_exact_match'),r.get('metrics',{}).get('candidate_set_match'),r.get('metrics',{}).get('field_errors',r.get('error'))] for r in parser],'질의별 parser 오류')
            else:body+='<p>LLM 조건 추출: 미실행.</p>'
        path=directory/'retrieval_results.json'
        if path.exists():body+=search_tables(C.read_json(path)['records'],freeze)
        else:body+='<p>검색 결과 집계: 실행 중 또는 미실행. 완료로 판정하지 않습니다.</p>'
        body+=answers_table(directory/'answer_results.json',freeze)
        body+='<h2>최종 정리·채택 판단</h2><p>운영 채택은 없음. source-relative 소표본·OCR 통과 가정·현재 미완성 계층의 한계가 있습니다. 개선·회귀는 위 조건별 표와 질문별 실패에서 판단하며, 구조화 선별 손실·필수 조건 근거 회귀가 있으면 평균 카드 포함률 증가만으로 채택하지 않습니다. 질문 보강 후 전체 검색은 후속 실험으로 남깁니다.</p>'
    body+='<h2>공유 캐시 누적 자원 — 단계별 비용·토큰 아님</h2><p>아래는 렌더 시점의 30~33번 공유 API 캐시 전체 합계입니다. 단계별 API 토큰·비용은 미측정이며, 이 수치를 DB 변환이나 청킹 단계의 자원량으로 사용하지 않습니다. 단계별로 직접 측정한 시간·파일 크기는 해당 표에 따로 표시합니다. 30번 기존 JSON의 DB 변환은 외부 LLM을 호출하지 않았습니다.</p><pre>'+ESC(C.canonical(api_resources()))+'</pre>'
    artifact=f'../../notebooks/data/{C.STAGES[stage].name}/'
    body+=f'<footer>노트북: <a href="../../notebooks/{stage}_{NAMES[stage]}.ipynb">{stage}번</a> · <a href="{artifact}">기계 판독 산출물 폴더</a> · 전체 노트북 재실행 여부와 원본 보존 검증은 실행 감사 산출물에서 확인합니다.</footer>'
    target=C.ROOT/'docs/report'/f'{stage}_{NAMES[stage]}_report.html'
    target.write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+ESC(TITLES[stage])+'</title><style>'+STYLE+'</style><main>'+body+'</main></html>',encoding='utf-8')
    return target

if __name__=='__main__':
    for stage in NAMES:print(render(stage))
