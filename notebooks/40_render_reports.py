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
    body+='<p>계산 검증의 범위: 이번 합산 회귀는 percent_supported / 주장 퍼센트 근거 검사에 집중했으며 새18질문에는 등록된 합산식이 없습니다. requirement_complete / 요구 조건 충족의 최소 퍼센트 비교는 인용 원문의 개별 퍼센트만 사용하므로, 합산만으로 최소 비율을 충족하는 사례 전체가 해결됐다고 주장하지 않습니다. 동결 이후 발견한 제한은 공개하고 이번 실행의 채점·질문·프롬프트는 수정하지 않았습니다.</p>'
    body+=f'<h2>최종 정리</h2><p>새18질문과 평가 버전을 고정했고 원문 fact{len(d["source_audit"])}개를 대조해 핵심 fragment 불일치{len(d["source_fragment_exclusions"])}개와 필수 필드 누락·불일치{len(d["required_fragment_exclusions"])}건을 정답 후보에서 제외하고 기록했습니다. 예외 문구의 추가 불일치는 별도 원문 감사에서 공개합니다. OCR 자체 통과와 제목 계층은 가정이며 PDF 독립 검증은 하지 않았습니다. 신규 API0·요금0입니다. 계산식 회귀는 고정 예제의 검증이며 새 질문의 계산 정확도 전체를 입증하지 않습니다. 다음40번에서 조건 추출18회와 두 프로필×세 검색108조건을 한 차례 비교합니다.</p>'
    save(n,'answer_rubric','답변 평가 보완과 새 질문 동결',body,[C.STAGES[n]/'dataset.json',C.STAGES[n]/'scorer_contract.json',C.STAGES[n]/'freeze_receipt.json'])
    notebook(n,'answer_rubric','답변 평가 보완과 새 질문 동결',C.STAGES[n]/'dataset.json')

