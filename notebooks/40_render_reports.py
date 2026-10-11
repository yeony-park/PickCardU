"""Shared existing report styling and executable result-view notebooks."""
import html
import json
from pathlib import Path
import importlib.util
import sys
s=importlib.util.spec_from_file_location('render_audit_common',Path(__file__).with_name('37_followup_common.py'))
C=importlib.util.module_from_spec(s);sys.modules[s.name]=C;s.loader.exec_module(C)
R=C.load('prior_html_helpers','34_render_bundle_report.py')

def table(headers,rows):return R.table(headers,rows)
def save(n,name,title,body,artifacts):
    links=' · '.join(f'<a href="../../{html.escape(str(p.relative_to(C.ROOT)))}">{html.escape(p.name)}</a>' for p in artifacts)
    target=C.ROOT/f'docs/report/{n}_{name}_report.html'
    content=f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{R.STYLE}</style></head><body><main><h1>{n} {title}</h1>{body}<p>{links}</p></main></body></html>'
    target.write_text(content,encoding='utf-8');return target

def notebook(n,name,title,result_file):
    import nbformat
    nb=nbformat.v4.new_notebook()
    nb.cells=[nbformat.v4.new_markdown_cell(f'# {n} {title}\n\n운영 코드는 읽기 전용으로 사용합니다. 장시간 runner는 재개·캐시·원응답 보존을 담당하며 이 노트북은 단계별 저장 결과를 표시합니다. 전체 실행 명령은 계획 문서와 보고서를 참고하세요. API runner는 표시 셀에서 자동 실행하지 않습니다.'),
      nbformat.v4.new_code_cell("from pathlib import Path\nimport json\nroot=Path.cwd()\nif root.name=='notebooks': root=root.parent\nassert (root/'notebooks').is_dir()"),
      nbformat.v4.new_code_cell(f"result=json.loads((root/{str(result_file.relative_to(C.ROOT))!r}).read_text())\nprint(json.dumps({{k:v for k,v in result.items() if k not in ['records','queries','source_audit','retrieval','answers']}},ensure_ascii=False,indent=2)[:16000])"),
      nbformat.v4.new_code_cell("records=result.get('records',result.get('queries',[]))\nprint('저장 행 수:',len(records))\nprint(json.dumps(records[:3],ensure_ascii=False,indent=2)[:10000])")]
    nb.metadata={'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'}}
    path=C.ROOT/f'notebooks/{n}_{name}.ipynb';nbformat.write(nb,path)
    C.initialize(n)
    import contextlib,io
    namespace={};count=0
    for cell in nb.cells:
        if cell.cell_type!='code':continue
        count+=1;buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer):exec(compile(cell.source,str(path),'exec'),namespace)
        cell.execution_count=count
        cell.outputs=[nbformat.v4.new_output('stream',name='stdout',text=buffer.getvalue())]
    nbformat.write(nb,path)
    C.write(C.STAGES[n]/'notebook_execution.json',{'code_cells':3,'all_code_cells_executed':all(c.get('execution_count') for c in nb.cells if c.cell_type=='code'),
       'runner_reexecuted_by_notebook':False,'execution_method':'All display cells executed sequentially in shared Python namespace; no Jupyter kernel',
       'notebook_sha256':C.digest(path.read_bytes())})

def render37():
    n=37;data=C.read(C.STAGES[n]/'guard_results.json')
    body='<h2>목적과 고정 조건</h2><p>운영 검증이 인용 오류와 혜택 내용 오류를 어디까지 막는지 확인했습니다. 동일 카드·근거의 정상 대조군에서 한 오류씩 바꿨습니다. 고정 출력 주입이므로 실제 LLM 오류 발생률은 측정하지 않습니다. 운영 코드·OCR·청킹은 수정하지 않았습니다.</p>'
    body+=table(['오류/대조군','내용 정답','검증 수락','검사 단계','잘못된 수락'],[[r['case'],r['source_correct'],r['accepted'],r['decision_stage'],r['false_accept']] for r in data['records']])
    body+='<p class="guide">accepted / 수락은 운영 출력 형식·인용 소유권 검사를 통과했다는 뜻이며 내용 정답과 다릅니다. false accept / 잘못된 수락은 잘못된 출력 중 통과한 건수로 낮을수록 좋습니다. false reject / 잘못된 거부는 정상 출력 중 거부된 건수로 낮을수록 좋습니다. 스타벅스10% 근거를 인용하면서99%라고 답하면 내용은 틀려도 인용 소유권은 맞을 수 있습니다. 표본은 의도적 주입11개이며 실제 오류율로 일반화할 수 없습니다.</p>'
    body+=table(['실제 답변 함수 진단','provider 시도','최종 오류'],[[r['case'],r['provider_attempts'],r['error'] or '없음'] for r in data['retry_records']])
    body+='<p class="guide">provider 시도는 주입된 SDK 호출 횟수이며 유료 API 요청 수가 아닙니다. 형식/인용 실패의 원래 최대2시도와 의미 오류의 첫 시도 통과를 비교합니다. 재시도는 같은 검색 근거로 답변을 다시 만들며 제외된 카드를 다시 검색하지 않습니다.</p>'
    body+='<h2>결과와 최종 정리</h2><p>인용·형식 오류 방어는 작동하지만 같은 카드의 잘못된 혜택률·혜택 종류·기간·실적·본문 수치는 통과하는 방어 공백을 확인했습니다. 근거0이면 실제 API endpoint는 답변 provider 호출0으로 근거 부족을 반환했습니다. HTTP 전송은 검사하지 않았습니다. TestClient가 이 환경의 AnyIO thread portal 시작에서 대기해 stack을 확인한 뒤 동일 실제 route endpoint를 직접 호출했습니다. 신규 API0·요금0이며 CPU/메모리/전체 시간은 미측정입니다. 다음38번에서 조건 추출 오류로 후보가 빠지는 영향을 확인합니다.</p>'
    save(n,'operational_guard_audit','운영 답변 검증 방어 범위',body,[C.STAGES[n]/'guard_results.json',C.ROOT/'notebooks/37_followup_plan.md'])
    notebook(n,'operational_guard_audit','운영 답변 검증 방어 범위',C.STAGES[n]/'guard_results.json')

