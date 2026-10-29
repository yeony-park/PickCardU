"""Render the full bundle matrix and execute its inspection notebook cells."""
from __future__ import annotations

from collections import defaultdict
import html
import importlib.util
import io
import json
from pathlib import Path
import sys
from contextlib import redirect_stdout

spec = importlib.util.spec_from_file_location('bundle_report_common', Path(__file__).with_name('34_followup_common.py'))
F = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = F
spec.loader.exec_module(F)

LABELS = {'fixed5': '기존 최대5개', 'fixed4': '최대4개', 'budget5': '토큰 확인·최대5개',
          'budget8': '토큰 확인·최대8개', 'U': '벡터+BM25', 'S_THEN_U': '구조화→비구조화',
          'S_PLUS_U': '두 결과 융합'}
SUBGROUPS = {'and': 'AND / 여러 혜택 모두', 'or': 'OR / 혜택 중 하나', 'boundary': 'boundary / 수치 경계',
            'cap': 'cap / 혜택 한도', 'category': 'category / 혜택 분야', 'negative': 'negative / 음성 대조',
            'numeric': 'numeric / 할인·적립 수치', 'proper_noun': 'proper_noun / 카드명',
            'semantic': 'semantic / 의미·추천', 'spend': 'spend / 전월 실적'}
STYLE = 'body{font:16px/1.7 system-ui,sans-serif;background:#f6f8fb;color:#17233b;margin:0}main{max-width:1180px;margin:32px auto;padding:28px;background:white;border-radius:16px}table{border-collapse:collapse;width:100%;margin:18px 0;font-size:14px}td,th{border:1px solid #ccd5e2;padding:9px;text-align:left;vertical-align:top}th{background:#eaf0f8}.guide{padding:14px;background:#eef6ff;margin:12px 0}.note{padding:14px;background:#fff7df;border-left:5px solid #d89b17}.best{background:#dcf5e7}.runner{background:#fff2cc}pre{white-space:pre-wrap}a{color:#1458aa}caption{text-align:left;font-weight:700}'


def table(headers, rows, caption='', classes=None):
    head = '<tr>' + ''.join(f'<th>{html.escape(str(v))}</th>' for v in headers) + '</tr>'
    body = ''.join('<tr class="' + (classes[i] if classes else '') + '">' +
                   ''.join(f'<td>{html.escape(str(v))}</td>' for v in row) + '</tr>' for i, row in enumerate(rows))
    return '<div style="overflow-x:auto"><table><caption>' + html.escape(caption) + '</caption><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>'


def metric_guide():
    rows = [
        ['Failure / 실패', '전체 질문 실행 수 중 실패 수. 낮을수록 좋고0건이면 실행 통과.', '긴 묶음 때문에 카드 검색이 중단되면1건.', '성공만으로 카드·조건이 맞다고 증명하지 않음.'],
        ['Anchor Hit@3 / 정답 카드 포함', '알려진 정답 카드가 있는 질문 수를 분모로 Top3 포함 여부 평균. 높을수록 좋고 포함이면1.', '편의점10%의 알려진 정답 카드가3위 안이면 통과.', '부분 정답 목록이며 전체 시장 정답이나 조건 정확도가 아님.'],
        ['Anchor MRR / 첫 정답 순위', '같은 양성 질문 분모, 첫 정답 순위의 역수 평균. 높을수록 좋고1위는1.', '정답이2위이면0.5.', '추가 정답 회수와 근거 완전성을 보여주지 않음.'],
        ['Witness Recall / 필수 원문 회수', '필수 근거 주석이 있는 질문마다 알려진 정답 카드 중 필요한 원문 필드가 같은 카드의 한 근거에 있는 비율을 평균. 높을수록 좋음.', '편의점10%와 전월실적30만원을 모두 요구했다면 같은 카드 근거에 두 조건이 있어야 통과.', '문자열·부분 라벨 진단. 표 분할과 바꿔 말하기를 놓치고 조건 관계 의미를 검증하지 못함.'],
        ['Document Tokens / 묶음 토큰', 'BGE tokenizer로 직렬화 묶음 문서를 special tokens 없이 센 개수. 한도4096 이하가 실행 통과.', '청크3개가4553토큰이면 개수가 적어도 실패.', '최종 답변 모델의 토큰이나 비용이 아님.'],
        ['Pair Tokens / 질문과 묶음 토큰', '동일 tokenizer로 query+document에 special tokens를 넣은 개수. effective max8192 이하가 통과.', '문서4096에 질문·구분 토큰을 더한 입력도 한도 안이어야 함.', '문서4096 한도도 별도로 통과해야 함.'],
        ['Win/Loss/Tie / 개선·하락·동률', '같은 질문·모드·후보목록에서 기준과 선택 설정의 지표 차이를 세며 세 수 합이 비교 질문 분모.', '기준 실패0점에서 선택 설정 정답 포함1이면 개선1건.', '실행 실패 제거와 정상 질문의 의미 품질 개선을 분리해야 함.'],
    ]
    return '<div class="guide"><strong>지표 안내</strong>' + table(['지표', '분모·방향·통과 기준', '카드 혜택 예', '한계'], rows) + '<p>검색 실패는 Hit/MRR/Witness에서0점입니다. 전체 Hit/MRR 분모27개, Witness 분모24개이며 분할별 분모는 표에 표시합니다.</p></div>'


