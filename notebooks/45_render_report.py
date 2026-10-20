"""Render the comparative report from saved results; never calls providers."""
from collections import Counter
from decimal import Decimal
import html
import importlib.util
from pathlib import Path
import sys
import markdown

spec=importlib.util.spec_from_file_location('io45_report',Path(__file__).with_name('45_experiment_io.py'))
I=importlib.util.module_from_spec(spec);sys.modules[spec.name]=I;spec.loader.exec_module(I)
HTML=I.ROOT/'docs/report/45_evidence_supplement_comparison_report.html'


def table(headers,rows):
    def esc(v):return html.escape(str(v)).replace('|','&#124;').replace('\n','<br>')
    return '\n'.join(['| '+' | '.join(map(esc,headers))+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(esc,row))+' |' for row in rows])


def render():
    preparation=I.read(I.OUT/'preparation_summary.json');effects=I.read(I.OUT/'package_change_audit.json')
    automatic=I.read(I.OUT/'automatic_evaluation.json');manual=I.read(I.OUT/'source_review_summary.json')
    cost=I.read(I.OUT/'cost_accounting.json');resources=I.read(I.OUT/'answer_resources.json')
    review=I.read(I.OUT/'source_reviews.json');prepared=I.read(I.OUT/'prepared_packages.json')
    profiles=['card_page_section_benefit','parent_child_bundle'];short={'card_page_section_benefit':'benefit','parent_child_bundle':'parent-child'}
    labels={'all':'all / 전체','numeric':'numeric / 수치·조건','proper_noun':'proper_noun / 카드·고유명사','semantic':'semantic / 의미·혜택',
            'all_changed':'all_changed / 실제 변경 전체','append_only':'append_only / 추가만','replacement':'replacement / 교체 포함',
            'win':'win / 개선','loss':'loss / 퇴행','tie':'tie / 동률','uncertain':'uncertain / 불확실',
            'PASS':'PASS / 핵심 충족','PARTIAL':'PARTIAL / 일부 충족','FAIL':'FAIL / 미충족','UNCERTAIN':'UNCERTAIN / 불확실'}
    lines=['# 45. 카드·순위 고정, 동일 예산 원문 근거 보완 비교',
      '## 왜 실행했는가\n\n구조화 검색은 후보 카드와 일치 fact를 찾지만 현재 순차 방식은 카드 범위만 비구조화 검색에 전달한다. 같은 카드의 핵심 혜택·조건 근거가 최종 답변 입력에서 빠지는지, 검증된 원문 근거를 보완하면 답변이 개선되는지 확인했다. 원문 연결 검사를 먼저 실행하고 이후 근거 패키지만 비교했다.',
      '## 바꾼 것과 고정한 것\n\nA는 42번 S_THEN_U의 저장 카드·순위·근거·답변이다. B는 입력 카드와 순위를 고정하고 카드별 첫 A 근거를 보존한 뒤, 완전 충족 그룹의 fact에 연결된 원문 청크를 조건별로 번갈아 채웠다. 잔여 자리는 기존 근거로 채웠다. 카드별 최대 5개와 질문 포함 64,000 UTF-8 bytes 상한이 동일하다. 원문 중간 절단·재청킹·파서/임베딩/검색/BGE 신규 호출은 없다. 답변 생성은 기존 gpt-6-luna/medium, prompt/schema, 2400 출력 상한과 기존 재시도 정책을 유지했다.',
      '**이 비교는 고정 카드·동일 예산의 근거 재구성이다.** 추가만 된 11쌍과 기존 근거가 교체된 7쌍을 구분한다. 교체 행의 차이를 순수 추가만의 인과효과로 해석하지 않는다. LLM 최종 추천의 누락/순서 변화는 결과이며, 고정한 것은 검색 카드 목록/순위다.',
      '## 주의점\n\n106개 Upstage 문서의 OCR/구조화 통과 가정과 불완전한 제목 계층을 유지했다. 이번 연결은 원좌표를 수리하지 않는 보수적 full-line/fragment/관계 범위 검사다. 제외를 OCR 오류나 운영 검증기 실패로 단정하지 않는다. 31번 기존 매핑의 target/value 포함만으로 근거를 인정하지 않았다. 여러 청크는 원문에서 같은 fact 관계가 확인되는 경우에만 연결했다.',
      '42번의 개발 질문 48개(양성44·진단4)를 재사용했다. A는 과거 저장 답변이고 B는 신규 확률 생성이므로 시점/생성 변동이 섞인다. 독립 holdout·사람의 블라인드 검증·통계적 우월·운영 채택 결과가 아니다. 방식명을 가린 쌍대 검토지만 검토자의 이전 세션 맥락은 남아 있다. 전체 논리 조건은192개이며 동일 입력의 답변 재사용은 반복 독립 실행이 아니다.',
      '## 1. 원문 근거 연결 결과',
      'Mapping status(연결 상태)는 이번에 선택된 카드의 보완 대상인 고유 (프로필,fact) 271개가 분모다. verified는 모든 참조의 좌표/발췌/원문 줄이 맞고 같은 카드·페이지 청크에서 연결됨, invalid_source는 이번 엄격한 좌표·관계 규칙에서 거절됨, unmapped는 원문/청크 연결 미확인이다. verified가 많으면 보완 가능성이 높지만 의미 정확도 점수는 아니다. 예: 커피 fact의 10%를 다른 줄의 10%로 대신 연결하면 안 된다. 원좌표가 유일한 원문 위치의 힌트로만 쓰이는 운영 처리와 달라 정상 fact도 제외할 수 있다. DB 전체3,403개를 전수 재검증한 값이 아니다.']
    lines.append(table(['프로필','대상 fact','verified / 연결 확인','invalid_source / 엄격 검사 제외','unmapped / 연결 미확인'],
        [[short[p],sum(preparation['by_profile'][p]['mapping'].values()),*[preparation['by_profile'][p]['mapping'].get(k,0) for k in ['verified','invalid_source','unmapped']]] for p in profiles]))
    reasons=Counter(r['reason'] for r in prepared['mapping'].values())
    reasons_ko={'fragment does not match exact source coordinates':'원좌표와 발췌 불일치',
        'invalid fragment coordinates':'좌표 범위 유효성 미충족','all source-bound field lines covered':'원문 줄 전체 연결 확인',
        'required source lines not uniquely covered by same-card/page chunks':'같은 카드·페이지 청크에서 필요한 줄의 유일한 연결 미확인',
        'field reference outside relationship scope':'관계 범위 밖 필드 참조'}
    lines.append('연결·제외 사유(프로필별 동일 fact는 별도 집계): '+ '; '.join(f'{reasons_ko[k]}: {v}' for k,v in reasons.items())+'.')
    lines+=['## 2. 실제 근거 변경 범위',
      'Treatment scope(실제 변경 범위)의 분모는 프로필별48쌍이다. append_only는 기존 근거를 모두 유지한 추가, replacement는 일부 기존 근거가 빠진 재구성, same_payload는 실제 답변 입력 동일, mapping_unavailable은 확인된 연결이 없어 A 유지, fallback_unchanged는 기존 비구조화 복귀 경로 유지다. 품질 통과율이 아니며 같은 입력에는 새 API 호출을 하지 않았다. 예: 첫 기존근거+새근거가5개 상한을 채우면 기존 나머지가 밀릴 수 있다.']
    lines.append(table(['프로필','추가만','교체 포함','입력 동일','매핑 미확인','기존 복귀 유지'],
      [[short[p],*[sum(r['profile']==p and r['effect_type']==kind for r in effects['records']) for kind in ['append_only','replacement','same_payload','mapping_unavailable','fallback_unchanged']]] for p in profiles]))
    lines+=['## 3. 기존 자동 평가 — 전체와 질문 유형별',
      'Automatic complete(기존 자동 조건 충족)는 양성 질문 분모44개이며 실패도0이다. 알려진 부분 정답 카드·인용·문자열 조건을 통과하면1로 높을수록 좋지만 목록 밖 정답과 바꿔 말하기를 놓칠 수 있다. 예: 할인 카드와 수치가 맞아도 목록 밖이면0일 수 있다. 43번의 사후 핵심요구44/44와 다른 지표다. Success는 예외 없이 답변 생성된 질문 수이고 정답률이 아니다. Citation ownership(인용 소유권)은 제공된 같은 카드 근거만 참조하면 통과하며 높을수록 좋지만 의미 정확성은 아니다. Unsupported %(비율 근거 미확인)는 claim의 비율을 인용에서 찾지 못한 건수로 낮을수록 좋지만 다른 혜택의 같은 숫자가 있으면 놓친다.',
      'Witness facts(계획 fact 근거 회수)는 해당 행에서 보완 대상으로 삼은 fact 중 엄격한 연결 검사와 최종 근거의 모든 원문 줄 포함을 만족한 수/대상 fact 수다. 높을수록 원문 연결이 많지만 같은 selector에서 파생한 진단이며 독립 정답/의미 정확도가 아니다. 매핑 제외 fact도 대상 분모에 남긴다. 같은 프로필 A/B에서만 비교하며 프로필 간 분모 차이를 품질 우열로 해석하지 않는다. 예: 커피의 대상·율·실적 줄을 모두 가져와야 회수로 센다.']
    lines.append(table(['프로필','질문 유형','방식','분모','Success','자동충족','인용소유권','미확인 % claim','Witness facts'],
       [[short[r['profile']],labels[r['group']],r['mode'],r['n'],r['success'],r['automatic_complete'],r['citation_ownership'],r['unsupported_percent_claims'],f"{r['witness_hit_facts']}/{r['witness_planned_facts']}"] for r in automatic['metrics']]))
    lines+=['전체 행은 양성44개, 유형별 행은 표의 분모에 적힌 해당 유형 질문 수를 사용한다. 카드 목록·순위를 고정했으므로 Hit/MRR 개선을 주장하지 않는다. A의 기존 자동 점수96행을 재계산해 저장값과 일치 확인했다. 기존42·43점수를 덮어쓰지 않았다.',
      '## 4. 변경된 답변의 원문 쌍대 검토',
      'Paired win/loss/tie(쌍대 개선/퇴행/동률)는 변경된18쌍을 방식명 없이 원문과 소유 인용으로 대조한 결과다. 양쪽 핵심요구 PASS/PARTIAL/FAIL/UNCERTAIN, 지원하는 적용 조건, 새 잘못된 주장·필수조건 손실·성공→실패를 본다. B의 새로운 오류/핵심요구퇴행/중요조건소실은 loss, 오류 없이 확인된 답변 개선은 win, 불명확은 uncertain이다. 예: 실적30만원을 정확히 추가하면 개선 후보지만 새 월한도를 잘못 쓰면 퇴행이다. 단순히 근거가 늘었다고 win으로 세지 않는다. 한 행이라도 퇴행/불확실이면 해당 프로필의 B를 선정하지 않는다. 검토자는 Codex이며 독립 사람 평가가 아니다.']
    lines.append(table(['프로필','변경 유형','검토쌍','개선','퇴행','동률','불확실'],
       [[short[r['profile']],labels[r['effect_type']],r['n'],r['win'],r['loss'],r['tie'],r['uncertain']] for r in manual['summary']]))
    lines+=['### 전체48쌍 기준 효과',
      '전체 쌍대 분모는 프로필별48이다. 실제 변경18쌍의 판정에 동일 답변 재사용78쌍의 확정 동률을 더한다. 미변경 답변을 새로 생성·독립 검토했다는 뜻은 아니다. parent-child 미변경 답변의 절대 수동 PASS율은 재검토하지 않아 미측정이다. 이 표는 같은 답변의 차이가0이라는 쌍대 비교만 포함한다.']
    lines.append(table(['프로필','전체쌍','개선','퇴행','동률','불확실'],[[short[r['profile']],r['n'],r['win'],r['loss'],r['tie'],r['uncertain']] for r in manual['overall_paired']]))
    lines+=['### 질문별 변경 결과',
      '분모는 실제 변경18쌍이다. A/B core는 원질문의 핵심 요구에 대한 사후 원문 판정이며 부수 주장 전체의 정확도 점수가 아니다. outcome은 바로 위의 쌍대 규칙을 적용한다. 구체적인 소유 인용·원문 근거는 source_reviews와 방식 가림 패킷에 연결한다.']
    kinds={(r['query_id'],r['profile']):r['effect_type'] for r in effects['records']}
    lines.append(table(['질문','프로필','변경','A core / 핵심','B core / 핵심','판정','원문 확인 내용'],[[r['query_id'],short[r['profile']],labels[kinds[(r['query_id'],r['profile'])]],labels[r['A_core']],labels[r['B_core']],labels[r['outcome']],r['finding']] for r in manual['records']]))
    diagnostics=[r for r in automatic['rows'] if r['diagnostic']]
    lines+=['## 5. 진단 질문과 실패',
      '진단 분모는 프로필·방식별4개(E45~E48)다. 이번에는 네 질문의 B 입력이 A와 같아 답변을 그대로 재사용했다. 새로운499% 혜택/미확인상점 추천을 생성하지 않았지만 새로운 실패복귀 정책이나 추출 오류 빈도를 검증한 것은 아니다. Success(실행 성공)는 정답과 구분하며 낮은 실패 수가 품질 보장은 아니다.']
    lines.append(table(['프로필','방식','진단 분모','실행 성공','입력 범위 내 출력'],[[short[p],m,4,sum(r['status']=='ok' for r in diagnostics if r['profile']==p and r['mode']==m),sum(r['fixed_card_scope_ok'] for r in diagnostics if r['profile']==p and r['mode']==m)] for p in profiles for m in ['A','B']]))
    lines+=['## 6. 비용·시간·자원',
      'API requests는 새45 캐시에 기록한 실제 물리 요청 수로 실패/재시도도 포함한다. Tokens는 각 물리 응답의 usage이며 input 중 cached/write는 세부 항목이라 input에 다시 합하지 않는다. 비용은 공식 Standard 단가로 계산한 신규 사용량 비용이고 청구서 실측은 아니다. 로컬 답변 재사용과 OpenAI cached input은 다르다. 예: 같은 패키지78쌍은 새 전송0이지만 신규 호출에 provider 캐시가 적용될 수도 있다. 품질이 같을 때 자원량이 작으면 유리하지만 오류 증가를 비용 절감으로 상쇄하지 않는다.']
    s=cost['summary'];tokens=s.get('tokens',{})
    token_labels={'input_tokens':'input_tokens / 전체 입력 토큰','output_tokens':'output_tokens / 출력·추론 토큰',
      'ordinary_input_tokens':'ordinary_input_tokens / 일반 입력 토큰','cached_input_tokens':'cached_input_tokens / 공급자 캐시 읽기 토큰',
      'cache_write_tokens':'cache_write_tokens / 공급자 캐시 쓰기 토큰','embedding_tokens':'embedding_tokens / 임베딩 토큰'}
    resource_rows=[['신규 물리 API 요청',s['record_count']],['가격 계산 가능/불가 응답',f"{s['known_priced_records']}/{s['unknown_cost_records']}"],
      ['신규 확인 가능 사용량 비용 USD',s['known_cost_usd']],['실제 청구서 비용','미측정'],['로컬 A답변 재사용 쌍',78],
      *[[token_labels[key],value] for key,value in tokens.items()],['답변 프로세스 경과 초',round(resources['wall_seconds_this_process'],3)],
      ['답변 프로세스 CPU 초',round(resources['cpu_seconds_this_process'],3)],['최대 RSS KiB',resources['peak_rss_kib']],
      ['신규 원응답 API 캐시 bytes',sum(f.stat().st_size for f in (I.OUT/'api_cache').glob('*.json'))],
      ['신규 모델·인덱스·청크 수',0],
      ['GPU/새 검색/BGE/임베딩/파서','미사용 / 모두0'],['Codex 원문 검토 시간·플랫폼 모델 사용료','미측정']]
    lines.append(table(['자원','측정값'],resource_rows))
    answered=I.read(I.OUT/'answers.json')['records'];profile_cost=[]
    for p in profiles:
        rows=[r for r in answered if r['profile']==p and r['origin']=='changed_B']
        keys={e['key'] for r in rows for e in r['events']}
        charged=[r for r in cost['records'] if Path(r['path']).stem in keys]
        profile_cost.append([short[p],len(rows),len(charged),sum(r['status']!='ok' for r in charged),
            str(sum((Decimal(r['cost_usd']) for r in charged if r['cost_usd'] is not None),Decimal(0))),
            round(sum(r['wall_seconds'] for r in rows),3)])
    lines.append('프로필별 분모는 실제 변경 답변11개와7개다. 요청 수는 실패·재시도를 포함한 물리 호출 수, 실패 수는 그중 비정상 응답, 처리 시간은 해당 답변 호출 경과 시간의 합계다. 같은 질문 반복 평균이 아니고 A의 과거 생성 비용은 이번 신규 비용에 합산하지 않는다.')
    lines.append(table(['프로필','변경 답변','물리 요청','실패 요청','신규 비용 USD','답변 처리 합계 초'],profile_cost))
    lines.append('공식 단가 확인일2026-10-20, 100만 토큰당 입력$0.10·cached input$0.01·cache write$0.125·출력$0.50. [OpenAI 모델 단가](https://developers.openai.com/api/docs/models/gpt-6-luna), [공식 가격표](https://developers.openai.com/api/docs/pricing). 프로세스 시간은 초기 보존 검사와 API 실행을 포함하며 원문 연결 준비·Codex 검토 전체 시간은 아니다. 단일 실행이며 반복 평균·표준편차는 미측정이다.')
    lines+=['## 원자료와 재현',
      '[45번 노트북](../../notebooks/45_evidence_supplement_comparison.ipynb) · [입력·근거 패키지](../../notebooks/data/45_evidence_supplement_comparison/prepared_packages.json) · [추가/교체 구분](../../notebooks/data/45_evidence_supplement_comparison/package_change_audit.json) · [실행 계약](../../notebooks/data/45_evidence_supplement_comparison/execution_contract.json) · [기존 자동 평가 전체192행](../../notebooks/data/45_evidence_supplement_comparison/automatic_evaluation.json) · [원문 쌍대 검토](../../notebooks/data/45_evidence_supplement_comparison/source_reviews.json) · [쌍대 결과](../../notebooks/data/45_evidence_supplement_comparison/source_review_summary.json) · [신규 비용 원장](../../notebooks/data/45_evidence_supplement_comparison/cost_accounting.json) · [검증 명령](../../notebooks/data/45_evidence_supplement_comparison/verification_commands.json). 노트북은 저장 결과와 검증 셀을 실행하며 유료 작업은 재개 가능한 별도45 runner에서 실행한다.',
      '## 최종 요약']
    verification=I.read(I.OUT/'verification_commands.json')
    lines.insert(-1,'검증: '+verification['final']['result']+'. 노트북의 네 코드 셀을 전체 실행했고 신규 원응답18개를 재파싱했다. 기존 파일25,069개의 해시·크기·수정시각을 보존했다. [최종 감사 기록](../../notebooks/data/45_evidence_supplement_comparison/completion_audit.json) · [독립 검토 기록](../../notebooks/data/45_evidence_supplement_comparison/final_review.json). 브라우저 시각 검사는 미측정이며 HTML 구조·로컬 링크를 확인했다.')
    decisions='; '.join(short[p]+': '+('A 유지' if manual['decision'][p]=='A' else 'B 개발 개선후보') for p in profiles)
    counts='; '.join(f"{short[r['profile']]} 개선{r['win']}·퇴행{r['loss']}·동률{r['tie']}·불확실{r['uncertain']}" for r in manual['summary'] if r['effect_type']=='all_changed')
    lines.append(f'원문 연결271개 중48개를 엄격한 규칙에서 확인했고,96쌍 중18쌍의 근거가 바뀌었다. 전체 카드·순위는 유지됐다. 실제 변경쌍의 결과는 {counts}이다. 적용 실적·한도 설명이 늘었지만, 첫달 제외·기존 한도 설명 손실, 근거를 잘못 붙인 조건, 실적 산정일·서비스 범위 해석 오류가 남았다. 선정 결과는 {decisions}이며 B 재구성 방식은 이번 기준에서 채택하지 않는다. 구조화→비구조화 검색 순서 자체를 거절한 결과가 아니며 청킹 우열도 새로 확정하지 않는다. 신규 확인 가능 API 사용량 비용은 USD {s["known_cost_usd"]}, 미가격 응답 {s["unknown_cost_records"]}개, 단일 답변 프로세스는 {resources["wall_seconds_this_process"]:.1f}초다. 운영/DB/기존 결과 변경이나 운영 채택은 없다. 원좌표 기반 연결의 낮은 적용 범위, 기존 A와 신규 B의 생성 차이, 개발 질문 재사용, OCR 통과 가정과 제목 계층 한계가 남아 있다. 다음 검증 후보는 기존 중요조건 보존과 동일 서비스 범위의 해석을 보장하도록 근거 선택·인용을 보완한 뒤 같은 A/B 기준으로 재비교하는 것이다. 이는 아직 실행하지 않은 후속 제안이며 일부 AND 활용·실패정책 변경은 별도 후속 범위다.')
    text='\n\n'.join(lines)+'\n';md=I.ROOT/'notebooks/45_evidence_supplement_comparison_report.md'
    md.write_text(text.replace('../../notebooks/','./'))
    body=markdown.markdown(text,extensions=['tables','fenced_code'])
    css='body{font:16px/1.7 system-ui,sans-serif;color:#20304a;background:#f5f7fb;margin:0}main{max-width:1400px;margin:24px auto;padding:28px;background:white}table{border-collapse:collapse;width:100%;font-size:14px;display:block;overflow-x:auto;margin:20px 0}th,td{border:1px solid #cbd5e1;padding:9px;vertical-align:top;min-width:75px}th{background:#edf3fb}pre{white-space:pre-wrap;padding:16px;background:#f1f5f9}a{color:#1458aa}h2{border-top:1px solid #dbe3ee;padding-top:20px}'
    HTML.write_text('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>45. 고정 카드 원문 근거 보완 비교</title><style>'+css+'</style></head><body><main>'+body+'</main></body></html>\n')
    return {'report':str(HTML.relative_to(I.ROOT)),'decisions':manual['decision']}


if __name__=='__main__':print(render())