if __name__=='__main__':render37()

def render38():
    n=38;d=C.read(C.STAGES[n]/'condition_results.json')
    assert d['completed']==d['expected']==11
    body='<h2>목적과 고정 조건</h2><p>조건 추출의 감지된 실패와 감지되지 않은 의미 오류를 구분하고, 원문 정답→정답 조건 selector→오류 조건 selector→최종 검색의 손실을 나눴습니다. 기존 질문·임베딩과 benefit 운영 검색을 사용했으며 오류는 통제 주입입니다. 답변 생성과 신규 API 호출은 없습니다.</p>'
    body+=table(['조건','복귀','원문 양성 카드','정답조건 후보','오류조건 후보','추출로 추가 손실','실제 검색범위 손실','최종 정답 포함'],[
        [r['case'],r['fallback'],r['gold_count'],len(r['gold_selector_cards']),len(r['faulty_selector_cards']),len(r['additional_parser_gold_loss']),
         len(r['effective_scope_gold_loss']),r['retrieval'].get('metrics',{}).get('anchor_hit_at_3','실패/평가 없음')] for r in d['records']])
    body+='<p class="guide">원문 양성 카드는 이전32의 보수적 부분 정답이며 질문별 분모가 다릅니다. 정답조건 후보와도 차이가 있으면 자료 정규화·selector의 기존 손실입니다. 추출로 추가 손실은 정답조건에서 남았지만 오류조건에서 빠진 카드 수입니다. 복귀 후 실제 검색범위 손실은 다시 넓힌 범위에서 빠진 양성 카드 수입니다. Hit@3 / 정답 포함은 상위3카드에 알려진 양성이 하나라도 있으면1, 없으면0으로 높을수록 좋습니다. 스타벅스 할인→적립 오류는 형식에 맞고 후보도 있어 전체 검색으로 돌아가지 않습니다. 후보 손실은 최종 답변 오류와 같지 않고 이 실험은 답변을 생성하지 않았습니다.</p>'
    actual=d['actual_prior33'];positive=[r for r in actual if r['gold_count']]
    body+=table(['기존33 실제 추출 진단','통과/분모'],[
      ['호출·형식 성공',f"{sum(r['status']=='ok' for r in actual)}/{len(actual)}"],
      ['검색 경로 일치',f"{sum(r['metrics']['route_match'] for r in actual if r['status']=='ok')}/{len(actual)}"],
      ['조건 전체 일치',f"{sum(r['metrics']['condition_exact_match'] for r in actual if r['status']=='ok')}/{len(actual)}"],
      ['카드 범위 일치',f"{sum(r['metrics']['card_scope_match'] for r in actual if r['status']=='ok')}/{len(actual)}"]])
    body+='<p class="guide">이 표는 기존33의 실제 단일 LLM 추출 기록을 재사용한 이력이며 이번에 재호출한 결과가 아닙니다. route / 검색 경로,condition exact match / 모든 조건 일치,card scope / 지정 카드 범위가 이전 수동 기준과 같으면 통과입니다.33질문이 분모이며 높을수록 좋습니다. 프롬프트에 없는 조건 표현과 부분 정답 때문에 완전한 의미 정확도는 보장하지 않습니다. 통제 오류11개의 빈도를 실제 LLM 오류율로 해석하지 않습니다.</p>'
    body+=f'<h2>최종 정리</h2><p>감지된 실패·미해결·후보0·일반 조언은 비구조화로 복귀합니다. 감지되지 않은 혜택 종류·숫자·대상 오류는 제한 검색을 계속할 수 있고, 잘못된 명시카드 범위는 복귀에도 남을 수 있습니다. AND→OR는 후보를 늘리며 오추천 위험을 만들 수 있습니다. 신규 API0·API요금0, 새 BGE 계산쌍{d["bge_new_pairs"]}개입니다. CPU 전력·최고메모리·전체실행시간은 미측정입니다. 운영 변경은 없으며 다음39번에서 평가 기준과 새 질문을 먼저 고정합니다.</p>'
    save(n,'query_condition_audit','검색 조건 오류와 복귀 경로',body,[C.STAGES[n]/'condition_results.json',C.STAGES[n]/'protocol.json'])
    notebook(n,'query_condition_audit','검색 조건 오류와 복귀 경로',C.STAGES[n]/'condition_results.json')

