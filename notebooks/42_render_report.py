"""Expanded comparison, paired uncertainty and source-review qualification.

Only reads completed saved rows. Does not call models or change frozen scores.
"""
import html
from pathlib import Path
from collections import Counter
import json
import sys
import importlib.util

spec = importlib.util.spec_from_file_location('render42_runtime', Path(__file__).with_name('41_followup_runtime.py'))
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)
V = R.load('render42_layout', '41_render_report.py')
S = R.load('render42_stats', '42_statistics.py')
I = R.load('render42_binding', '42_input_binding.py')

def validate_decision(summary, review, decision, results_sha256):
    expected = {'combined_results_sha256': results_sha256,
                'evaluation_summary_sha256': R.digest(summary), 'quality_review_sha256': R.digest(review)}
    if decision.get('binding') != expected or review.get('combined_results_sha256') != results_sha256:
        raise RuntimeError('Decision is not bound to the current results and source review')
    if not review.get('coverage_complete') or review.get('scores_changed') is not False or decision.get('production_adopted') is not False:
        raise RuntimeError('Incomplete review, changed scores or unsupported production claim')
    rows = {(r['profile'], r['mode']):r for r in summary['metrics'] if r['group']=='all'}
    for key in ['raw_best', 'selected_configuration']:
        value = decision[key]
        actual = rows[(value['profile'], value['mode'])]
        if (value['complete'], value['positive_n']) != (actual['complete'], actual['positive_n']):
            raise RuntimeError('Decision score differs from current aggregation')
    best = decision['raw_best']
    if best['complete'] != max(r['complete'] for r in rows.values()):
        raise RuntimeError('Raw best is not the observed maximum')
    pair = next(p for p in summary['baseline_comparisons'] if p['candidate']==[best['profile'],best['mode']])
    expected_pair = {'wins':pair['wins'], 'losses':pair['losses'], 'ties':len(pair['ties']),
                     'net':pair['net_improvement'], 'exact_p_descriptive':pair['exact_p_descriptive']}
    if decision['baseline_paired_raw'] != expected_pair:
        raise RuntimeError('Decision paired changes differ from saved results')
    if decision['raw_minimum_net_rule_passed'] != (pair['net_improvement']>=2):
        raise RuntimeError('Decision minimum net guard differs')
    selected = decision['selected_configuration']
    baseline = ('card_page_section_benefit','S_THEN_U')
    # This source-qualified decision retains the reference. Equal automatic
    # scores cannot authorize a different configuration or a semantic claim.
    if ((selected['profile'],selected['mode']) != baseline or decision.get('enrichment_selected') is not False or
        decision.get('semantic_superiority_confirmed') is not False or
        decision.get('decision') != 'retain_reference_due_to_partial_gold_and_lexical_artifacts'):
        raise RuntimeError('Selected configuration or qualification flags contradict this source-reviewed decision')
    regressions = any(next(r for r in summary['metrics'] if r['group']==g and (r['profile'],r['mode'])==baseline)['complete'] -
                      next(r for r in summary['metrics'] if r['group']==g and (r['profile'],r['mode'])==(best['profile'],best['mode']))['complete'] >= 2
                      for g in ['numeric','proper_noun','semantic'])
    if decision['subgroup_regression_at_least2'] != regressions:
        raise RuntimeError('Decision subgroup guard differs')
    if review.get('diagnostic_expectations_passed') != 32 or decision.get('diagnostics_regression') is not False:
        raise RuntimeError('Diagnostic source review is not qualified')
    return True

def paired(cases, lookup, reference, candidate):
    wins, losses, ties, changes = [], [], [], []
    for q in cases:
        if not q['gold_cards']:
            continue
        a = lookup[(q['query_id'], *reference)].get('scores', {}).get('known_positive_complete') or 0
        b = lookup[(q['query_id'], *candidate)].get('scores', {}).get('known_positive_complete') or 0
        (wins if b > a else losses if b < a else ties).append(q['query_id'])
        if a != b:
            changes.append({'query_id': q['query_id'], 'question': q['question'], 'reference_score': a, 'candidate_score': b})
    return {'reference': list(reference), 'candidate': list(candidate), 'wins': wins, 'losses': losses, 'ties': ties,
            'net_improvement': len(wins) - len(losses), 'exact_p_descriptive': S.paired_exact(len(wins), len(losses)), 'changed': changes}

