"""Render all conditions and paired changes from saved experiment artifacts."""
import html
import importlib.util
import json
from pathlib import Path
import sys
from collections import Counter

spec = importlib.util.spec_from_file_location('report_runtime', Path(__file__).with_name('41_followup_runtime.py'))
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)
NAMES = {'U': '비구조화 단독', 'S_THEN_U': '구조화 후 비구조화', 'S_PLUS_U': '두 검색 결과 융합',
         'Q_EXPAND_U': '검색문 보강 후 전체 검색', 'card_page_section_benefit': 'benefit · 혜택 단위',
         'parent_child_bundle': 'parent-child · 관련 청크 묶음'}
GUIDE = '''<p class="guide">지표 안내: Hit@3(정답 포함)은 원문에서 정답 카드를 확인한 질문 중 검색 상위3개에 해당 카드가 있는 질문 수입니다. 높을수록 좋으며 한 카드라도 포함되면 통과합니다. 예: 커피 할인 카드가 상위3개에 있으면 통과하지만 혜택 조건을 찾았다는 뜻은 아닙니다.
MRR(첫 정답 순위)은 같은 질문에서 첫 정답 카드 순위의 역수 평균이며 실패는0입니다. 높을수록 좋고 1위는1, 2위는0.5입니다. 카드가 맞아도 근거는 부족할 수 있습니다.
답변충족(KnownPositiveComplete)은 같은 양성 질문 중 정답 카드의 요구조건을 답변에서 함께 설명하고 인용·명시범위·수치 지원 검사도 통과한 질문 수입니다. 높을수록 좋고 자동판정 전부 충족 시 통과합니다. 예: 커피10% 할인과 월1만원 한도를 같은 카드 근거로 설명해야 합니다. 문자열·동의어·부정문과 큰 인용문의 숫자로 오탐/누락이 가능하며 의미적 사실 검증을 보장하지 않습니다.
실패는 전체 논리조건 중 검색/답변 실행에 실패한 수로 낮을수록 좋고0이 통과입니다. 예: 토큰/형식 실패는 점수 분모에서 제외하지 않습니다.
인용/범위 오류는 전체 질문에서 타 카드 인용 또는 명시 카드 밖 답변 수입니다. 낮을수록 좋고0이 통과지만 같은 카드의 잘못된 혜택은 놓칠 수 있습니다.
수치 진단은 인용에서 지원되지 않는 퍼센트 주장의 개수이며 낮을수록 좋습니다. 호텔567% 혜택을 부정한 문장도 오탐될 수 있어 원문 점검이 필요합니다.</p>'''

def aggregate(cases, retrievals, answers):
    look = {(r['query_id'], r['profile'], r['mode']): r for r in retrievals}
    labels = {q['query_id']: q for q in cases}
    profiles = list(dict.fromkeys(r['profile'] for r in answers))
    modes = ['U', 'S_THEN_U', 'S_PLUS_U', 'Q_EXPAND_U']
    rows = []
    for group in ['all', 'numeric', 'proper_noun', 'semantic']:
        for profile in profiles:
            for mode in modes:
                selected = [r for r in answers if r['profile'] == profile and r['mode'] == mode and
                            (group == 'all' or labels[r['query_id']]['subgroup'] == group)]
                if not selected:
                    continue
                positive = [r for r in selected if labels[r['query_id']]['gold_cards']]
                rr = [look[r['query_id'], profile, mode] for r in positive]
                rows.append({'group': group, 'profile': profile, 'mode': mode, 'all_n': len(selected), 'positive_n': len(positive),
                    'hit': sum(r.get('metrics', {}).get('anchor_hit_at_3') or 0 for r in rr),
                    'mrr': sum(r.get('metrics', {}).get('anchor_mrr') or 0 for r in rr) / len(rr) if rr else None,
                    'complete': sum(r.get('scores', {}).get('known_positive_complete') or 0 for r in positive),
                    'failures': sum(r['status'] != 'ok' or look[r['query_id'], profile, mode]['status'] != 'ok' for r in selected),
                    'ownership_failures': sum(r.get('scores', {}).get('citation_ownership') != 1 for r in selected),
                    'scope_failures': sum(r.get('scores', {}).get('explicit_scope_preserved') != 1 for r in selected),
                    'unsupported_percent_claims': sum(r.get('scores', {}).get('unsupported_percent_claims', 0) for r in selected)})
    pairs = []
    lookup = {(r['query_id'], r['profile'], r['mode']): r for r in answers}
    for profile in profiles:
        for first, second in [('U', 'Q_EXPAND_U'), ('S_THEN_U', 'Q_EXPAND_U'), ('S_PLUS_U', 'Q_EXPAND_U')]:
            changed = []
            totals = Counter()
            for q in cases:
                if not q['gold_cards']:
                    continue
                a = lookup[q['query_id'], profile, first].get('scores', {}).get('known_positive_complete') or 0
                b = lookup[q['query_id'], profile, second].get('scores', {}).get('known_positive_complete') or 0
                value = 'win' if b > a else 'loss' if b < a else 'tie'
                totals[value] += 1
                if value != 'tie':
                    changed.append({'query_id': q['query_id'], 'question': q['question'], 'result': value})
            pairs.append({'profile': profile, 'baseline': first, 'candidate': second, 'wins': totals['win'],
                          'losses': totals['loss'], 'ties': totals['tie'], 'changed': changed})
    return {'metrics': rows, 'paired': pairs}