def render39():
    n=39;d=C.read(C.STAGES[n]/'dataset.json');contract=C.read(C.STAGES[n]/'scorer_contract.json')
    body='<h2>목적과 변경·고정 조건</h2><p>기존 문자열 평가의 합산값·분리 주장·상세 질문 문제를 보완하고 새 질문 비교 전에 기준을 고정했습니다. 원문 근거·질문·평가 해시를 저장하고 검색 결과는 정답 작성에 사용하지 않았습니다. 운영 답변 검증을 강화하는 변경이 아니라 실험 평가의 새 버전입니다. 이전 점수와 직접 비교하지 않습니다.</p>'
    body+=table(['새 질문','성격','평가 응답형','원문 양성 카드'],[[q['query_id']+' '+q['question'],q['subgroup'],q['task'],len(q['gold_cards'])] for q in d['queries']])
    body+='<p class="guide">양성 카드는 원문 fact와 Upstage 페이지의 fragment / 해당 부분을 대조한 보수적 부분 정답입니다.15질문이 양성,3질문은 조언·모호한 요구·원문 양성 없음으로 별도 평가합니다. 질문마다 알려진 양성 중 하나를 반환하면 검색 정답 포함을 통과합니다. 원문 양성0은 실제 혜택이 없다는 증거가 아닙니다. 새로 작성한 동일 코퍼스 확증 질문이며 비공개 holdout / 독립 검증 자료가 아닙니다.</p>'
    body+='<h2>평가 규칙과 회귀 확인</h2><p>허용 합산은 같은 카드·같은 혜택·같은 단위의 사전 등록식만 인정하며 모든 피연산자 근거가 인용돼야 합니다. Decimal 정확한 합계이며 반올림은 허용하지 않습니다. 분리된 실적·한도 주장은 같은 카드이고 해당 혜택 주장과 인용을 공유해야 합니다. 상세 질문은 추천 배열 없이도 대상 카드의 근거 있는 주장으로 평가합니다.12개 회귀 fixture / 고정 예제가 구현 전 실패하고 구현 후 통과했습니다.</p>'
    body+='<p class="guide">primary metric / 주 지표인 답변 조건 동시 충족은 양성15질문 중 알려진 카드의 요구 조건·추가 설명·인용 소유권·퍼센트 근거가 모두 충족하면1입니다. 높은 값이 좋고 실패 답변은0입니다. 예: 삼성 taptap O의 스타벅스 할인율·실적·월 한도를 같은 카드의 인용 근거로 설명해야 합니다. 숫자가 큰 인용 문단의 다른 혜택에 있는 경우 같은 의미의 근거로 잘못 인정될 수 있어 완전 의미 검증이 아닙니다. explicit scope / 명시카드 범위와 unsupported percent / 근거 없는 퍼센트 주장을 별도 확인합니다.</p>'
    body+=f'<h2>최종 정리</h2><p>새18질문과 평가 버전을 고정했고 원문 fact{len(d["source_audit"])}개를 대조해 핵심 fragment 불일치{len(d["source_fragment_exclusions"])}개와 필수 필드 누락·불일치{len(d["required_fragment_exclusions"])}건을 정답 후보에서 제외하고 기록했습니다. 예외 문구의 추가 불일치는 별도 원문 감사에서 공개합니다. OCR 자체 통과와 제목 계층은 가정이며 PDF 독립 검증은 하지 않았습니다. 신규 API0·요금0입니다. 계산식 회귀는 고정 예제의 검증이며 새 질문의 계산 정확도 전체를 입증하지 않습니다. 다음40번에서 조건 추출18회와 두 프로필×세 검색108조건을 한 차례 비교합니다.</p>'
    save(n,'answer_rubric','답변 평가 보완과 새 질문 동결',body,[C.STAGES[n]/'dataset.json',C.STAGES[n]/'scorer_contract.json',C.STAGES[n]/'freeze_receipt.json'])
    notebook(n,'answer_rubric','답변 평가 보완과 새 질문 동결',C.STAGES[n]/'dataset.json')