def build_summary():
    path = R.STAGES[42]
    I.verify_inputs(path)
    run = R.read(path / 'run_summary.json')
    if run['retrieval_rows'] != 384 or run['answer_rows'] != 384 or not R.read(path / 'preservation.json')['passed']:
        raise RuntimeError('Full384 and preservation evidence required before comparison')
    dataset = R.read(path / 'dataset.json')
    combined = R.read(path / 'combined_results.json')
    expected = {(q['query_id'], profile, mode) for q in dataset['queries'] for profile in V.NAMES if profile in ['card_page_section_benefit', 'parent_child_bundle'] for mode in ['U','S_THEN_U','S_PLUS_U','Q_EXPAND_U']}
    lookup = {(r['query_id'], r['profile'], r['mode']): r for r in combined['answers']}
    if len(lookup) != 384 or set(lookup) != expected:
        raise RuntimeError('Full matrix key mismatch')
    summary = V.aggregate(dataset['queries'], combined['retrievals'], combined['answers'])
    for row in summary['metrics']:
        row['completion_wilson_nominal95'] = S.wilson(row['complete'], row['positive_n']) if row['positive_n'] else None
    configurations = list(dict.fromkeys((r['profile'], r['mode']) for r in combined['answers']))
    comparisons = [paired(dataset['queries'], lookup, ('card_page_section_benefit', 'S_THEN_U'), c)
                   for c in configurations if c != ('card_page_section_benefit', 'S_THEN_U')]
    within = [paired(dataset['queries'], lookup, (p,'U'), (p,mode))
              for p in ['card_page_section_benefit','parent_child_bundle'] for mode in ['S_THEN_U','S_PLUS_U','Q_EXPAND_U']]
    groups = {}
    for label in ['shared_category_taxonomy', 'other_positive']:
        selected = [q for q in dataset['queries'] if q['gold_cards'] and bool(q['shared_category_taxonomy']) == (label == 'shared_category_taxonomy')]
        ids = {q['query_id'] for q in selected}
        groups[label] = V.aggregate(selected, [r for r in combined['retrievals'] if r['query_id'] in ids],
                                   [r for r in combined['answers'] if r['query_id'] in ids])['metrics']
    summary.update(baseline_comparisons=comparisons, within_profile_comparisons=within,
                   taxonomy_groups=groups, repeated_calls=0, independent_holdout=False,
                   positive_anchor_limit='up to3 reviewed card anchors; other valid cards can be scored as misses')
    R.write(path / 'evaluation_summary.json', summary)
    changed = sorted({r['query_id'] for p in comparisons + within for r in p['changed']})
    violations = [r for r in combined['answers'] if r.get('scores',{}).get('citation_ownership') != 1 or
                  r.get('scores',{}).get('explicit_scope_preserved') != 1 or r.get('scores',{}).get('unsupported_percent_claims',0)]
    diagnostics = [q['query_id'] for q in dataset['queries'] if q['expected_behavior']]
    R.write(path / 'quality_review_manifest.json', {'changed_positive_queries': changed,
        'guard_violation_conditions': [[r['query_id'],r['profile'],r['mode']] for r in violations],
        'diagnostic_queries': diagnostics, 'required': 'Inspect every configuration of changed questions, all guard violations and all4 diagnostic expectations; record valid unlabelled alternatives and rubric false failures separately; do not change frozen scores',
        'results_sha256': R.digest((path/'combined_results.json').read_bytes())})
    return summary

