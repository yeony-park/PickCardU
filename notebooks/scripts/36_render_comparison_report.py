"""Publish selected six-condition results and explicitly separate diagnostics."""
from collections import Counter
from contextlib import redirect_stdout
import html
import importlib.util
import io
import json
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('comparison_report_common',Path(__file__).with_name('34_followup_common.py'))
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)
R=F.load_module('comparison_bundle_tables',F.ROOT/'notebooks/scripts/34_render_bundle_report.py')
A=F.load_module('comparison_answer_tables',F.ROOT/'notebooks/scripts/35_render_answer_report.py')
PROFILES=['card_page_section_benefit','parent_child_bundle'];MODES=['U','S_THEN_U','S_PLUS_U']
LABELS={**R.LABELS,**A.LABELS}


def combine(retrievals,answers,cases):
    return {**R.aggregate(retrievals,cases),**A.aggregate(answers,cases),
        'ownership':sum(r.get('metrics',{}).get('citation_ownership',0) for r in answers)/len(answers) if answers else None}


def percent(value):return '미평가' if value is None else f'{100*value:.2f}%'


def render():
    base=F.STAGES[36];primary=F.read_json(base/'primary_results.json');protocol=F.read_json(base/'protocol.json')
    cases=F.read_json(F.OLD[32]/'dataset_freeze.json')['queries']
    diagnostics=F.read_json(base/'primary_diagnostics.json');inspection=F.read_json(base/'quality_inspection.json')
    unknown=F.read_json(base/'unknown_results.json');hierarchy=F.read_json(base/'hierarchy_reference_audit.json')
    if len(primary['retrievals'])!=198 or len(primary['answers'])!=198 or len(unknown['records'])!=54:
        raise RuntimeError('Incomplete comparison cannot be published')
    retrievals=primary['retrievals'];answers=primary['answers']
    rb={(r['query_id'],r['profile'],r['mode']):r for r in retrievals}
    ab={(r['query_id'],r['profile'],r['mode']):r for r in answers}
    db={(r['query_id'],r['profile'],r['mode']):r for r in diagnostics['records']}
    body='<h1>36. 실패 해결 후 두 운영 프로필·세 검색 방식 비교</h1>'
    body+='<p class="note">선택한 실험 설정은 parent-child의 토큰 확인·최대5개 묶음과 답변 기본2400토큰입니다. 운영 채택은 없습니다. 고정 Upstage106문서의 OCR 통과를 가정하며 제목 계층·검증기는 수정하지 않았습니다. 기존33개 질문에서 나온 탐색 결과로, 새로운 독립 질문에서의 성능은 미검증입니다.</p>'
    body+='<h2>왜 실행했는가·확인할 질문</h2><p>검색·답변 실패를 제거했을 때 기존 청킹 프로필과 검색 방식의 차이가 남는지 확인합니다. 정상 응답 간 점수 차이를 근거·조건 설명·추천 형식으로 나누고, 구조화 선별의 확인 불가 필드를 가진 카드를 후보에 남겼을 때 누락을 복구하는지 별도 실험합니다.</p>'
    body+='<h2>변경·고정 조건과 주의점</h2><p>34번에서 묶음 정책4개를 비교해 토큰 확인 최대5개를 선택했고,35번에서는 기본2400·잘림 확인 시4800·처음부터4800을 비교해 기본2400을 선택했습니다. 선택은 개발20질문에서 동결한 뒤 이미 본 나머지13개에서 확인했습니다. 전체33개는 사후 진단이며 독립 확증이나 holdout / 처음 보는 평가셋이 아닙니다.</p>'
    body+='<p>기본6조건은 선택한34번 검색과35번의 정확히 연결된 답변198행을 재사용했습니다. 이 정리에 새 기본 답변 API는0회입니다. 개별 운영 청크·임베딩·BM25/벡터0.6/0.4·Top50/D20/Top3·BGE·4096문서/8192입력 한도·답변 프롬프트/스키마·카드당 근거 최대5개·64KB를 유지했습니다. benefit의 선택적 재정렬과 parent-child의 전체 질문 묶음 재정렬 차이도 유지한 운영 프로필 비교이므로 청킹 하나의 인과 효과는 아닙니다.</p>'
    body+='<p>34번에서 근사 검색 후보 변동을 확인해 저장된 BM25/vector 후보를 고정했습니다. 과거 실패 두 질문만 새 후보를1회 생성하고 기존 실패를 재현했습니다. 전체 비교는 과거·현재 후보가 섞인 조건부 비교이며 매번 새로 검색한 전체 경로 평가가 아닙니다. 범위 검색의 정확 L2와 전체 검색의 근사 L2 차이도 구조화 선별 효과와 함께 섞일 수 있습니다.</p>'
    body+=R.metric_guide()+A.guide()
    summaries=[]
    for split in ['all','development','confirmatory']:
        cohort=cases if split=='all' else [c for c in cases if c['split']==split];ids={c['query_id'] for c in cohort}
        for profile in PROFILES:
            for mode in MODES:
                rr=[r for r in retrievals if r['query_id'] in ids and r['profile']==profile and r['mode']==mode]
                aa=[r for r in answers if r['query_id'] in ids and r['profile']==profile and r['mode']==mode]
                summaries.append({'split':split,'profile':profile,'mode':mode,**combine(rr,aa,cohort)})
    best=sorted({r['complete'] for r in summaries if r['split']=='all'},reverse=True)
    classes=['best' if r['split']=='all' and r['complete']==best[0] else 'runner' if r['split']=='all' and r['complete']==best[1] else '' for r in summaries]
    body+='<h2>기본6조건 전체 결과</h2><p>초록은 전체 답변 조건 동시 충족의 관측 최고, 노랑은 다음 값입니다. 기본 구조화 선행 설정은 검증 후보이며 이 표의 최고를 운영 설정으로 채택하지 않습니다.</p>'
    body+=R.table(['범위','운영 프로필','검색','검색 실패/실행','답변 성공/실행','양성/근거 분모','Hit','MRR','Witness','답변 조건 동시 충족','조건 설명'],[
        [{'all':'전체·사후 진단','development':'개발20개','confirmatory':'선택 후 확인13개'}[r['split']],LABELS[r['profile']],LABELS[r['mode']],
         f'{r["failed"]}/{r["n"]}',f'{r["successes"]}/{r["attempts"]}',f'{r["positive_n"]}/{r["witness_n"]}',
         percent(r['hit']),f'{r["mrr"]:.4f}',percent(r['witness']),percent(r['complete']),percent(r['coverage'])] for r in summaries],classes=classes)
    body+='<p class="guide">전체 검색·답변 분모는 조건별33질문, Hit/MRR·답변 조건 동시 충족은 알려진 양성27개, Witness는 근거 주석24개입니다. 실패는0점으로 포함합니다. 선택 후 모든198검색·답변이 성공해 양쪽 성공 질문만의 공통 분모도 전체와 같습니다. 정답 카드 포함과 실제 조건 설명·근거 완전성은 별개입니다.</p>'
    wins=[]
    comparisons=[(p,'U',p,m) for p in PROFILES for m in ['S_THEN_U','S_PLUS_U']]
    comparisons += [(PROFILES[0],m,PROFILES[1],m) for m in MODES]
    for pa,ma,pb,mb in comparisons:
        for metric,field in [('Hit','anchor_hit_at_3'),('MRR','anchor_mrr'),('Witness','witness_recall_at_3'),('조건 동시 충족','known_positive_complete')]:
            counts=Counter();ids=[]
            for case in cases:
                if not (case['witness_groups'] if metric=='Witness' else case['gold_cards']):continue
                keya=(case['query_id'],pa,ma);keyb=(case['query_id'],pb,mb)
                a=ab[keya]['scores'].get(field) if metric=='조건 동시 충족' else rb[keya]['metrics'].get(field)
                b=ab[keyb]['scores'].get(field) if metric=='조건 동시 충족' else rb[keyb]['metrics'].get(field)
                outcome='개선' if b>a else '하락' if b<a else '동률';counts[outcome]+=1
                if outcome!='동률':ids.append(f'{case["query_id"]}:{outcome}')
            wins.append({'baseline_profile':pa,'baseline_mode':ma,'candidate_profile':pb,'candidate_mode':mb,
                'metric':metric,'n':sum(counts.values()),**{k:counts[k] for k in ['개선','하락','동률']},'changed_queries':ids})
    body+='<h2>같은 질문별 개선·하락·동률</h2>'+R.table(['기준','비교 대상','지표','분모','개선','하락','동률','바뀐 질문'],[
        [LABELS[r['baseline_profile']]+' / '+LABELS[r['baseline_mode']],LABELS[r['candidate_profile']]+' / '+LABELS[r['candidate_mode']],
         r['metric'],r['n'],r['개선'],r['하락'],r['동률'],', '.join(r['changed_queries']) or '없음'] for r in wins])
    body+='<p class="guide">기준과 비교 대상을 같은 질문에서 짝지어 높은 값·낮은 값·같은 값을 셉니다. 세 수 합이 분모이며 Hit/MRR/답변은27개, Witness24개입니다. 평균 상승과 별개로 바뀐 질문을 공개합니다. 작은 표본·부분 정답·단일 모델 응답의 한계가 있습니다.</p>'
    subgroups=[]
    for subgroup in sorted({c['subgroup'] for c in cases}):
        cohort=[c for c in cases if c['subgroup']==subgroup];ids={c['query_id'] for c in cohort}
        for profile in PROFILES:
            for mode in MODES:
                subgroups.append({'subgroup':subgroup,'profile':profile,'mode':mode,**combine(
                    [r for r in retrievals if r['query_id'] in ids and r['profile']==profile and r['mode']==mode],
                    [r for r in answers if r['query_id'] in ids and r['profile']==profile and r['mode']==mode],cohort)})
    body+='<h2>질문 유형별 기본6조건</h2>'+R.table(['유형','프로필','검색','질문/양성/근거','Hit','Witness','답변 조건 동시 충족','조건 설명'],[
        [R.SUBGROUPS[r['subgroup']],LABELS[r['profile']],LABELS[r['mode']],f'{r["n"]}/{r["positive_n"]}/{r["witness_n"]}',
         percent(r['hit']) if r['positive_n'] else '미평가',percent(r['witness']) if r['witness_n'] else '미평가',percent(r['complete']),percent(r['coverage'])] for r in subgroups])
    body+='<p class="guide">유형별 질문 수와 양성·근거 분모를 표시합니다. 정답 미주석 질문은 정답 카드 지표에서 미평가이며 오답으로 바꾸지 않습니다. 카드명 상세 질문의 빈 추천 목록은 정상 답변이어도 기존 추천 점수가0일 수 있습니다.</p>'
    body+='<h2>정상 응답인데 점수가 다른 네 질문</h2>'+R.table(['질문','질문 내용','benefit 점수','parent-child 점수','분류'],[
        [r['query_id'],r['question'],r['comparison'][0]['scores']['known_positive_complete'],r['comparison'][1]['scores']['known_positive_complete'],
         next(n['finding'] for n in inspection['notes'] if n['query_id']==r['query_id'])] for r in diagnostics['s_then_complete_disagreements']])
    body+='<p class="guide">구조화→비구조화의 같은33질문 중 조건 동시 충족1/0이 다른4개입니다. 양쪽 모두 검색·답변 실행은 성공했습니다. 이0/1은 실제 답변 의미의 정오 판정이 아니며 아래 근거 점검을 함께 읽어야 합니다.</p>'
    for note in inspection['notes']:
        body+=f'<h3>{html.escape(note["query_id"])} · {html.escape(note["finding"])}</h3><p>{html.escape(note["evidence"])}</p><p>{html.escape(note["conclusion"])}</p>'
    body+='<p>Q30 parent-child는 적립 유의사항을 반환했지만 편의점·통신사의7% 대상표가 들어간 benefit 근거와 범위가 다릅니다. 대상명·적립 단어 존재만으로 필요한 혜택 관계가 충분하다고 볼 수 없습니다. Q10은 원문 PDF 텍스트층에서 두 비율을 함께 자동 확인하지 못해 OCR 근거에 한정한 진단입니다. Q12·Q29·Q30 PDF 표본 페이지는 읽기 전용으로 확인했으며 전체 OCR 승인·현재 판매 혜택 검증을 뜻하지 않습니다.</p>'
    union=[]
    for profile in PROFILES:
        for mode in MODES:
            rows=[r for r in diagnostics['records'] if r['profile']==profile and r['mode']==mode and r['single_witness'] is not None]
            union.append({'profile':profile,'mode':mode,'n':len(rows),'single':sum(r['single_witness'] for r in rows)/len(rows),
                'union':sum(r['same_card_union_witness'] for r in rows)/len(rows)})
    body+='<h2>같은 카드의 여러 근거를 함께 읽는 사후 진단</h2>'+R.table(['프로필','검색','근거 질문 분모','원래 한 근거 기준','같은 카드 근거 모아 확인'],[
        [LABELS[r['profile']],LABELS[r['mode']],r['n'],percent(r['single']),percent(r['union'])] for r in union])
    body+='<p class="guide">Same-card union witness / 같은 카드 근거 합쳐 확인은 기존24개 근거 질문에서 알려진 정답 카드의 필수 원문 필드가 같은 카드의 개별 또는 합친 근거에 있는 비율을 질문별 평균합니다. 높을수록 좋고 편의점10%와 실적30만원이 별도 근거에 있어도 함께 찾으면 통과할 수 있습니다. 다른 카드 근거는 합치지 않습니다. 서로 다른 혜택·실적 구간이 합쳐져 잘못 통과할 수 있어 실제 관계·정확도 증명은 아닙니다. 사후 진단이며 원래 Witness·점수·설정 선택을 바꾸지 않았습니다. 작업 경로 제한에 따라 이 진단 정의는 이 보고서에 기록하고 공용 지표 문서는 변경하지 않았습니다.</p>'
    unknown_summary=[]
    for profile in PROFILES:
        rows=[r for r in unknown['records'] if r['profile']==profile];ids={r['query_id'] for r in rows};cohort=[c for c in cases if c['query_id'] in ids]
        before=combine([rb[(c['query_id'],profile,'S_THEN_U')] for c in cohort],[ab[(c['query_id'],profile,'S_THEN_U')] for c in cohort],cohort)
        after=combine([r['retrieval'] for r in rows],[r['answer'] for r in rows],cohort)
        unknown_summary.append({'profile':profile,'before':before,'after':after,
            'strict_mean':sum(r['strict_candidate_count'] for r in rows)/len(rows),
            'union_mean':sum(r['union_candidate_count'] for r in rows)/len(rows),
            'strict_known_retained':sum(r['strict_known_retained'] for r in rows),
            'union_known_retained':sum(r['union_known_retained'] for r in rows),
            'known_gold_units':sum(r['known_gold_count'] for r in rows)})
    body+='<h2>별도 실험: 확인 불가 카드 후보를 남기기</h2><p>해결된 구조화 질문27개×2프로필=54행에서 조건 충족 카드와 unknown / 값 확인 불가 카드를 합친 뒤 검색했습니다. 확인 불가는 조건을 충족한 것으로 표시하지 않습니다. 명시한 카드 범위를 유지하고 기본6조건 승자 선정에는 섞지 않습니다. 늘어난 범위의 후보는1회 동결하고 재개 시 재사용하도록 검증했습니다.</p>'
    body+=R.table(['프로필','평균 조건충족 후보→합집합','정답 후보 유지/카드·질문 단위','Hit 기준→추가','Witness 기준→추가','답변 동시 충족 기준→추가'],[
        [LABELS[r['profile']],f'{r["strict_mean"]:.2f} → {r["union_mean"]:.2f}',f'{r["strict_known_retained"]} → {r["union_known_retained"]} / {r["known_gold_units"]}',
         percent(r['before']['hit'])+' → '+percent(r['after']['hit']),percent(r['before']['witness'])+' → '+percent(r['after']['witness']),
         percent(r['before']['complete'])+' → '+percent(r['after']['complete'])] for r in unknown_summary])
    body+='<p class="guide">후보 평균 분모는 구조화 질문27개, 정답 후보 유지는 카드·질문 쌍으로 같은 카드가 다른 질문에서 다시 셉니다. Hit/답변 양성 분모24개, Witness24개입니다. 유지율은 높을수록 좋지만 후보 추가가 Top3 정답·근거 품질을 보장하지 않습니다. 엄격 후보가0이면 기존 방식은 전체106카드 검색으로 돌아갔으므로 합집합이 기존 실검색 범위를 오히려 좁힐 수 있습니다. 새 답변은 별도 모델 응답이어서 답변 차이에 생성 변동이 섞입니다.</p>'
    body+=R.table(['질문','프로필','조건충족/확인불가 포함','알려진 정답 유지','Hit 기준→추가','답변 동시 충족 기준→추가'],[
        [r['query_id'],LABELS[r['profile']],f'{r["strict_candidate_count"]}/{r["union_candidate_count"]}',f'{r["strict_known_retained"]}→{r["union_known_retained"]}/{r["known_gold_count"]}',
         str(r['baseline_metrics'].get('anchor_hit_at_3'))+' → '+str(r['retrieval']['metrics'].get('anchor_hit_at_3')),
         str(ab[(r['query_id'],r['profile'],'S_THEN_U')]['scores']['known_positive_complete'])+' → '+str(r['answer']['scores']['known_positive_complete'])] for r in unknown['records']])
    body+='<p class="guide">전체54조건을 공개합니다. None / 미평가는 정답 카드가 주석되지 않은 질문입니다. Q12 parent-child의 정답 카드 포함은1→0, Q30은 두 프로필 모두1→0입니다. Q30의 조건충족 후보0에서 기존 전체검색으로 돌아가던 범위를 확인불가1카드로 제한해 알려진 정답이 빠졌습니다. 모든 unknown을 일괄 추가하는 설정은 이 결과에서 채택하지 않습니다.</p>'
    body+='<h2>현재 parent-child 관계의 실제 상태</h2>'+R.table(['문서','청크','계층 노드','제목 수준','관련 청크 참조','참조·소유권·순환 검사','제목 의미 정확도'],[
        [hierarchy['documents'],hierarchy['chunks'],hierarchy['nodes'],json.dumps(hierarchy['heading_levels']),hierarchy['related_references'],
         '통과' if hierarchy['reference_integrity_passed'] else '미통과',hierarchy['semantic_heading_accuracy']]])
    body+='<p class="guide">문서106·청크1834·노드2183의 식별자 중복, 부모 참조, 카드 소유권, 순환 및 관련 청크8개 참조를 검사했습니다. 제목 수준0은 문서 루트106개, 수준1은2077개로 #→##의 다단계 관계는 현재 자료에 없습니다. 참조 오류가 없다는 것과 올바른 제목·혜택 관계를 갖췄다는 것은 다릅니다. 제목 의미 정확도는 미검증이며 관계 보완 후 성능 상승을 확정하지 않습니다.</p>'
    provider=[F.read_json(p) for p in (base/'api_cache').glob('*.json')]
    resource={'new_primary_provider_requests':0,'new_unknown_provider_requests':len(provider),
        'unknown_provider_successes':sum(r['status']=='ok' for r in provider),'unknown_provider_failures':sum(r['status']=='failed' for r in provider),
        'new_provider_tokens':A.token_totals(provider),'new_bge_pairs':sum(r['new_pairs'] for r in unknown['scorer_calls']),
        'new_embeddings':0,'unknown_measured_row_wall_sum_seconds':sum(r['wall_seconds'] for r in unknown['records']),
        'new_provider_wall_sum_seconds':sum(r.get('wall_seconds',0) for r in provider),'runner_wall_seconds':'미측정',
        'cpu_time':'미측정','gpu':'CPU 실행·GPU 미사용','peak_memory':'미측정','billed_cost':'미측정',
        'transmitted_tokens':'미측정','transmitted_payload_bytes':'미측정','throughput':'미측정',
        'model_size_bytes':sum(p.stat().st_size for p in (F.ROOT/'.cache/reranker/bge-reranker-v2-m3').rglob('*') if p.is_file()),
        'working_index_size_bytes':sum(p.stat().st_size for p in (F.STAGES[34]/'indexes').rglob('*') if p.is_file()),
        'api_cache_bytes':sum(p.stat().st_size for p in (base/'api_cache').glob('*.json')),
        'bge_cache_bytes':sum(p.stat().st_size for p in (base/'bge_cache').glob('*.json')),
        'stage_bytes':sum(p.stat().st_size for p in base.rglob('*') if p.is_file()),'component_records':len(unknown['component_manifest']),
        'benefit_chunks':3183,'parent_child_chunks':1834,'model_and_index_changes':0,
        'cached_stage35_input_provenance':'Previously measured35 tokens/cost are not charged again by this primary assembly.'}
    body+='<h2>측정한 자원과 실행 한계</h2><pre>'+html.escape(json.dumps(resource,ensure_ascii=False,indent=2))+'</pre>'
    body+='<p class="guide">새 요청은 실제36캐시의 고유 요청 수이며 캐시 재사용은 추가 요청으로 세지 않습니다. input / 입력, output / 출력, cached input / 입력 캐시, reasoning / 출력에 포함된 추론 토큰을 구분합니다. 새 재정렬 쌍은 BGE가 계산한 질문·묶음 쌍274개입니다. 행별 경과 초 합과 API 경과 초 합은 부분 측정으로 모델 초기화·전체 runner 시간은 포함하지 않습니다. model / 모델, working index /34번 독립 작업 인덱스, API·BGE cache / 응답·점수 캐시의 bytes / 저장 용량은 보고서 생성 시점 파일 크기 합계입니다. 전체35의166개 새 요청과 현재36의44개 새 요청을 분리했고 실제 비용·최고 메모리·처리량·네트워크 전송량·전송 토큰은 미측정입니다. 입력 토큰을 실제 네트워크 전송량으로 해석하지 않습니다.</p>'
    cost_data=A.COST.build()
    resource.update(usage_based_new_cost_usd=cost_data['stages']['36']['known_cost_usd'],
        usage_based_followup_cost_usd=cost_data['followup_34_36']['known_cost_usd'],
        recorded_all_known_cost_usd=cost_data['all_recorded_30_36']['known_cost_usd'],
        cost_kind='공식 단가로 환산한 기록 사용량 소계; 실제 청구액은 미측정')
    body+=A.COST.section(cost_data,'36',R.table)
    body+='<h2>운영 검증 후보에 대한 현재 판단</h2><p><strong>우선 검증할 운영 후보는 benefit + 질문 조건에 따른 구조화→벡터/BM25 검색 + 기본2400 답변입니다.</strong> 이는 현재 근거를 바탕으로 한 추천이며 사전에 등록한 최종 운영 선택이나 운영 채택이 아닙니다. 구조화 조건을 해석할 수 없는 질문과 후보가 비는 경우에는 기존 범위를 보존한 비구조화 검색으로 돌아갑니다. 확인불가 후보를 일괄 추가하지 않습니다.</p>'
    body+=R.table(['구분','프로필·검색','정답 포함/양성 질문','필수 근거 회수/근거 질문','답변 조건 동시 충족/양성 질문'],[
        ['우선 운영 검증 후보','benefit · 구조화→비구조화','27/27','41.13% /24','23/27'],
        ['답변 지표의 관측 최고','benefit · 두 결과 융합','27/27','35.89% /24','24/27'],
        ['관계 보완 후 비교할 대안','parent-child · 구조화→비구조화','27/27','39.07% /24','21/27']])
    body+='<p class="guide">정답 포함과 답변의 양성 분모는27질문, 필수 근거 분모24질문이며 높은 값이 좋습니다. 편의점 할인 카드를 찾는 것과 그 카드의 실적·한도를 함께 설명하는 것은 다릅니다. benefit 구조화 선행은 융합보다 근거 회수가 높습니다. 답변 통과의 차이는 Q10 한 질문이며 구조화 선행이1%+추가0.5%의 합산값1.5%를 출력했을 때 기존 평가가 인용의 개별1%·0.5%로부터 계산을 인정하지 못한 영향입니다. 융합의 실제 답변 품질 상승으로 확정하지 않습니다. 문자열 평가·작은 표본·미완성 제목 계층 때문에 확정 승자로 삼지 않습니다.</p>'
    body+='<p>구조화 선행은 기본 경로에서 별도의 두 검색 결과 융합 단계를 요구하지 않아 다음 운영 검증을 시작할 후보로 추천합니다. 다만 위 답변 요금 진단에서는 benefit 구조화 선행의 질문당 요금이 융합보다 조금 높습니다. 전체 CPU·조건 추출·임베딩·실서비스 지연은 비교하지 않았으므로 운영 총비용이 더 저렴하다고 단정하지 않습니다. 새로운 질문에서 근거·답변·비용을 함께 확인해야 합니다.</p>'
    body+='<h2>최종 정리와 다음 검증</h2><p>토큰을 확인하며 최대5개를 묶어 parent-child의 U/융합 검색 실패를 제거했습니다. 구조화 선행은 두 프로필 모두 정답 포함27/27, 첫 정답1위이며 원래 필수 근거 회수에서도 각각의 U·융합보다 높습니다. 답변 조건 동시 충족의 관측 최고는 benefit 융합24/27이고, 구조화 선행은 benefit23/27·parent-child21/27입니다. 검색 우세와 답변 우세가 완전히 같지 않고 점수 차이에는 합산·분리 claim·추천 형식 영향이 있어 청킹 승자는 확정하지 않습니다.</p>'
    body+='<p>실험 선택은 토큰 확인 최대5개·기본2400이며 운영 채택은 없습니다. 토큰 확인 최대8개는 개발 품질 차이 없이 묶음 토큰이 더 많아 선택하지 않았고, 처음부터4800은 개발 통과 질문7행이 하락해 선택하지 않았습니다. 확인 불가 후보의 일괄 추가는 후보·계산·API 부담을 늘리면서 정답 포함과 근거 품질을 낮춰 채택하지 않습니다. 비용·시간은 위 실제 측정값과34·35 보고서를 참고하며 청구액은 미측정입니다.</p>'
    body+='<p>남은 한계는 미완성 제목 계층, OCR 통과 가정, 부분 정답·문자열 평가, 과거/현재 후보 혼합, 프로필의 재정렬 차이와 모델 단일 응답 변동입니다. 다음 별도 검증은 상세 질문·분리 claim·계산 값을 다루는 평가 기준을 먼저 동결하고 새로운 질문에 적용하는 것입니다. 다른 세션에서 제목 계층이 보완되면 별도 번호·자료 버전으로 비교합니다. 질문을 보강한 뒤 전체 검색하는 방식은 요청대로 후속 실험에 남겨둡니다.</p>'
    links=[('36번 노트북','../../notebooks/36_profile_search_comparison.ipynb'),('기본198조건 요약','../../notebooks/data/36_profile_search_comparison/evaluation_summary.json'),
        ('기본 원문·출처','../../notebooks/data/36_profile_search_comparison/primary_results.json'),('확인 불가54조건 원문','../../notebooks/data/36_profile_search_comparison/unknown_results.json'),
        ('요청별 비용 계산·단가·범위','../../notebooks/data/36_profile_search_comparison/cost_accounting.json'),
        ('근거·PDF 표본 진단','../../notebooks/data/36_profile_search_comparison/quality_inspection.json'),('고정 조건','../../notebooks/data/36_profile_search_comparison/protocol.json'),
        ('34 묶음 비교','34_bundle_comparison_report.html'),('35 출력 예산 비교','35_answer_budget_report.html')]
    body+='<p>'+' · '.join(f'<a href="{url}">{name}</a>' for name,url in links)+'</p>'
    path=F.ROOT/'docs/report/36_profile_search_comparison_report.html'
    path.write_text('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>36 구조화 검색 후속 비교</title><style>'+R.STYLE+'</style></head><body><main>'+body+'</main></body></html>')
    F.write_json(base/'report_summary.json',{'summaries':summaries,'wins':wins,'subgroups':subgroups,'union_diagnostic':union,
        'unknown_summaries':unknown_summary,'resources':resource,'production_adopted':False,'html_sha256':F.digest(path.read_bytes())})
    return path