def aggregate(rows, cases):
    positive = {c['query_id'] for c in cases if c['gold_cards']}
    witness = {c['query_id'] for c in cases if c['witness_groups']}
    def mean(key, ids):
        used = [r for r in rows if r['query_id'] in ids]
        return sum(r.get('metrics', {}).get(key, 0) if r['status'] == 'ok' else 0 for r in used) / len(used) if used else 0
    return {'n': len(rows), 'failed': sum(r['status'] != 'ok' for r in rows),
            'positive_n': sum(r['query_id'] in positive for r in rows),
            'witness_n': sum(r['query_id'] in witness for r in rows),
            'hit': mean('anchor_hit_at_3', positive), 'mrr': mean('anchor_mrr', positive),
            'witness': mean('witness_recall_at_3', witness)}


def render():
    base = F.STAGES[34]
    result = F.read_json(base / 'retrieval_results.json')
    if len(result['records']) != 396:
        raise RuntimeError('Incomplete bundle matrix cannot be reported')
    cases = F.read_json(F.OLD[32] / 'dataset_freeze.json')['queries']
    selection = result['selection']
    selected = selection['selected_policy']
    audit = F.read_json(base / 'failure_token_audit.json')
    content = '<h1>34. 청크 묶음 실패와 묶음 정책 비교</h1>'
    content += '<p class="note">OCR 검증 통과를 가정한 고정 Upstage106문서와 현재 미완성 제목 계층을 사용합니다. 개별 청크·임베딩·운영 소스를 바꾸지 않았습니다. 구조화→비구조화의 기존 parent-child 검색·답변은33/33 성공했으므로 다른 검색 경로의 실패 해결만으로 그 답변 품질 열세를 설명할 수 없습니다.</p>'
    content += '<h2>왜 실행했는가·확인할 질문</h2><p>Q05/Q28의 parent-child 검색 실패가 청크 개수 때문인지, 합친 문서 토큰 때문인지 확인하고 청크를 자르지 않는 선택 방식이 실행·근거 품질을 개선하는지 비교했습니다.</p>'
    content += '<h2>변경·고정 조건과 주의점</h2><p>변경 변수는 묶음 생성 정책뿐입니다. 기존 최대5개, 최대4개, 토큰 확인 최대5개/8개를 비교합니다. seed는 필수이며 원래 관련 청크→다른 후보 순서로 전체 청크를 선택합니다. 너무 긴 seed는 명시 실패로 남깁니다. 개별 청크 길이·본문·4096 문서 한도·8192 질문과 문서 입력 한도·BM25/벡터0.6/0.4·Top50/D20/Top3·BGE와 최종 카드당 근거 최대5개를 고정했습니다. 최대8개는 재정렬용 묶음 상한이며 답변 근거8개를 보장하지 않습니다.</p>'
    content += '<p>초기 재실행에서 Q10의 Chroma 근사 검색 후보 순위가 달라져 엄격 재현 검사가 중단되었습니다. 최종 카드·근거는 같았지만 내부 후보 변동이 있었습니다. 성공했던33번의 BM25/vector 전체 후보를 고정하고 실패했던 Q05/Q28 U만 현재 운영 검색기에서1회 생성했습니다. 이 신규 후보에서도 기존 실패를 재현했습니다. 각 질문의 정책 비교는 동일 후보이며 전체 평균은 과거·현재 후보가 섞인 기술 진단입니다. 매 조건마다 검색 엔진을 새로 실행한 전체 경로 평가로 해석하지 않습니다.</p>'
    content += '<p>개발20개에서 설정을 선택·해시 동결한 뒤 나머지13개를 실행했습니다. 모두 이전에 결과를 확인한 질문이므로13개는 post-selection verification / 설정 선택 후 확인이며 독립 확증·holdout이 아닙니다. 전체 행렬은 사후 탐색 결과입니다.</p>'
    diag = []
    for row in audit['records']:
        for bundle in row['over_document_limit']:
            diag.append([row['query_id'], bundle['card_key'], len(bundle['selected_chunk_ids']),
                         bundle['document_tokens'], bundle['pair_tokens'],
                         ', '.join(str(c['raw_text_tokens']) for c in bundle['chunks']), row['status']])
    content += '<h2>실패 원인 재현</h2>' + table(['질문', '카드', '실제 청크 수', '묶음 토큰', '질문+묶음', '개별 본문 토큰', '상태'], diag)
    content += '<p class="guide">청크 수는 실제 선택한 전체 청크 개수로 작을수록 좋다는 품질 기준이 아닙니다. 묶음 토큰은 본문·카드명·제목·경로 표기를 모두 포함하며4096 이하가 통과입니다. 개별 본문 토큰 합과 표기의 토큰 수는 다를 수 있습니다. Q28은3개라도4553토큰이라 실패했습니다.</p>'
    content += metric_guide()
    summaries = []
    for split in ['all', 'development', 'confirmatory']:
        relevant_cases = cases if split == 'all' else [c for c in cases if c['split'] == split]
        ids = {c['query_id'] for c in relevant_cases}
        label = {'all': '전체·사후 진단', 'development': '설정 선택20개', 'confirmatory': '선택 후 확인13개'}[split]
        for policy in ['fixed5', 'fixed4', 'budget5', 'budget8']:
            for mode in ['U', 'S_THEN_U', 'S_PLUS_U']:
                rows = [r for r in result['records'] if r['policy'] == policy and r['mode'] == mode and r['query_id'] in ids]
                metric = aggregate(rows, relevant_cases)
                summaries.append({'split': label, 'policy': policy, 'mode': mode, **metric})
    all_values = sorted({r['witness'] for r in summaries if r['split'] == '전체·사후 진단'}, reverse=True)
    ranked = all_values[:2]
    classes = ['best' if r['split'] == '전체·사후 진단' and r['witness'] == ranked[0] else
               'runner' if len(ranked) > 1 and r['split'] == '전체·사후 진단' and r['witness'] == ranked[1] else '' for r in summaries]
    content += '<h2>모든 조건별 결과</h2><p>초록은 전체 필수 원문 회수율의 관측 최고, 노랑은 서로 다른 다음 값입니다. 운영 채택이나 개발 집합에서 선택된 설정과 같은 뜻이 아닙니다.</p>'
    content += table(['범위', '묶음 정책', '검색', '실행/실패', '양성/근거 분모', 'Hit', 'MRR', 'Witness'],
        [[r['split'], LABELS[r['policy']], LABELS[r['mode']], f'{r["n"]}/{r["failed"]}',
          f'{r["positive_n"]}/{r["witness_n"]}', f'{100*r["hit"]:.2f}%', f'{r["mrr"]:.4f}',
          f'{100*r["witness"]:.2f}%'] for r in summaries], classes=classes)
    content += '<p class="guide">실행/실패는 전체 시도 수와 실패 건수입니다. Hit/MRR은 알려진 양성 질문, Witness는 필수 근거 주석 질문을 분모로 합니다. 높을수록 좋고 실패는0입니다. 편의점 정답 카드만 찾고 실적 조건을 못 찾으면 Hit는 통과해도 Witness는 낮아집니다.</p>'
    candidates = selection['candidates']
    content += '<h2>개발 집합 선택 규칙과 결과</h2><p>Q05/Q28 U·융합 실패 제거, 신규 실패0, 구조화 선행 Hit 통과 질문의 하락 없음, 필수 원문 회수 평균 하락 없음이 통과 조건입니다. 통과 후보 중 구조화 선행 근거 회수율→묶음 토큰 합→사전 동률 순서로 선택했습니다.</p>'
    content += table(['정책', '실패 수', '두 질문4실패 해결', 'Hit 하락 질문', '구조화 선행 Witness', '묶음 토큰 합', '조건 통과'],
        [[LABELS[r['policy']], r['failed_rows'], '해결' if r['target_repaired'] else '미해결', ', '.join(r['s_then_hit_losses']) or '없음',
          f'{100*r["s_then_witness"]:.2f}%', r['document_tokens'], '통과' if r['passes'] else '미통과'] for r in candidates])
    content += '<p class="guide">실패 수의 분모는20질문×3검색=60행입니다. 두 질문4실패 해결은 Q05/Q28 각각 U와융합의4행이 모두 성공한 경우입니다. 토큰 합은 실행에 구성한 재정렬 묶음의 합이며 답변 API 토큰·비용이 아닙니다. 품질 조건을 먼저 통과해야 작은 값이 유리합니다.</p>'
    lookup = {(r['query_id'], r['policy'], r['mode']): r for r in result['records']}
    wins = []
    details = []
    for mode in ['U', 'S_THEN_U', 'S_PLUS_U']:
        for metric, key, allowed in [('Hit', 'anchor_hit_at_3', [c for c in cases if c['gold_cards']]),
                                     ('Witness', 'witness_recall_at_3', [c for c in cases if c['witness_groups']])]:
            counts = defaultdict(int)
            for case in allowed:
                a, b = lookup[(case['query_id'], 'fixed5', mode)], lookup[(case['query_id'], selected, mode)]
                va = a.get('metrics', {}).get(key, 0) if a['status'] == 'ok' else 0
                vb = b.get('metrics', {}).get(key, 0) if b['status'] == 'ok' else 0
                outcome = '개선' if vb > va else '하락' if vb < va else '동률'
                counts[outcome] += 1
                if outcome != '동률':
                    details.append([case['query_id'], SUBGROUPS[case['subgroup']], LABELS[mode], metric, outcome,
                                    f'{va:.4f}', f'{vb:.4f}', '성공' if a['status']=='ok' else '실패', '성공' if b['status']=='ok' else '실패'])
            wins.append([LABELS[mode], metric, len(allowed), counts['개선'], counts['하락'], counts['동률']])
    content += '<h2>기준 대비 선택 설정의 질문별 차이</h2>' + table(['검색', '지표', '분모', '개선', '하락', '동률'], wins)
    content += '<p class="guide">같은 질문을 짝지어 지표 증가/감소/같음을 셉니다. Hit 분모27개, Witness24개입니다. 실패0점이 성공으로 바뀐 개선과 정상 실행 간 근거 변화를 상세 표로 구분합니다.</p>'
    content += table(['질문', '유형', '검색', '지표', '판정', '기준', '선택', '기준 상태', '선택 상태'], details)
    content += '<p class="guide">기준·선택은 같은 질문의 지표값으로 높을수록 좋습니다. Witness가0.2에서0.4로 증가해도 알려진 정답의 필수 원문 구성요소 회수 진단이며 답변 사실 정확도는 아닙니다.</p>'
    subgroup_rows = []
    for subgroup in sorted({c['subgroup'] for c in cases}):
        subset_cases = [c for c in cases if c['subgroup'] == subgroup]
        ids = {c['query_id'] for c in subset_cases}
        for policy in ['fixed5', selected]:
            for mode in ['U', 'S_THEN_U', 'S_PLUS_U']:
                rows = [r for r in result['records'] if r['policy'] == policy and r['mode'] == mode and r['query_id'] in ids]
                m = aggregate(rows, subset_cases)
                subgroup_rows.append([SUBGROUPS[subgroup], LABELS[policy], LABELS[mode], m['n'], m['failed'],
                                      f'{m["positive_n"]}/{m["witness_n"]}', f'{100*m["hit"]:.2f}%', f'{100*m["witness"]:.2f}%'])
    content += '<h2>질문 유형별 결과</h2>' + table(['원본 유형', '정책', '검색', '실행', '실패', '양성/근거 분모', 'Hit', 'Witness'], subgroup_rows)
    content += '<p class="guide">유형은 원래 질문 데이터의 numeric / 수치 조건, semantic / 의미·혜택, proper_noun / 카드명, negative / 음성 대조 등의 구분입니다. 각 행의 양성/근거 분모로 평균하며 분모0인0%는 평가 불가이지 품질0점이 아닙니다. 동일 표본의 진단이며 전반 개선이 특정 유형 하락을 가리지 않도록 함께 공개합니다.</p>'
    resources = {'new_llm_requests': 0, 'new_embeddings': 0,
        'new_scorer_pairs_this_final_run': sum(r['new_pairs'] for r in result['scorer_calls']),
        'unique_new_scorer_pairs_including_interrupted_runs': len(list((base / 'bge_cache').glob('*.json'))),
        'wall_seconds_final_run': result['wall_seconds'], 'independent_index_copy_bytes': F.read_json(base / 'index_copy.json')['bytes'],
        'storage_stage_bytes': sum(p.stat().st_size for p in base.rglob('*') if p.is_file()),
        'gpu': '미사용·CPU4threads', 'cpu_time': '미측정', 'peak_memory': '미측정',
        'billed_api_cost': 0, 'serving_latency': '미측정'}
    content += '<h2>자원·재현성·원본 보존</h2><pre>' + html.escape(json.dumps(resources, ensure_ascii=False, indent=2)) + '</pre>'
    content += '<p class="guide">new scorer pairs는 새로 BGE 점수를 계산한 질문·묶음 쌍 수로 API 요청이 아닙니다. 최종 실행 수와 중단된 초기 실행을 포함한 고유 캐시 수를 분리합니다. wall은 캐시가 섞인 단일 실행 초, bytes는 실제 저장 공간으로 낮을수록 자원 부담이 작지만 품질 통과가 우선입니다. 임베딩/API 새 요청0회이며 별도 모델 추론 비용은 청구되지 않았습니다. 운영 지연이나 반복 평균·표준편차는 미측정입니다.</p>'
    preservation = F.read_json(base / 'preservation_verification.json')
    content += '<p>보호 파일 ' + str(preservation['checked_files']) + '개, 변경 ' + str(len(preservation['changed'])) + '개, 누락 ' + str(len(preservation['missing'])) + '개입니다. 복사 SQLite4개 integrity check를 통과했습니다. /proc writer 확인은 도구의 실행 namespace에 한정되며 시스템 전체 writer 부재를 증명하지 않습니다. 원본 해시의 복사 전후 일치와 비어 있지 않은 WAL 검사로 보완했습니다.</p>'
    content += '<h2>최종 정리</h2><p>선택된 실험 설정은 <strong>' + LABELS[selected] + '</strong>입니다. 운영 채택은 없습니다. 실패 원인은 개수가 아니라 합쳐진 문서의 토큰 초과였으며, 단순 최대4개는 해당 실패를 해결하지 못했습니다. 통과하지 않은 설정의 이유는 개발 선택 표에 모두 공개했습니다. 질문별 하락과 비용·시간은 위 표를 확인하세요. 현재 제목 계층·OCR 통과 가정·부분 정답 라벨·근사 검색 변동이 남습니다. 다음은 동일 검색 근거에서 답변 재시도/출력 예산을 분리 비교하고 두 운영 프로필의 답변 품질을 확인하는35·36번입니다.</p>'
    content += '<ul>'
    for mode in ['U', 'S_THEN_U', 'S_PLUS_U']:
        old = next(r for r in summaries if r['split']=='전체·사후 진단' and r['policy']=='fixed5' and r['mode']==mode)
        new = next(r for r in summaries if r['split']=='전체·사후 진단' and r['policy']==selected and r['mode']==mode)
        content += '<li>' + LABELS[mode] + ': 실패 ' + str(old['failed']) + '→' + str(new['failed']) + '건, 정답 카드 포함 ' + f'{100*(new["hit"]-old["hit"]):+.2f}' + '%p, 필수 원문 회수 ' + f'{100*(new["witness"]-old["witness"]):+.2f}' + '%p.</li>'
    content += '</ul><p>최대8개는 개발 집합에서 구조화 선행 근거 회수율이 최대5개와 같으면서 묶음 토큰 합이 더 커 선택하지 않았습니다. 새 임베딩·API 요청0회이며 독립 인덱스 복사와 로컬 BGE 추론 자원이 추가됐습니다. 검색 실패 해결이 답변 품질 향상을 보장하지 않으므로 후속 답변 평가를 진행합니다.</p>'
    content += '<p><a href="../../notebooks/34_bundle_comparison.ipynb">34번 노트북</a> · <a href="../../notebooks/data/34_bundle_comparison/retrieval_results.json">전체396행 결과</a> · <a href="../../notebooks/data/34_bundle_comparison/failure_token_audit.json">실패 토큰 원문 진단</a> · <a href="../../notebooks/data/34_bundle_comparison/component_freeze.json">후보 동결·출처</a> · <a href="../../notebooks/data/34_bundle_comparison/selection.json">선택 조건 전체</a></p>'
    out = F.ROOT / 'docs/report/34_bundle_comparison_report.html'
    out.write_text('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>34 청크 묶음 비교</title><style>' + STYLE + '</style></head><body><main>' + content + '</main></body></html>', encoding='utf-8')
    F.write_json(base / 'report_summary.json', {'summaries': summaries, 'wins': wins, 'differences': details,
                  'resources': resources, 'selected_policy': selected, 'html_sha256': F.digest(out.read_bytes())})
    return out