def table(rows):
    heads = ['프로필', '검색 방식', '정답 포함', 'MRR', '답변충족', '실패', '인용/범위 오류', '수치 진단']
    body = ''
    for r in rows:
        values = [NAMES[r['profile']], NAMES[r['mode']], f"{r['hit']}/{r['positive_n']}",
                  f"{r['mrr']:.3f}" if r['mrr'] is not None else '해당 없음', f"{r['complete']}/{r['positive_n']}",
                  f"{r['failures']}/{r['all_n']}", f"{r['ownership_failures']}/{r['scope_failures']}", r['unsupported_percent_claims']]
        body += '<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in values) + '</tr>'
    return '<table><thead><tr>' + ''.join('<th>' + h + '</th>' for h in heads) + '</tr></thead><tbody>' + body + '</tbody></table>'

def render(stage=41):
    path = R.STAGES[stage]
    dataset = R.read(R.DATA / '39_answer_rubric/dataset.json') if stage == 41 else R.read(path / 'dataset.json')
    combined = R.read(path / 'combined_results.json')
    result = aggregate(dataset['queries'], combined['retrievals'], combined['answers'])
    R.write(path / 'evaluation_summary.json', result)
    summary = R.read(path / 'run_summary.json')
    title = '41 검색문 보강 후 전체 검색 비교' if stage == 41 else '42 질문 수 확대 확증 비교'
    body = f'<h1>{title}</h1><p>왜 실행했나: 조건 추출에서 얻은 표현을 검색문에 추가했을 때 기존 검색 방식보다 잘 찾는지 확인합니다.</p>'
    if stage == 41:
        body += '<p>개발 비교: 기존39의18질문 중 양성15개와 일반/미확인3개를 사용합니다. 기존40의108조건과 신규36조건을 연결했습니다. 새 질문 확증/독립 holdout/반복 생성 실험이 아닙니다.</p>'
    body += '<p>변경: 전체 검색의 내부 검색문에 검증된 대상 표현과 원문 혜택종류를 추가합니다. 고정: 사용자 원질문으로 답변 생성, 같은 자료·운영 청킹·모델·출력2400·Top3·재정렬 프로필·budget5. 숫자를 보강문에 추가하지 않습니다. 명시 카드 범위는 유지합니다.</p>'
    body += '<p>주의: 여행→호텔·커피→브랜드는 범주 확장입니다. 의미동일성 보장을 하지 않습니다. AND/OR를 검색문에 표현해도 벡터/BM25 순위가 논리조건을 강제하지는 않습니다. OCR 검증 통과 가정과 불완전한 제목 계층을 유지하며 청킹 외 프로필 차이도 있습니다.</p>'
    for group, label in [('all', '전체'), ('numeric', '수치·조건'), ('proper_noun', '카드 상세'), ('semantic', '의미·혜택 및 일반 대조')]:
        body += '<h2>' + label + '</h2>' + GUIDE + table([r for r in result['metrics'] if r['group'] == group])
    body += '<h2>질문별 개선과 퇴행</h2><p class="guide">승/패/동률은 동일 질문에서 후보의 답변충족0/1이 비교 기준보다 높음/낮음/같음을 뜻합니다. 양성 질문이 분모이며 실패0점입니다. 예: 기준 실패·보강 통과면 승1입니다. 자동판정 표현 차이도 승패로 잡힐 수 있으므로 원문 점검이 필요합니다.</p>'
    body += '<ul>' + ''.join(f"<li>{NAMES[p['profile']]} · {NAMES[p['baseline']]} → 보강: 승{p['wins']} / 패{p['losses']} / 동률{p['ties']}; 변경 질문 {html.escape(', '.join(c['query_id']+' '+c['result'] for c in p['changed']) or '없음')}</li>" for p in result['paired']) + '</ul>'
    body += '<h2>비용과 실행 자원</h2><p>신규 물리 요청과 캐시 재사용은 분리합니다. 비용은 기록된 provider 사용량에 단가를 적용한 환산값이며 실제 청구액은 미측정입니다. 단가 출처/날짜는 cost_accounting.json에 있습니다. 이전40 측정 시간은 이번 신규 실행 시간으로 합산하지 않습니다.</p>'
    body += '<pre>' + html.escape(json.dumps({'new_cost': summary['cost'], 'measured_resources': summary['resources']}, ensure_ascii=False, indent=2)) + '</pre>'
    name = '41_query_expansion_report.html' if stage == 41 else '42_expanded_query_evaluation_report.html'
    relative = '../../notebooks/data/' + path.name + '/'
    body += '<p>원자료: ' + ' · '.join(f'<a href="{relative}{f}">{f}</a>' for f in ['protocol.json', 'evaluation_summary.json', 'combined_results.json', 'cost_accounting.json', 'preservation.json']) + '</p>'
    body += '<h2>최종 요약</h2><p>모든 조건과 하위집단의 관측값을 공개했습니다. 적용 후보 판단은 새 질문 평가와 질문별 원문 점검을 함께 확인한 뒤 기록합니다. 운영 적용 없음. 같은 질문 재호출 변동은 미측정입니다.</p>'
    document = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>' + title + '</title><style>body{max-width:1200px;margin:32px auto;padding:0 20px;font:16px/1.7 sans-serif;color:#182230}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #ced5dd;padding:8px;text-align:left}.guide{background:#f1f5f9;padding:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere}h2{margin-top:32px}</style><body>' + body + '</body></html>'
    (R.ROOT / 'docs/report' / name).write_text(document)
    return result

if __name__ == '__main__':
    render()