def render40_partial():
    """Report unfinished execution without constructing a full-grid quality claim."""
    n=40;directory=C.STAGES[n];C.initialize(n)
    recovery=C.read(directory/'memory_recovery.json');memory=C.read(directory/'memory_telemetry.json')
    answers=[C.read(p) for p in sorted((directory/'answers').glob('*.json'))]
    retrieval=[C.read(p) for p in sorted((directory/'retrieval').glob('*.json'))]
    runner=C.load('partial_cost_runner','40_new_query_comparison.py');ledger=runner.cost()
    from decimal import Decimal
    binding=C.read(directory/'memory_recovery_contract.json')
    baseline=runner.PRICE.aggregate([r for r in ledger['records'] if r['path'] in binding['before_manifest']])
    assert baseline['record_count']==binding['api_records']
    new_cost=str(Decimal(ledger['summary']['known_cost_usd'])-Decimal(baseline['known_cost_usd']))
    pending=108-len(answers)
    C.write(directory/'cost_accounting.json',ledger)
    result={'execution_complete':False,'completed_answers':len(answers),'completed_retrievals':len(retrieval),'expected':108,
      'pending_answers':108-len(answers),'repeated_runs':0,'physical_requests':ledger['summary']['record_count'],
      'known_cost_usd':ledger['summary']['known_cost_usd'],'unknown_cost_records':ledger['summary']['unknown_cost_records'],
      'billed_cost_usd':None,'status':'memory_headroom_stopped','memory':memory,'records':answers,
      'new_requests_on_latest_attempt':ledger['summary']['record_count']-recovery['physical_requests_before_resume'],
      'initial_known_cost_usd':baseline['known_cost_usd'],'known_new_cost_since_first_recovery':new_cost}
    C.write(directory/'partial_summary.json',result)
    body='<h2>목적과 미완료 상태</h2><p>37 운영 방어 범위,38 추출 오류와 복귀,39 평가·새 질문 동결을 마친 뒤 새18질문×두 운영 프로필×세 검색108조건을 실행하는 단계입니다. 현재 완료한 일부 질문으로 전체 조건의 우열을 판단하지 않습니다. 4번은 미완료이며 반복 답변 생성5는 실행하지 않았습니다.</p>'
    body+=table(['항목','현재 확인된 결과'],[
        ['검색 / 답변 완료',f"{len(retrieval)} / {len(answers)} (목표108)"],['아직 실행하지 못한 답변',108-len(answers)],
        ['저장된 물리 API 요청',ledger['summary']['record_count']],['사용량 기반 요금 환산 USD',ledger['summary']['known_cost_usd']],
        ['이번 재개에서 새로 호출한 API',result['new_requests_on_latest_attempt']],['첫 복구 계약 이후 추가 요금 환산 USD',new_cost],
        ['요금 미확인 요청',ledger['summary']['unknown_cost_records']],['실제 청구액','미측정'],
        ['재개 중 가용 메모리 KiB',memory['minimum_host_available_kib']],['중단 당시 필요 여유 KiB',memory['last_required_headroom_kib']],
        ['재개 중 신규 모델 점수 호출',memory['completed_singleton_calls']],['반복 답변 실험','실행하지 않음']])
    body+=f'<p class="guide">완료 행은 실제 저장된 검색·답변의 건수이며 목표108행이 분모입니다. 미실행{pending}행을 성공이나 정답으로 계산하지 않습니다. 물리 요청은 고유 저장 API 기록이며 캐시 재사용은 새 요청으로 세지 않습니다. 이번 요청 증가는 최신 실행 시작 시점의 저장 요청 수와 비교합니다. 추가 요금은 첫 복구 계약에 보존한41개 요청의 사용량 비용을 현재 알려진 소계에서 뺀 값입니다. 비용은 저장된 실제 사용 토큰×2026-10-11 고정 공식 단가이며 실제 청구액·세금·환율·CPU전력은 미측정입니다. 가용 메모리는 모델 호출 전 시스템 관측값이며 순간 최저값을 보장하지 않습니다. 필요 여유는 관측 최고RSS-currentRSS+512MiB 또는 최소1GiB 중 큰 값입니다. 예: 스타벅스 질문의 원문·청크·토큰 한도는 그대로 두고 모델에 문서를 한 개씩 넣더라도 PC 전체의 메모리가 충분해야 합니다.</p>'
    extra=len(answers)-binding['completed_rows']
    body+='<h2>중단 원인과 재개 조건</h2><p>첫 실행은 커널의 시스템 메모리 부족으로 종료 코드137을 받았습니다. 4,012토큰을 포함한5쌍 점수 비교에서 원래 저장 점수와 문서1개 호출 차이는 허용0.0001 이하였고 신규 API0회였습니다. 어댑터·프로토콜·기준 캐시 해시와 기존22행·41응답을 묶은 재개 계약은 유지합니다. 첫 재개는 신규 모델 호출 전에 여유 검사에서 멈췄습니다. 이후 준비 검사가 통과한 실행을 시작했습니다.</p>'
    body+=f'<p>첫 계약 이후 {extra}개 답변을 추가 완료했고 현재 {pending}개 답변이 남았습니다. 최신 실행은 메모리 여유 부족으로 중단됐으며 모델 점수 호출{memory["completed_singleton_calls"]}회를 관측했습니다. 순간 최고RSS는{memory["peak_rss_kib"]}KiB였고 이를 재개 전 필요 여유 계산에도 반영합니다. 모델 자체 또는 입력 토큰 오류로 판정하지 않습니다. 다른 프로세스·운영 코드·원문·청크 길이·토큰 제한은 바꾸지 않았습니다. 할당 해제 진단에서 반환한 약112MiB만으로는 충분한 여유를 입증하지 못했습니다.</p>'
    body+='<h2>재현과 다음 단계</h2><p>동일PC의 다른 작업을 정리해 시작 전 가용 메모리를 약3GiB 이상 확보한 뒤 기존40_memory_recovery.py로 재개합니다. 실제 실행 중 동적 여유 검사를 계속 적용하며3GiB도 메모리 부족 부재를 보장하지 않습니다. started/failed / 미완료·실패 API 기록이 있으면 자동 재호출을 막습니다. 정상 저장된 응답을 재사용하고 질문·프롬프트·채점·운영 청킹은 유지합니다. 가용 메모리 확인 전 반복 재시작하지 않습니다.</p>'
    body+='<pre>PYTHONDONTWRITEBYTECODE=1 conda run --no-capture-output -n skn25 python -B notebooks/40_memory_recovery.py</pre>'
    body+=f'<h2>현재 정리</h2><p>1~3번 진단과 평가 동결은 완료했고4번 전체 비교는 미완료입니다. 검색·청킹의 새 승자와 운영 채택은 없습니다. 관측된 방어 공백·조건 오류 영향은37·38 보고서에 공개했습니다. 메모리 진단과 재개 코드는 저장했지만 아직{pending}개 답변과 전체 비교·최종 독립 감사가 남아 있습니다. 비용은 위의 현재 알려진 소계이며 전체 실행 요금은 아닙니다.</p>'
    save(n,'new_query_comparison','새 질문 비교의 중단·미완료 현황',body,[directory/'partial_summary.json',directory/'memory_recovery.json',directory/'memory_telemetry.json',directory/'memory_parity.json',directory/'memory_allocation_diagnosis.json',directory/'cost_accounting.json',directory/'memory_recovery_contract.json',directory/'resume_memory_check.json'])
    notebook(n,'new_query_comparison','새 질문 비교의 중단·미완료 현황',directory/'partial_summary.json')