def execute_notebook():
    code=["from pathlib import Path\nimport importlib.util,sys\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents] if (p/'packages/rag-core').is_dir())\ns=importlib.util.spec_from_file_location('notebook36_common',ROOT/'notebooks/scripts/34_followup_common.py');F=importlib.util.module_from_spec(s);sys.modules[s.name]=F;s.loader.exec_module(F)\nF.initialize(36)\nprint('environment',Path(sys.prefix).name)",
        "p=F.read_json(F.STAGES[36]/'primary_results.json');assert len(p['retrievals'])==len(p['answers'])==198\nprint('primary API',p['new_primary_api_calls'])\nprint('selections',F.read_json(F.STAGES[36]/'protocol.json')['selected_bundle_policy'],F.read_json(F.STAGES[36]/'protocol.json')['selected_answer_budget_policy'])",
        "u=F.read_json(F.STAGES[36]/'unknown_results.json');assert len(u['records'])==54\nprint('unknown rows',len(u['records']))\nprint('hierarchy',F.read_json(F.STAGES[36]/'hierarchy_reference_audit.json'))\nfor n in F.read_json(F.STAGES[36]/'quality_inspection.json')['notes']:print(n['query_id'],n['finding'])",
        "R=F.load_module('notebook36_report',ROOT/'notebooks/scripts/36_render_comparison_report.py')\nprint('report',R.render().relative_to(ROOT))\nprint('preservation',F.read_json(F.STAGES[36]/'preservation_verification.json'))"]
    nb={'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'display_name':'Python (skn25)','language':'python','name':'python3'},
        'language_info':{'name':'python'},'experiment':{'full_notebook_cells_executed':True,'runner_executed_separately':True}},'cells':[
        {'cell_type':'markdown','id':'intro36','metadata':{},'source':['36 기본6조건과별도확인불가후보진단. 장시간 API·재정렬·후보동결·재개는36_comparison.py가 담당합니다. 승인된 skn25에서 `python -B notebooks/scripts/36_comparison.py`, 이어 `python -B notebooks/scripts/36_inspect_quality.py`를 실행합니다. 이 노트북의 모든 코드 셀은 결과 확인과 보고서 생성이며 유료 전체 실험을 다시 호출하지 않습니다.']}]}
    ns={}
    for i,source in enumerate(code,1):
        out=io.StringIO()
        with redirect_stdout(out):exec(compile(source,f'36_notebook-cell{i}','exec'),ns)
        nb['cells'].append({'cell_type':'code','id':f'code36{i}','metadata':{},'source':source.splitlines(keepends=True),
            'execution_count':i,'outputs':[{'output_type':'stream','name':'stdout','text':out.getvalue().splitlines(keepends=True)}]})
    path=F.ROOT/'notebooks/36_profile_search_comparison.ipynb';path.write_text(json.dumps(nb,ensure_ascii=False,indent=1)+'\n')
    F.write_json(F.STAGES[36]/'notebook_execution.json',{'code_cells_executed':4,'full_notebook_rerun':True,
        'paid_runner_rerun_in_notebook':False,'notebook_sha256':F.digest(path.read_bytes())})


if __name__=='__main__':
    F.initialize(36)
    execute_notebook()
    print('36 notebook/report written')