def execute_notebook():
    root_cell = "from pathlib import Path\nimport importlib.util,json,sys\nROOT=next(p for p in [Path.cwd(),*Path.cwd().parents] if (p/'packages/rag-core').is_dir())\nspec=importlib.util.spec_from_file_location('notebook34_common',ROOT/'notebooks/scripts/34_followup_common.py')\nF=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)\nF.initialize(34)\nprint('approved environment',Path(sys.prefix).name)"
    cells = [root_cell,
        "audit=F.read_json(F.STAGES[34]/'failure_token_audit.json')\nfor row in audit['records']:\n print(row['query_id'],row['status'],[(b['card_key'],b['document_tokens']) for b in row['over_document_limit']])\nassert audit['new_llm_calls']==audit['new_embeddings']==0",
        "results=F.read_json(F.STAGES[34]/'retrieval_results.json')\nassert len(results['records'])==396\nassert all(r.get('original_replay_equal') for r in results['records'] if r['policy']=='fixed5')\nprint('selected',results['selection']['selected_policy'])\nfor candidate in results['selection']['candidates']:print(candidate)",
        "report=F.load_module('notebook34_report',ROOT/'notebooks/scripts/34_render_bundle_report.py')\npath=report.render()\nprint('report',path.relative_to(ROOT))\nprint('protected',F.read_json(F.STAGES[34]/'preservation_verification.json'))"]
    notebook = {'nbformat': 4, 'nbformat_minor': 5, 'metadata': {'kernelspec': {'display_name': 'Python (skn25)', 'language': 'python', 'name': 'python3'},
        'language_info': {'name': 'python'}, 'experiment': {'stage': 34, 'full_notebook_cells_executed': True,
        'runner_executed_separately': True}}, 'cells': []}
    intro = '34 청크 묶음 비교. 원래 청크/모델한도/운영 소스를 유지합니다. 모델 계산과 재개·캐시 관리는34_bundle_experiment.py가 담당하므로 장시간 작업을 노트북 셀만으로 다시 수행하지 않습니다. 먼저 승인된 skn25에서 `python -B notebooks/scripts/34_bundle_experiment.py all`을 실행하세요. 이 노트북은 모든 표시·감사 코드 셀을 실행해 저장된 실제 결과와 보고서를 확인합니다.'
    notebook['cells'].append({'cell_type': 'markdown', 'id': 'intro34', 'metadata': {}, 'source': [intro]})
    namespace = {}
    for index, code in enumerate(cells, 1):
        stream = io.StringIO()
        with redirect_stdout(stream):
            exec(compile(code, f'34_bundle_comparison.ipynb-cell{index}', 'exec'), namespace)
        notebook['cells'].append({'cell_type': 'code', 'id': f'code34{index}', 'metadata': {},
                                 'source': code.splitlines(keepends=True), 'execution_count': index,
                                 'outputs': [{'output_type': 'stream', 'name': 'stdout', 'text': stream.getvalue().splitlines(keepends=True)}]})
    destination = F.ROOT / 'notebooks/34_bundle_comparison.ipynb'
    destination.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    F.write_json(F.STAGES[34] / 'notebook_execution.json', {'code_cells_executed': len(cells), 'full_notebook_rerun': True,
        'paid_or_model_runner_rerun_in_notebook': False, 'python': sys.version, 'conda_environment': Path(sys.prefix).name,
        'notebook_sha256': F.digest(destination.read_bytes())})


if __name__ == '__main__':
    F.initialize()
    execute_notebook()
    print('34 notebook/report written')