def render40():
    import statistics
    from collections import Counter
    n=40;summary=C.read(C.STAGES[n]/'run_summary.json');answers=C.read(C.STAGES[n]/'answer_results.json')['records']
    retrieval=C.read(C.STAGES[n]/'retrieval_results.json')['records'];dataset=C.read(C.STAGES[39]/'dataset.json')
    queries={q['query_id']:q for q in dataset['queries']};ledger=C.read(C.STAGES[n]/'cost_accounting.json')
    profiles=['card_page_section_benefit','parent_child_bundle'];modes=['U','S_THEN_U','S_PLUS_U']
    names={'card_page_section_benefit':'benefit / 혜택 청킹','parent_child_bundle':'parent-child / 관계 묶음',
           'U':'비구조화 단독','S_THEN_U':'구조화→비구조화','S_PLUS_U':'두 결과 융합'}
    rb={(r['query_id'],r['profile'],r['mode']):r for r in retrieval};ab={(r['query_id'],r['profile'],r['mode']):r for r in answers}
    groups=['all','numeric','proper_noun','semantic'];labels={'all':'전체','numeric':'수치·조건','proper_noun':'카드 상세','semantic':'의미·혜택'}
    rows=[];metrics=[]
    for subgroup in groups:
        cohort=[q for q in queries.values() if subgroup=='all' or q['subgroup']==subgroup]
        positive=[q for q in cohort if q['gold_cards']];witness=[q for q in positive if q['witness_groups']]
        for p in profiles:
            for mode in modes:
                rs=[rb[(q['query_id'],p,mode)] for q in cohort];ans=[ab[(q['query_id'],p,mode)] for q in cohort]
                hit=sum(rb[(q['query_id'],p,mode)].get('metrics',{}).get('anchor_hit_at_3') or 0 for q in positive)
                mrr=sum(rb[(q['query_id'],p,mode)].get('metrics',{}).get('anchor_mrr') or 0 for q in positive)/len(positive)
                wr=sum(rb[(q['query_id'],p,mode)].get('metrics',{}).get('witness_recall_at_3') or 0 for q in witness)/len(witness)
                k=sum(ab[(q['query_id'],p,mode)]['scores']['known_positive_complete'] or 0 for q in positive)
                entry={'subgroup':subgroup,'profile':p,'mode':mode,'attempted':len(cohort),'search_success':sum(r['status']=='ok' for r in rs),
                    'answer_success':sum(a['status']=='ok' for a in ans),'positive':len(positive),'hit':hit,'mrr':mrr,'witness_queries':len(witness),'core_witness_recall':wr,
                    'known_positive_complete':k,'citation_failures':sum(not a['scores']['citation_ownership'] for a in ans),
                    'scope_failures':sum(not a['scores']['explicit_scope_preserved'] for a in ans),
                    'unsupported_percent_claims':sum(a['scores'].get('unsupported_percent_claims',0) for a in ans)}
                metrics.append(entry)
                rows.append([labels[subgroup],names[p],names[mode],f"{entry['search_success']}/{len(cohort)}",f"{entry['answer_success']}/{len(cohort)}",
                  f'{hit}/{len(positive)}',f'{mrr:.3f}',f'{wr*100:.2f}% / {len(witness)}',f'{k}/{len(positive)}',
                  entry['citation_failures'],entry['scope_failures'],entry['unsupported_percent_claims']])
    body='<h2>왜 실행했는지와 고정한 조건</h2><p>37 운영 검증 방어 범위,38 조건 오류 진단,39 평가·새 질문 동결 뒤18질문×두 운영 프로필×세 검색108조건을 한 차례 비교했습니다. 이전33질문과 문구 중복은 없지만 같은 코퍼스에서 작성한 확증 자료이며 비공개 holdout / 독립 검증 자료는 아닙니다. OCR 통과 가정·미완성 제목 계층은 유지했고 운영 코드는 수정하지 않았습니다. 기본2400 출력·토큰 확인 최대5개 묶음·50개 채널 후보·20개 재정렬 후보·상위3카드·64KB 근거 예산을 고정했습니다. 두 프로필의 운영 재정렬 정책 차이와 글로벌 approximate / 근사 검색·범위 exact / 정확 검색 차이가 포함됩니다. 비교 동안 동일 질문·프로필·범위의 채널 후보는 한 번 캡처해 고정했습니다.</p>'
    body+='<p>U / 비구조화 단독도 LLM이 인식한 명시 카드 범위를 공유하므로 완전히 독립적인 전역 검색 기준이 아닙니다. 감지된 추출 실패·미해결·후보0이면 원래 범위를 보존한 비구조화로 복귀합니다. 동일 답변 입력은 캐시를 공유하고 반복 생성5는 실행하지 않았습니다.</p>'
    for subgroup in groups:
        levels=sorted({m['known_positive_complete'] for m in metrics if m['subgroup']==subgroup},reverse=True)
        for row,metric in zip(rows,metrics,strict=True):
            if metric['subgroup']!=subgroup:continue
            value=metric['known_positive_complete']
            row.insert(3,'관측 최고' if value==levels[0] else '차순위' if len(levels)>1 and value==levels[1] else '')
    body+='<h2>전체 조건과 하위집단</h2>'
    body+=table(['질문 집단','운영 프로필','검색','답변 지표 관측순위','검색 성공','답변 성공','정답 포함','첫 정답 순위 점수','핵심 근거 회수','답변 조건 충족','인용 실패','범위 실패','퍼센트 근거 미충족'],rows)
    guide='<p class="guide">search/answer success / 검색·답변 성공은 오류 없이 실행된 질문 수/시도 수이며 높은 값이 좋고 실패 행도 품질 분모에 포함합니다. Hit@3 / 정답 포함은 알려진 양성 카드가 상위3개에 하나라도 있으면1이며 양성15질문이 전체 분모입니다. MRR / 첫 정답 순위 점수는 첫 양성 카드 순위의 역수 평균이며1위=1,2위=0.5,없음·실패=0입니다. core witness recall / 핵심 혜택 근거 회수는 양성 카드의 대상·행동·값 원문이 같은 검색 근거에 존재하는 카드 비율을 질문별로 평균합니다(전체14질문). 이 새 진단은 실적·한도 전체의 의미 관계를 검증하지 않으며 이전32/36의 필수 근거 회수와 직접 비교하지 않습니다. 예: 스타벅스10% 할인 카드의 실제 할인 대상·행동·값을 검색 근거에서 찾습니다. 긴 원문과 분리 셀·부분 정답 때문에 과소/과대 평가할 수 있습니다.</p>'
    guide+='<p class="guide">known positive complete / 답변 조건 충족은 양성15질문에서 알려진 카드의 요구 조건·추가 설명·인용 소유권·명시 범위·퍼센트 근거 검사에 모두 통과하면1이며 높을수록 좋습니다. 상세 질문은 추천 배열이 비어도 대상 카드 주장으로 평가합니다. citation failure / 인용 실패와 scope failure / 범위 실패는 질문별 건수이며 낮을수록 좋습니다. unsupported percent / 퍼센트 근거 미충족은 주장 건수이며 낮을수록 좋습니다. 실제10%를99%라고 쓰면 근거 미충족입니다. 인용 문단의 다른 혜택에 있는 숫자가 잘못 인정되거나 정확한 바꿔 말하기가 탈락할 수 있어 완전한 답변 정확도가 아닙니다. 이 값은 운영 차단 성공과 다릅니다.</p>'
    body+=guide+'<p>관측 최고와 차순위는 같은 질문 집단의 답변 조건 충족 건수로만 표시하며 동점은 모두 표시합니다. 선택된 운영 설정이라는 뜻은 아닙니다.</p>'
    paired=[]
    for p in profiles:
        for before,after in [('U','S_THEN_U'),('S_THEN_U','S_PLUS_U')]:
            wins=[];losses=[];ties=[]
            for q in queries.values():
                if not q['gold_cards']:continue
                a=ab[(q['query_id'],p,before)]['scores']['known_positive_complete'];b=ab[(q['query_id'],p,after)]['scores']['known_positive_complete']
                (wins if b>a else losses if b<a else ties).append(q['query_id'])
            paired.append({'profile':p,'before':before,'after':after,'wins':wins,'losses':losses,'ties':ties})
    body+='<h2>같은 질문에서 개선·하락·동률</h2>'+table(['프로필','비교','개선','하락','동률','개선 질문','하락 질문'],[
      [names[r['profile']],names[r['before']]+' → '+names[r['after']],len(r['wins']),len(r['losses']),len(r['ties']),','.join(r['wins']) or '없음',','.join(r['losses']) or '없음'] for r in paired])
    body+='<p class="guide">wins/losses/ties / 개선·하락·동률은 동일 양성15질문의 답변 조건 충족0/1을 짝지어 비교합니다. 예: 비구조화0→구조화선행1은 개선1건입니다. 개선이 많고 하락이 적을수록 좋지만 단일 응답·같은 자료·작은 표본 때문에 통계적 우월성이나 운영 채택을 입증하지 않습니다.</p>'
    diagnostic=[];parsers=C.read(C.STAGES[40]/'parser_results.json')['records']
    E=C.load('report_schema_diagnostics','32_search_experiment.py');C.initialize(40)
    facts=E.DB.load_facts(C.F.OLD[30])
    for q,r in zip(dataset['queries'],parsers,strict=True):
        import copy
        reference=copy.deepcopy(q['plan'])
        corrected=q['query_id'] in ['N07','N08','N09','N11']
        if corrected:
            for group in reference['groups']:
                for requirement in group:
                    requirement.update(min_percent=None,max_spend_krw=None,min_cap_krw=None,cap_period='any')
                    if q['query_id']=='N11':requirement['action']='any'
        plan=r['plan'];oracle=E.DB.select_cards(facts,reference);selection=E.DB.select_cards(facts,plan)
        diagnostic.append({'query_id':q['query_id'],'status':r['status'],'route_match':int(plan['route']==q['plan']['route']),
          'condition_exact_match':int(E.normalized_plan(plan)['groups']==E.normalized_plan(reference)['groups']),
          'card_scope_match':int(set(plan['card_keys'])==set(q['plan']['card_keys'])),
          'gold_count':len(q['gold_cards']),'gold_selector_retained':len(set(oracle['eligible_cards'])&set(q['gold_cards'])),
          'llm_selector_retained':len(set(selection['eligible_cards'])&set(q['gold_cards'])),
          'fallback':not selection['eligible_cards'] or bool(plan['unresolved']) or plan['route']=='unstructured',
          'post_hoc_reference_correction':corrected,'parser_reference':reference})
    body+='<h2>실제 새 질문의 조건 추출과 후보 손실</h2>'+table(['질문','추출 성공','경로 일치','조건 일치','명시 카드 일치','원문 양성','정답조건 후보 보존','LLM조건 후보 보존','복귀'],[
        [r['query_id'],r['status'],r['route_match'],r['condition_exact_match'],r['card_scope_match'],r['gold_count'],r['gold_selector_retained'],r['llm_selector_retained'],r['fallback']] for r in diagnostic])
    body+='<p class="guide">parser success / 추출 성공은 출력 형식과 호출 성공입니다. route / 경로·condition / 조건·card scope / 명시 카드 일치는 표의 기준 계획과 같으면1입니다. 원문 양성 카드가 정답조건 selector에서도 빠지면 구조화 자료·selector 손실이며 LLM조건에서 추가로 빠지면 추출 영향입니다. fallback / 복귀는 감지된 실패·미해결·후보0 등의 전환입니다. 높은 보존·일치가 좋지만 같은 뜻의 다른 계획은 exact match / 완전 일치에서 탈락할 수 있습니다. 후보 보존만으로 최종 답변 정답을 판정하지 않습니다.</p>'
    body+='<p>사후 진단 보정: N07·N08·N09·N11의 사전 평가 plan에는 답변에서 확인할 실제 혜택률·실적·한도가 들어 있었지만 질문은 그 값을 요구 조건으로 명시하지 않았습니다. 따라서 이 추출 진단에서만 해당 수치 필터를 비우고 N11의 혜택 종류도 제한하지 않았습니다. 원래 동결 질문·정답·답변 채점과 검색·API 실행은 바꾸지 않았습니다. 이 표는 사후 기준 보정을 포함한 진단이며 사전 등록된 추출 정확도로 해석하면 안 됩니다. 상세 조회의 비구조화 경로도 타당할 수 있어 경로·조건 완전 일치만으로 의미 오류를 단정하지 않습니다.</p>'
    detail=[]
    for q in queries.values():
        for p in profiles:
            detail.append([q['query_id'],names[p]]+[ab[(q['query_id'],p,m)]['scores']['known_positive_complete'] if q['gold_cards'] else '양성 평가 제외' for m in modes])
    body+='<h2>질문별 전체 답변 조건 충족</h2>'+table(['질문','프로필','비구조화','구조화선행','융합'],detail)+guide
    unknown=[]
    for q in queries.values():
        if q['gold_cards']:continue
        for p in profiles:
            a=ab[(q['query_id'],p,'S_THEN_U')]
            unknown.append([q['query_id'],names[p],a['status'],a.get('answer',{}).get('answer_status','실패'),a.get('answer',{}).get('answer_text',a.get('error',''))])
    body+='<h2>조언·모호한 질문·원문 양성 없음</h2>'+table(['질문','프로필','실행','답변 상태','답변 원문'],unknown)
    body+='<p class="guide">answered / 답변과 insufficient evidence / 근거 부족은 모델의 출력 상태이며 실제 정답 판정이 아닙니다. N16은 일반 조언,N17은 소비 예산·최적화 요구,N18은 호텔567% 조건입니다. 원문 양성 없음은 실제 전체 상품의 혜택 없음이 아니므로 부정 정확도 분모로 쓰지 않습니다. 관련 없는 혜택 추천·임의 실적 변환·과도한 확정을 원문에서 확인합니다.</p>'
    costs=ledger['summary'];physical=ledger['records'];kinds=Counter('embedding' if Path(r['path']).name.startswith('embedding_batch_') else 'llm' for r in physical)
    body+='<h2>사용량·비용·시간</h2>'+table(['자원','측정값'],[
      ['신규 물리 LLM 요청',kinds['llm']],['신규 질문 임베딩 배치',kinds['embedding']],['논리 검색/답변 행','108/108'],
      ['사용량 기반 환산 USD',costs['known_cost_usd']],['요금 미확인 요청',costs['unknown_cost_records']],['실제 청구액','미측정'],
      ['입력/출력/캐시 토큰',__import__('json').dumps(costs['tokens'],ensure_ascii=False)],
      ['저장된 신규 고유 BGE 계산 쌍',len(list((C.STAGES[40]/'bge_cache').glob('*.json')))],
      ['마지막 재개 구간에서 새로 계산한 BGE 쌍',summary['new_bge_pairs']],
      ['마지막 재개 runner 경과 초',f"{summary['runner_wall_seconds']:.3f}"],['전력·처리량','미측정'],['반복 실행·표준편차','반복0·미측정']])
    body+='<p class="guide">physical request / 물리 요청은 저장된 고유 응답 요청이고 논리108행·캐시 공유와 다릅니다. 비용은 일반 입력·캐시 읽기·캐시 쓰기·출력 사용량에 검증된2026-10-11 공식 USD 단가를 적용하며 추론 토큰은 출력에 포함해 중복 계산하지 않습니다. 임베딩은 기존 문서 벡터를 재사용하고 새 질문만 생성했습니다. 실패의 기록된 사용량도 포함하며 누락·세금·환율·할인·기존OCR·Codex대화·CPU전력은 포함하지 않습니다. 실제 청구액과 전체 운영 비용은 미측정입니다. 저장된 BGE 쌍은 실행을 재개하기 전과 후의 고유 질문·문서 점수 저장본이며 메모리 진단의 별도 재계산과 중단 시 미저장 계산은 제외합니다. 시간은 마지막 재개 구간이며 첫 실행·진단·대기 시간을 포함한 누적 실행 시간과 다릅니다. 비용·시간은 낮을수록 유리하지만 품질 통과를 대신하지 않습니다. <a href="https://developers.openai.com/api/docs/models/gpt-6-luna">OpenAI 모델 요금</a> · <a href="https://developers.openai.com/api/docs/models/text-embedding-3-small">임베딩 요금</a>.</p>'
    if (C.STAGES[40]/'authorized_document_change_binding.json').exists():
        from decimal import Decimal
        receipt=C.read(C.STAGES[40]/'memory_recovery.json')
        original=C.read(C.STAGES[40]/'memory_recovery_contract.json')
        initial={path for path in original['before_manifest'] if '/api_cache/' in path and path.endswith('.json')}
        segments={key:{'requests':0,'cost_usd':Decimal(0)} for key in ['initial','earlier_resume','latest_resume']}
        for row in physical:
            record=C.read(C.ROOT/row['path'])
            phase='initial' if row['path'] in initial else 'earlier_resume' if record['started_at']<receipt['started_at'] else 'latest_resume'
            segments[phase]['requests']+=1
            segments[phase]['cost_usd']+=Decimal(row['cost_usd'])
        assert sum(r['requests'] for r in segments.values())==len(physical)
        assert sum(r['cost_usd'] for r in segments.values())==Decimal(costs['known_cost_usd'])
        assert segments['latest_resume']['requests']==receipt['physical_requests_after_resume']-receipt['physical_requests_before_resume']
        measured={key:{**value,'cost_usd':str(value['cost_usd'])} for key,value in segments.items()}
        C.write(C.STAGES[40]/'resume_cost_segments.json',{'segments':measured,'latest_resume_wall_seconds':receipt['resume_wall_seconds'],
            'earlier_elapsed_seconds':None,'billed_cost_usd':None,'scope':'Stage40 only; historical frozen rates and usage, not invoice.'})
        phase_names={'initial':'처음 완료한 22조건까지','earlier_resume':'이전 재개로 6조건 추가','latest_resume':'이번 재개로 80조건 추가'}
        body+='<h2>실행 구간별 비용</h2>'+table(['구간','물리 API 요청','사용량 환산 USD','구간 경과 초'],
          [[phase_names[key],value['requests'],str(value['cost_usd']),f"{receipt['resume_wall_seconds']:.3f}" if key=='latest_resume' else '미측정'] for key,value in segments.items()])
        body+='<p class="guide">구간은 첫 복구 계약의 요청 목록과 마지막 재개 시작 시각으로 구분했습니다. 요청 수는 실제 API 기록 수이며 캐시를 공유한 논리 조건 수와 다릅니다. 동일 질문·근거의 저장 응답을 재사용하면 새 요청·새 비용이 없습니다. USD는 기록된 토큰 사용량의 환산액으로 실제 청구액은 미측정입니다. 같은 품질이면 요청·비용·시간이 낮을수록 유리하지만 품질 실패를 상쇄하지 않습니다. 이전 구간의 정확한 경과 시간은 이번 기록으로 복원하지 않았습니다. <a href="../../notebooks/data/40_new_query_comparison/resume_cost_segments.json">구간별 계산 자료</a></p>'
    if (C.STAGES[40]/'memory_recovery.json').exists():
        recovery=C.read(C.STAGES[40]/'memory_recovery.json');binding=C.read(C.STAGES[40]/'memory_recovery_contract.json')
        parity=C.read(C.STAGES[40]/'memory_parity.json');telemetry=C.read(C.STAGES[40]/'memory_telemetry.json')
        # The historical time(1) log is not bound to the latest continuation.
        # This continuation did not measure user/system CPU seconds.
        cpu_user=cpu_system=None
        body+='<h2>메모리 부족 중단과 재개</h2><p>첫 실행은 커널의 global OOM / 시스템 메모리 부족으로 종료 코드137을 받았습니다. API·입력 토큰 한도 실패가 아닙니다. 중단 시 정상 답변22행과 정상 API 응답41개를 보존했고 해당 응답은 재호출하지 않았습니다. 원래 재정렬 함수에 온전한 문서를 한 개씩 전달하는 실험용 어댑터로 재개했습니다. 원문·청킹·최대5개 묶음·4096/8192 토큰 한도·모델 가중치·CPU float32·LLM 요청은 유지했습니다. 처음 완료한22행은 원래 배치2, 나머지86행은 호출당 문서1개입니다. 저장본의 해시와 두 실행 구간을 별도 계약에 기록했습니다.</p>'
        body+=table(['검사·자원','결과'],[
          ['원래 배치 / 재개 호출당 문서 수','2 / 1'],['보존한 기존 행 / 재개 대상 행',f"{binding['completed_rows']} / {len(binding['pending_keys'])}"],
          ['점수 일치 표본 수 / 허용 차이',f"{len(parity['records'])} / {parity['tolerance']}"],
          ['표본 최고 점수 차이',max(r['absolute_difference'] for r in parity['records'])],
          ['점수 일치 검사 최고 문서 토큰',max(r['document_tokens'] for r in parity['records'])],
          ['재개 중 최고 문서 / 질문·문서 토큰',f"{telemetry['max_document_tokens']} / {telemetry['max_pair_tokens']}"],
          ['재개 모델 프로세스 최고 RSS KiB',telemetry['peak_rss_kib']],
          ['재개 중 최소 시스템 가용 메모리 KiB',telemetry['minimum_host_available_kib']],
          ['재개 경과 초',f"{recovery['resume_wall_seconds']:.3f}"],['재개 상태',recovery['status']],
          ['마지막 재개 사용자 / 시스템 CPU 초',f"{cpu_user[1]} / {cpu_system[1]}" if cpu_user and cpu_system else '미측정'],
          ['전체 누적 CPU시간·중단 전 최고 메모리','미측정']])
        body+='<p class="guide">parity / 점수 일치 검사는 짧은3쌍과 긴2쌍의 원래 저장 점수와 문서1개 호출의 절대 차이를 비교하며0.0001 이하가 통과입니다. 긴 표본은 새 질문의 완료 문서만으로3000토큰 이상이 확보되지 않아 이전34번의 Q05·Q28 저장 검색에도 접근해 선정했고 개별 점수 파일 경로·해시를 기록했습니다. 기존 자료는 읽기만 했습니다. 문서는 자르지 않고 질문·문서 토큰 수도 기록했습니다. 이는 전체 순위 동일성이나 이후 메모리 부족 방지를 보장하지 않습니다. RSS / 실제 상주 메모리의 최고값은 재개 모델 프로세스가 측정한 KiB이며 시스템 전체 사용량과 다릅니다. 최소 가용 메모리는 모델 호출 전후 관측값이어서 호출 중 순간 최저값을 놓칠 수 있습니다. 재정렬·신규 API 호출 전의 필요 여유는 관측 최고RSS에서 현재RSS를 뺀 예상 증가량에512MiB를 더하고 최소1GiB를 요구합니다. 이 휴리스틱은 다른 프로세스의 갑작스러운 할당으로 생기는 메모리 부족을 보장해 막지는 못합니다. API의 미완료 저장 기록이 있으면 자동 재호출을 차단합니다. 마지막 재개의 사용자·시스템 CPU 초는 미측정입니다. 이전 실행의 time 로그를 이번 실행값으로 재사용하지 않았으며 전체 중단·진단 이력의 CPU 합계도 미측정입니다. 별도 점수 검사5쌍은 반복 답변 생성 실험5가 아니며 API0회입니다. 재정렬 배치 차이가 섞였으므로 본 자료로 정확한 지연 비교나 수치상 완전한 재현성을 주장하지 않습니다.</p>'
        if (C.STAGES[40]/'authorized_document_change_binding.json').exists():
            approval=C.read(C.STAGES[40]/'authorized_document_change_binding.json')
            body+='<h2>참고 문서 변경과 재현 방법</h2><p>평가지표 안내서는 검색·청킹·채점 입력이 아닌 별도 참고 문서입니다. 사용자가 요청한 문서 정리 커밋 '+html.escape(approval['commit'][:7])+' 때문에 광범위한 원본 보존 검사가 재개를 차단했습니다. 기존 목록은 수정하지 않고 해당 커밋의 문서 내용·크기·수정 시각과 목록 자체의 SHA를 별도 기록해 승인된 한 건만 구분했습니다. 나머지 운영 코드·OCR 자료·모델·기존 결과는 기존 보존 기준을 계속 적용했습니다.</p>'
            body+='<pre>python -B notebooks/40_authorized_document_change.py --check\npython -B notebooks/40_authorized_document_change.py</pre><p>이 보조 runner는 캐시와 메모리 점검을 유지해 중단 지점부터 재개합니다. 실험용 Python 의존성이 준비된 환경에서 실행하며 API 호출에는 별도 사용 승인이 필요합니다. 이번 실행은 승인된 conda 환경에서 수행했습니다. <a href="../../notebooks/data/40_new_query_comparison/authorized_document_change_binding.json">문서 변경 구분 기록</a> · <a href="../../notebooks/40_authorized_document_change.py">재개 보조 코드</a></p>'
    overall=[r for r in metrics if r['subgroup']=='all'];ordered=sorted(overall,key=lambda r:(-r['known_positive_complete'],-r['hit'],-r['mrr']))
    top=ordered[0]['known_positive_complete'];joint=[r for r in ordered if r['known_positive_complete']==top]
    best_labels='; '.join(names[r['profile']]+' · '+names[r['mode']] for r in joint)
    body+='<h2>최종 정리</h2><p>이 단일 실행의 답변 조건 충족 관측 최고는 '+best_labels+f" ({top}/15)입니다. 최고 점수가 같은 조건은 모두 함께 표시하고 차순위와 전체 조건은 위 표에 공개했습니다. 이전 운영 검증 후보와 비교해 운영 채택·승격을 하지 않습니다. 고정 평가와 부분 정답·미완성 계층·프로필 정책 차이·문자열 검사·단일 응답 변동이 남아 청킹의 확정 우열이나 일반화를 주장하지 않습니다. 비용·실패·하위집단 하락을 함께 판단해야 합니다. 반복 실행5는 제외했고 다음 검증은 새로운 독립 자료와 의미 관계·제목 계층 보완이 필요한 별도 단계입니다.</p>"
    # Remaining quality inspection is a separately documented review, never score tuning.
    quality=C.STAGES[40]/'quality_inspection.json'
    if quality.exists():
        notes=C.read(quality)
        body+='<h2>답변 원문 확인</h2>'+table(['질문/조건','확인한 문제','평가 한계'],[[r['case'],r['finding'],r['limitation']] for r in notes['records']])
        body+='<p class="guide">이 표는 실행 후 원문 확인이며 사전 점수를 바꾸거나 설정을 고르는 추가 실험이 아닙니다. 답변 조건 충족 지표의 수치·행동 관계·부정 표현 한계를 실제 원문과 비교합니다. 표본 확인은 전체 의미 정확도의 증거가 아닙니다.</p>'
    output={'metrics':metrics,'paired':paired,'parser_diagnostics':diagnostic,'cost':costs,'raw_best_by_primary':[{'profile':r['profile'],'mode':r['mode']} for r in joint],
       'production_adopted':False,'independent_holdout':False,'repeated_runs':0}
    C.write(C.STAGES[40]/'evaluation_summary.json',output)
    save(n,'new_query_comparison','새 질문의 검색·청킹 단일 비교',body,[C.STAGES[n]/'evaluation_summary.json',C.STAGES[n]/'retrieval_results.json',
        C.STAGES[n]/'answer_results.json',C.STAGES[n]/'cost_accounting.json',C.STAGES[39]/'dataset.json',C.STAGES[n]/'protocol.json'])
    notebook(n,'new_query_comparison','새 질문의 검색·청킹 단일 비교',C.STAGES[n]/'run_summary.json')