def render():
    from bs4 import BeautifulSoup
    path = R.STAGES[42]
    summary = build_summary()
    review = R.read(path/'quality_review.json') if (path/'quality_review.json').exists() else None
    decision = R.read(path/'decision.json') if (path/'decision.json').exists() else None
    if review and decision:
        validate_decision(summary, review, decision, R.digest((path/'combined_results.json').read_bytes()))
    # Reuse the stable stage41 layout; its renderer reads only this new stage.
    V.render(42)
    R.write(path/'evaluation_summary.json', summary)
    file = R.ROOT / 'docs/report/42_expanded_query_evaluation_report.html'
    soup = BeautifulSoup(file.read_text(), 'html.parser')
    soup.body.h1.find_next('p').string = ('왜 실행했나: 기존18질문의 개발 비교 뒤, 새48질문으로 검색 방식과 운영 프로필의 관측 성능을 확인하고 후보 선택의 근거를 넓히기 위해 실행했습니다. 같은 질문을 여러 번 생성하는 반복 시험과 구분합니다.')
    notice = soup.new_tag('p', attrs={'class':'guide'})
    notice.string = ('확증 범위: 새48질문(수치16·카드상세16·의미16),양성44·일반/모호/자료미확인4입니다. 같은106카드 자료이며 독립 비공개 holdout이 아닙니다. '
        '정답은 최대3카드의 원문검토된 partial anchor(부분 정답 기준)입니다. 목록 밖의 유효 카드도 자동 점수에서는 미포함으로 잡힐 수 있으므로 승패와 모든 방어 위반의 원문을 따로 확인합니다. '
        '74개 예비 anchor 혜택의 관계를 Codex와 읽기 전용 독립 검토자가 확인해72개를 유지하고2개를 평가 정답에서 제외했습니다. 원본 DB/OCR은 수정하지 않았습니다. '
        '이번 Q_EXPAND_U는 실제 추출 조건 재표현과 고정 DB 범주 표현 추가 정책의 평가입니다. 같은 질문을 재생성한 반복 실험은 아니며 실행별 분산은 미측정입니다.')
    soup.body.h1.insert_after(notice)
    for guide in soup.select('p.guide'):
        if 'Hit@3' in guide.get_text():
            guide.append(' 이 보고서의 정답 포함 분모는 검토된 부분 정답 카드가 있는 질문입니다. 모든 실제 관련 카드의 완전한 정답 목록을 뜻하지 않습니다.')
    # The final summary must be last, after source review and qualification.
    for heading in list(soup.find_all('h2')):
        if heading.get_text() == '최종 요약':
            following = heading.find_next_sibling('p')
            if following:
                following.decompose()
            heading.decompose()
    extra = soup.new_tag('section')
    rows = [r for r in summary['metrics'] if r['group']=='all']
    text = '<h2>관측 비율의 불확실성과 짝 비교</h2><p class="guide">Wilson95%는 양성44질문의0/1 답변충족 관측 비율로 계산한 명목 구간입니다. 예:44중40통과도 실제 모든 질문에서 성공한다는 보장이 아닙니다. 성공률은 높을수록 좋지만 구간만으로 통과/채택하지 않습니다. 같은 카드·템플릿·부분정답을 공유하므로 독립 모집단의95%보장으로 해석하지 않습니다. Exact McNemar는 같은 질문에서 두 설정 중 한쪽만 통과한 승/패로 계산한 양측 p값입니다. 예:승3·패0의 p값은0.25입니다. 작을수록 동률 가설과 덜 맞지만 여러 비교·공유질문·자동평가 오류가 있어 유의성만으로 채택하지 않습니다.</p><ul>'
    for r in rows:
        bounds=r['completion_wilson_nominal95']
        text += f"<li>{V.NAMES[r['profile']]} · {V.NAMES[r['mode']]}: 답변충족{r['complete']}/{r['positive_n']}, 명목 구간{bounds['lower']:.3f}~{bounds['upper']:.3f}</li>"
    text += '</ul><p>기준 후보는 benefit 구조화 후 비구조화 검색입니다. 아래 승/패/동률은 그 기준보다 답변충족이 높음/낮음/같음이며 양성44질문을 사용합니다.</p><ul>'
    for p in summary['baseline_comparisons']:
        text += f"<li>{V.NAMES[p['candidate'][0]]} · {V.NAMES[p['candidate'][1]]}: 승{len(p['wins'])} / 패{len(p['losses'])} / 동률{len(p['ties'])}, 순개선{p['net_improvement']}, 서술용 p={p['exact_p_descriptive']:.4f}</li>"
    text += '</ul><h2>공유 범주 정의와 표본 한계</h2><p>커피·여행5질문(E12/E13/E33/E34/E35)은 평가와 검색의 범주 어휘가 일부 공유됩니다. 이 하위집합의 개선을 확장 사전과 독립적인 의미 검증으로 해석하지 않습니다. 다른 양성39질문과 별도 결과를 남겼습니다. 새로운 문구·조건으로 질문을 늘렸지만 템플릿과 카드 자료가 겹치므로 모집단 일반화나 같은 질문 재호출 안정성을 보장하지 않습니다.</p>'
    for label, title in [('shared_category_taxonomy','공유 범주5질문'),('other_positive','나머지 양성39질문')]:
        text += '<h3>'+title+'</h3>'+V.GUIDE+V.table([r for r in summary['taxonomy_groups'][label] if r['group']=='all'])
    text += '<h2>선택 기준과 검토 상태</h2><p>보강 효과는 먼저 같은 프로필U보다 승이 패보다 많은지 확인합니다. 기준 후보 변경은 양성44개에서 순개선2개 이상·하위유형2개 이상 퇴행 없음·인용/범위/원문 확인된 잘못된 혜택의 방어 기준 통과를 요구합니다. 순개선0~1이면 기준을 유지합니다. 원문 검토 전에는 관측 최고와 선택을 구분합니다.</p>'
    if not review or not decision:
        text += '<p>현재 실행 결과의 원문 검토 및 최종 후보 판단을 진행 중입니다. 운영 적용 없음.</p>'
    else:
        text += '<p>'+html.escape(decision['summary_ko'])+'</p>'
        text += '<h2>원문 대조에서 확인한 차이</h2><p class="guide">이 표는 점수 변경표가 아닙니다. 양성44개 중 기준과 융합의 답변충족이 바뀐 네 질문과 방어 경고의 원인을 보여줍니다. 자동 통과는 고정 평가기의0/1이며 높을수록 좋지만 실제 의미 정확성을 보장하지 않습니다. 예: 유효한 GS25+넷플릭스 카드가 정답 목록 밖이면 자동0이 될 수 있습니다. 원문 확인은 혜택 대상·율·행동·인용 관계를 읽은 Codex 판단이고 사람의 완전한 PDF 검증이 아닙니다.</p>'
        text += '<table><thead><tr><th>질문</th><th>기준 순차 → 융합 자동점수</th><th>원문에서 확인한 의미</th></tr></thead><tbody>'
        for qid, score, finding in [
            ('E14 GS25+넷플릭스','0 → 1','기준도 taptap DIGITAL의 GS25 10%·넷플릭스50%로 조건 충족. 부분 정답 목록 미포함.'),
            ('E15 병원+약국','0 → 1','기준도 Mr.Life·Discount Plan+·롯데마트&MAXX의 유효 할인 근거를 답함. 부분 정답/표현 제약.'),
            ('E39 영화 할인','0 → 1','기준도 LOCA LIKIT Play 영화관60%를 답했지만 claim에 영화라는 단어가 없어 실패. 실제 회수 개선으로 확정 못함.'),
            ('E38 배달 적립','1 → 0','융합도 Point Plan의 주말 건당2만원 이상1천포인트를 정확히 답함. 부분 정답 목록 밖 유효 대안.'),
            ('E16 수치 경고','동결 유지','benefit 순차는7% 부정문 오탐. parent-child 융합의2%/10%는 다른 구성표인데 모순으로 설명함. 추천은 유보했으나 해석 오류는 남음.'),
            ('E31 / E34 합산 경고','동결 유지','수수료1%+0.3%=1.3%,가온 기본0.5%+커피추가0.3%=0.8%가 원문에 지원되지만 합산 등록 누락으로 경고.'),
            ('E02 parent-child 보강','자동1 유지','본문에서CU 적용을 유보했는데 claim 문자열 때문에 통과. 자동평가의 잘못된 통과.'),
            ('E18 / E19 상세','benefit1 / parent-child0','benefit은 쿠팡8%/4%·신세계5% 및 조건 근거를 회수. parent-child는 같은 카드의 구체 혜택 근거가 빠져 답변 유보.'),
        ]:
            text += '<tr>'+''.join('<td>'+html.escape(x)+'</td>' for x in [qid,score,finding])+'</tr>'
        text += '</tbody></table><p>승패가 바뀐16질문의 모든128설정, 방어 경고 E34의 해당 설정, 일반/모호/자료미확인4질문의32설정 등 필수161조건을 포함해168조건을 점검했습니다. 소유 근거를 가진 유효 대안·표현 오탐·근거 누락을 분리했고 동결 점수는 바꾸지 않았습니다. 모든384답변의 부수 문장까지 완전하게 의미 검증한 것은 아닙니다.</p>'
        text += '<p>일반 대조32답변은 모두 질문별 기대 행동을 충족했습니다. 할인/적립 비교 조언을 제공했고,생활비40만원만으로 무조건 최적카드를 확정하지 않았고,존재하지않는 상점과 호텔499%는 근거 미확인으로 답했습니다. 이32는 Codex 원문 검토 기록이며 양성44점수와 합산하지 않습니다.</p>'
    run = R.read(path/'run_summary.json')
    resources = R.read(path/'resource_details.json') if (path/'resource_details.json').exists() else None
    text += '<h2>실행 실패와 자원 해석</h2><p>384검색은 모두 성공했고383답변이 성공했습니다. E41 parent-child 융합1건은 duplicate_card_recommendation(같은 카드 중복 추천)으로 운영 답변 검증에서 거절됐습니다. 이 실패는0점으로 포함하며 빈 답변의 인용/범위 자동0을 실제 타 카드 인용 또는 카드 범위 이탈로 해석하지 않습니다. API 원응답 중 출력2400토큰에 도달한 실패2회도 있었지만 원래 재시도로 복구됐습니다. 모델의 같은 카드 중복 추천 실패는 출력 길이를 늘려 해결됐다고 볼 근거가 없습니다.</p>'
    if resources:
        text += (f'<p>신규 물리API307회: 조건 추출48회,답변256회(원응답 형식 통과254·출력 한도 실패2),임베딩 배치3회입니다. 답변 원응답 형식 통과 뒤 중복 추천 검사가 실패한2회가 있어 원응답 성공과 최종 답변 성공은 다릅니다. 답변 캐시 재사용132이벤트는 신규 물리 요청과 분리했습니다. 신규 임베딩{resources["new_embedding_vectors"]}개·3,364토큰,보강 적용34질문·원질문 복귀14질문입니다. 캐시 이벤트 followup35는 상속된 helper 이름이며 이번42의 동일 입력 캐시를 재사용했습니다.</p>')
        text += (f'<p>LLM 입력1,947,193토큰=캐시 쓰기1,254,855+캐시 입력691,426+일반 입력912,출력311,212토큰입니다. 비용USD{run["cost"]["known_cost_usd"]}는 실패·재시도를 포함한 신규 기록 사용량 환산입니다. 실제 청구액과 네트워크 전송 토큰 수는 미측정이며 provider 입력 사용량과 구분합니다. 단가는42 ledger의2026-10-11 기록을 사용했고,동일 단가를41에서2026-10-12 공식 문서로 재확인했습니다. 백만 토큰당 Luna 입력0.10·캐시 입력0.01·캐시 쓰기0.125·출력0.50달러,임베딩0.02달러입니다. <a href="https://developers.openai.com/api/docs/models/gpt-6-luna">모델 단가 공식 문서</a> · <a href="https://developers.openai.com/api/docs/models/text-embedding-3-small">임베딩 공식 문서</a>.</p>')
        text += (f'<p>경과{run["resources"]["wall_seconds_this_process"]:.1f}초(약125.6분),CPU시간{run["resources"]["cpu_seconds_this_process"]:.1f}초는 여러 스레드의 합계입니다. 조건 처리량은약{384/run["resources"]["wall_seconds_this_process"]:.3f}개/초,최고 프로세스RAM은약{run["resources"]["peak_rss_kib"]/1048576:.2f}GiB,최저 시스템 가용RAM은약{run["resources"]["minimum_available_kib"]/1048576:.2f}GiB입니다. BGE 신규2,925쌍을 온전한 문서1개씩CPU에서 처리했습니다. GPU미사용. 청크3183/1834개,후보20·상위3·청크 묶음최대5·모델/색인 구성 변경0입니다.</p>')
        text += f'<p>이번42 저장량 스냅샷은{resources["storage_bytes_excluding_private_indexes_and_execution_cache"]:,}bytes(독립 색인 복사/실행 캐시 제외,최종 감사 산출물 생성 전)입니다. 이전 자료의 삭제·이동은 없습니다. 표시용 노트북 전체4코드셀 실행은 저장 결과를 읽으며 유료 runner 전체 재실행을 뜻하지 않습니다. HTML 구조·링크 검사와 실제 브라우저 화면 확인은 구분하며 브라우저 렌더링은 미검증입니다.</p>'
    text += '<p>전체 원자료: <a href="../../notebooks/data/42_expanded_query_evaluation/evaluation_summary.json">평가·짝 비교 전체</a> · <a href="../../notebooks/data/42_expanded_query_evaluation/dataset.json">부분 정답 기준·표본</a> · <a href="../../notebooks/data/42_expanded_query_evaluation/source_relation_review.json">사전 원문 관계 검토</a> · <a href="../../notebooks/data/42_expanded_query_evaluation/quality_review_manifest.json">사후 원문 검토 대상</a></p>'
    text += '<p><a href="../../notebooks/data/42_expanded_query_evaluation/quality_review.json">질문·설정별 원문 검토</a> · <a href="../../notebooks/data/42_expanded_query_evaluation/decision.json">선택 근거와 한계</a> · <a href="../../notebooks/data/42_expanded_query_evaluation/resource_details.json">자원 상세</a> · <a href="../../notebooks/data/42_expanded_query_evaluation/completion_audit.json">최종 재계산·원본 보존 감사</a> · <a href="../../notebooks/data/42_expanded_query_evaluation/final_status.json">두 후속 실험 완료 상태</a> · <a href="41_query_expansion_report.html">이전41 개발 비교</a></p>'
    text += '<p>오프라인 회귀47검사가 통과했고,기존 파일20,631개의 내용·경로 보존과384개 원행/점수·비용을 재검증했습니다. 판정과 원문 검토는 결과 해시로 연결하며,오래된 판정·동점의 다른 설정 선택·근거 없는 의미 우위 주장도 변조 반례로 차단했습니다. 독립 코드 검토의 주요 발견 사항은 수정 후 해소됐습니다.</p>'
    text += '<h2>최종 요약</h2>'
    if decision:
        text += '<p>'+html.escape(decision['summary_ko'])+'</p>'
        text += '<p>같은 프로필의 비구조화 단독 대비 자동 답변충족은 benefit 순차+1·융합+3·보강+1,parent-child 순차+4·융합+4·보강0입니다. benefit과 parent-child의 차이를 청킹 하나의 효과로 단정하지 않습니다. 보강은 네이버쇼핑의 정확한 대상 근거 회수에서 퇴행했고,parent-child의 두 상세 질문은 구체 혜택 회수가 부족했습니다. 부분 정답과 표현 오탐 때문에 이 수치를 실제 의미 성공률로 해석하지 않습니다.</p>'
        text += f'<p>41 신규 비용USD0.020604415와42 USD{run["cost"]["known_cost_usd"]},두 후속 실험 합계USD0.340140030입니다.42 경과약125.6분·최고RAM약2.65GiB. 신규 호출을 다시 실행하지 않고 저장 결과로 평가했습니다. 같은 자료의 확증 평가이며 독립 비공개 평가·실행별 분산은 미측정,OCR 통과 가정·제목 계층 미완성은 그대로입니다. 다음 검증은 결과와 독립적으로 여러 유효 카드를 인정하는 정답/의미 평가를 고정해 benefit 순차와 융합의 실제 우열을 확인하는 것입니다. 운영 코드는 읽기 전용으로 유지했습니다.</p>'
    else:
        text += '<p>원문 검토와 후보 판정 진행 중. 운영 적용 없음.</p>'
    extra.append(BeautifulSoup(text,'html.parser'))
    soup.body.append(extra)
    file.write_text(str(soup))
    return summary

if __name__ == '__main__':
    render()
