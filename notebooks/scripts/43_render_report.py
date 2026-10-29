"""Render the source-qualified offline diagnosis, without model/API calls."""
import importlib.util
from pathlib import Path
import sys
import html
from collections import Counter

spec=importlib.util.spec_from_file_location('report43_io',Path(__file__).with_name('43_offline_validation.py'))
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)
V=C.R.load('report43_summary','43_answer_review_summary.py')
NAMES={'U':'비구조화 단독','S_THEN_U':'구조화 후 비구조화','S_PLUS_U':'두 검색 결과 융합'}
SCOPE_AUDIT_SHA256='ac8ccdffcb0318c897dff698831c7162d62d5a8cb24619a3af838cd0f572e5dd'
GUIDE='''<p class="guide">Core request satisfaction(핵심요구 충족)은 원질문의 대상·행동·수치·AND/OR를 소유 근거로 입증한 PASS 수입니다. 양성44질문(유형별16/16/12)이 분모이고 높을수록 좋습니다. 예:GS25와넷플릭스를각각10% 이상 할인하는 한카드의 근거를 설명하면 통과합니다. 목록밖의 유효카드도 인정하며 모든 부수문장의 정확도나 미래질문의 성공률을 뜻하지 않습니다. PARTIAL(부분충족)은 관련혜택이 있으나 핵심대상/조건 일부 미확정,FAIL(미충족)은 요구혜택을 못찾음,UNCERTAIN(불확실)은 자료관계가 미확정입니다. 모두 PASS에 합산하지 않고 낮을수록 좋습니다. 예:네이버페이 조건부할인을 정확한네이버쇼핑할인으로 확정 못하면 부분충족입니다. Wrong core recommendation(잘못된 핵심혜택 추천)은 같은 양성질문 중 다른추천의 대상·행동·율이 틀린 답변 수입니다. 낮을수록 좋고0이 방어통과 기준입니다. 예:CU7%할인을7%적립이라고 추천하면 오류입니다. 한카드가 요구를 충족해도 다른추천 오류는 별도기록하고,오류증가시 우세판정을 차단합니다. OCR 자체오류와 모든 부수문장 오류의 검출보장은 없습니다. 이전 자동 KnownPositiveComplete(답변충족)는42의 동결된 문자열·부분정답 검사 통과 수로,같은질문 분모이며 수정하지 않았습니다. 원문판정은 Codex/독립검토자의 사후판단이고 완전한 사람 블라인드 평가가 아닙니다.</p>'''

def table(headers,rows,classes=None):
    body=''
    for i,row in enumerate(rows):
        body+='<tr class="'+(classes[i] if classes else '')+'">'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>'
    return '<table><thead><tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+body+'</tbody></table>'

def render():
    summary=V.summarize()
    routing=C.R.read(C.PATH/'routing_results.json')
    preservation=C.R.read(C.PATH/'preservation.json')
    body='<h1>43 저장 답변 원문 재검토와 조건 라우팅 오프라인 검사</h1>'
    body+='<p>왜 실행했나:42의 부분정답 목록과 표현오탐 때문에 유효한 답변도 실패로 잡혔습니다. 저장답변의 핵심요구를 원문과 대조하고,조건추출 실패와 의미오류가 검색범위에 주는 영향을 확인했습니다.</p>'
    body+='<p>질문:benefit 순차/융합이 단독보다 실제요청을 더 충족하는가? 두 방식의 차이가 남는가? 추출실패·후보0·잘못된 조건에서 어떤 복귀가 일어나는가?</p>'
    body+='<p>변경:API 재호출 없이 저장144답변을97고유패킷으로 묶고,검토전에 동결한 원문판정 기준을 적용했습니다. 고정:42원질문/응답/근거·모델·색인·운영청킹·기존 자동점수. 청킹비교와 운영API 연결은 이번 범위에 없습니다.</p>'
    body+='<p class="guide">주의:이미 실행·관찰한42자료의 사후진단입니다. 새질문·독립 비공개평가·재생성반복이 아닙니다. 방식명과 자동점수를 가렸지만 검토자가 이전맥락을 본 상태라 완전한 블라인드가 아닙니다. OCR통과 가정과 제목계층 미완성을 유지합니다. 핵심요구 판정은 모든 부수문장의 사실정확성을 뜻하지 않습니다. 저장원문페이지→소유검색근거→구조화fact→표현 순서로 확인했습니다.</p>'
    for group,title in [('all','전체 양성44질문'),('numeric','수치·조건16질문'),('proper_noun','카드상세16질문'),('semantic','의미·혜택12질문')]:
        rows=[r for r in summary['metrics'] if r['group']==group]
        ranks=sorted({r['PASS'] for r in rows},reverse=True)
        body+='<h2>'+title+'</h2><p>초록은 사후PASS 관측최고,노랑은 차순위이며 운영채택을 뜻하지 않습니다.</p>'+GUIDE
        body+=table(['방식','핵심요구통과','부분충족','미충족','불확실','잘못된핵심추천','이전자동통과'],
            [[NAMES[r['mode']],f"{r['PASS']}/{r['n']}",r['PARTIAL'],r['FAIL'],r['UNCERTAIN'],r['wrong_core_recommendation'],f"{r['old_automatic_complete']}/{r['n']}"] for r in rows],
            ['best' if r['PASS']==ranks[0] else 'second' if len(ranks)>1 and r['PASS']==ranks[1] else '' for r in rows])
    body+='<h2>질문별 변화와 일반대조</h2><p class="guide">Paired wins/losses/ties(승/패/동률)는 같은양성44질문에서 후보의 PASS 여부가 기준보다 높음/낮음/같음입니다. PASS만1이고 다른판정은0입니다. 예:단독이배달할인만찾고 순차가적립을찾으면 순차승1입니다. 동일자료·사후판정이라 독립확증이나통계적유의성으로 해석하지 않습니다.</p><ul>'
    for pair in summary['paired']:
        body+=f"<li>{NAMES[pair['baseline']]} → {NAMES[pair['candidate']]}:승{len(pair['wins'])},패{len(pair['losses'])},동률{len(pair['ties'])};승질문 {', '.join(pair['wins']) or '없음'},패질문 {', '.join(pair['losses']) or '없음'}.</li>"
    body+='</ul><p>단독의 미충족은E02 CU적립/E38 배달적립,부분충족은E05 정확한네이버쇼핑할인입니다. 순차와융합은 이를소유근거로 충족했습니다. E14/E15 목록밖유효카드,E39 영화관표현,E38 PointPlan 대안도인정했습니다. E45~E48 일반조언·예산모호·상점미확인·499% 대조는방식별4/4,총12/12 기대행동통과입니다. 양성44와 합산하지 않습니다.</p>'
    body+='<h2>실제48질문의 선택·복귀재생</h2><p class="guide">Replay match(저장경로일치)는48실제parser응답을 기존QueryPlan/카탈로그/선택기에 재입력해 두프로필의 저장selection/fallback과 일치한질문 수입니다. 높을수록좋고48/48이 통과입니다. 예:CU7%후보목록과 복귀여부가 같으면일치입니다. 의미적으로 정확한조건을 뽑았다는 뜻은 아닙니다. Fallback(원래비구조화검색복귀)은비구조화경로·미해결조건·후보0이면적용되며,횟수가높고낮음만으로좋고나쁨을판정하지않습니다. 정상추출된 명시카드범위는 복귀에도 유지합니다.</p>'
    actual=routing['actual_records']
    reasons=Counter('unstructured' if r['normalized_plan']['route']=='unstructured' else 'unresolved' if r['normalized_plan']['unresolved'] else 'no_candidates' for r in actual if r['fallback'])
    body+=table(['항목','결과','해석'],[
        ['실제계획재생',f"{routing['actual_replay_matches']}/48",'두프로필의 저장96행과 일치'],
        ['실제추출형식',f"{sum(r['parse_gate']=='ok' for r in actual)}/48",'저장계획의형식/카탈로그 정상.의미정확도 별도'],
        ['복귀',f"{sum(r['fallback'] for r in actual)}/48",f"비구조화경로{reasons['unstructured']} / 미해결{reasons['unresolved']} / 후보0 {reasons['no_candidates']}"]])
    body+='<p>18질문이복귀했고,명시카드범위 안의 상세질문 비구조화검색도 포함됩니다.18건을추출실패로 해석하지않습니다. LLM재호출없이 예외/스키마/카탈로그 단계는 아래반례에서검사했습니다. 진입점은42정책의오프라인재현이며 운영API 전체실행은 아닙니다.</p>'
    scope=C.R.load('report43_scope_binding','43_scope_audit.py').verify_scope_artifact(C.PATH/'scope_audit.json',SCOPE_AUDIT_SHA256)
    if not scope['passed'] or scope['checked_search_rows']!=192:
        raise RuntimeError('Actual component scope audit is incomplete')
    body+='<p class="guide">Actual scope match(실제 범위 일치)는48질문×두프로필×U/순차192검색행의 실제component 범위·후보청크 소유권·검색trace입력·최종카드/근거가 예상범위와 일치하는지입니다. 높을수록좋고192/192가 통과입니다. 예:명시NH올원더풀 요청의 후보와근거가 모두 그카드여야 합니다. 이실험은외부ScopedSearcher에서범위를적용하므로 내부SearchConfig trace의null은범위미적용을뜻하지않습니다. 운영API통합 검증이나조건추출의미정확도는아닙니다.</p>'
    body+=f'<p>192/192행이범위검사를통과했고 실제범위지정{scope["actual_scoped_rows"]}행의후보·근거가모두그범위안이었습니다. 그{scope["scoped_rows_with_null_inner_trace"]}행의내부추적값은null이어서,이실험의범위판정에는component.identity.scope와청크소유권을함께확인해야합니다. E17도NH올원더풀의범위가component에보존됐고최종카드/근거는그카드안에있었습니다.</p>'
    body+='<h2>통제된28조건과 의미오류위험</h2><p class="guide">Fixture match(고정기대값일치)는실행전동결한합성fact/계획28조건의 선택·unknown·범위·복귀가예상과같은지입니다. 높을수록좋고28/28이통과입니다. 예:월한도요구에 일한도카드를제외해야합니다. 위험한현재동작도 관측기대로동결했으므로 통과가안전성보장은아닙니다. Lost correct cards(정답후보소실)/Admitted incorrect cards(불충족후보유입)은합성oracle와주입선택의차집합으로 낮을수록좋습니다. 예:할인을적립으로잘못뽑아할인카드가제외되면소실입니다. 실제오류빈도가아니며 A~I는합성카드입니다.</p>'
    body+=table(['조건','형식','선택카드','복귀','기대일치','정답소실 / 불충족유입'],
        [[r['case'],'정상' if r['parse_gate']=='ok' else '실패',','.join(r['eligible_cards']) or '없음','예' if r['fallback'] else '아니오','통과' if r['matches_frozen_expectation'] else '실패',
          ('해당없음' if r['correct_cards_lost'] is None else ','.join(r['correct_cards_lost']) or '없음')+' / '+('해당없음' if r['incorrect_cards_admitted'] is None else ','.join(r['incorrect_cards_admitted']) or '없음')] for r in routing['controlled_records']])
    body+='<p>형식정상 의미오류6조건(대상·행동·수치·AND/OR·월/일·다른알려진카드)은모두후보가남아복귀하지않았습니다.4조건은올바른후보를전부잃었고,수치/AND-OR조건은부적합후보가추가됐습니다. 형식검사만으로의미오류를잡지못하며 답변검증이제외된카드를복원하지는못합니다.</p>'
    body+='<p>추출실패/알수없는카탈로그키는42예외정책에따라 계획을초기화해명시범위가자동복원되지않았습니다. 정상추출후후보0/unresolved는범위를유지했습니다. 빈내부AND그룹[[]]은현재스키마가허용하고선택기에서무조건참이돼전체카드를선택합니다. 수정없이운영연결전확인사항으로기록했습니다. 오프라인회귀42개통과는 이세위험군의해소를뜻하지않습니다.</p>'
    resource=routing['resources']
    storage=sum(p.stat().st_size for p in C.PATH.rglob('*') if p.is_file() and 'execution_cache' not in p.parts)
    body+='<h2>비용·자원·보존</h2><p>새provider API0·임베딩0·재정렬모델추론0·추가실험API사용량비용USD0입니다. Codex원문검토의모델사용료/작업시간과실제청구액은미측정이며 실험API비용과구분합니다. GPU미사용. 운영지연/실제LLM오류율/동시부하/프로필전환은미측정입니다.</p>'
    body+=f'<p>선택기재생프로세스 경과{resource["wall_seconds"]:.3f}초,CPU{resource["cpu_seconds"]:.3f}초,최고RSS{resource["peak_rss_kib"]:,}KiB입니다. 검토·감사까지합산한전체시간이아닙니다. 저장량스냅샷{storage:,}bytes(실행캐시제외). 원본/운영/이전실험파일{preservation["checked_files"]:,}개보존확인. 모델/색인구성변경0,운영코드수정0입니다.</p>'
    relative='../../notebooks/data/43_offline_validation/'
    names={'protocol.json':'입력계약','review_contract.json':'질문별판정기준','answer_review_summary.json':'144판정/승패','review_packets.json':'방식가림패킷','routing_results.json':'48/28경로','scope_audit.json':'192행실제검색범위','routing_fixtures.json':'합성기대값','review_receipt.json':'검토바인딩','preservation.json':'원본보존'}
    if (C.PATH/'completion_audit.json').is_file():
        audit=C.R.read(C.PATH/'completion_audit.json')
        if not audit['passed']:
            raise RuntimeError('Final completion audit failed')
        names['completion_audit.json']='최종감사'
    else:
        body+='<p>최종 완료감사를 준비 중입니다. 아래 원문/범위 검사 결과와 최종 완료 여부는 구분합니다.</p>'
    body+='<p>전체원자료: '+' · '.join(f'<a href="{relative}{name}">{label}</a>' for name,label in names.items())+'</p>'
    body+='<h2>최종 요약</h2><p>사후핵심요구판정에서benefit 순차/융합44/44,단독41/44로CU적립·정확한네이버쇼핑할인·배달적립의회수가개선됐습니다. 순차와융합은승0·패0·동률44여서우열을확정하지않습니다. 기존기준후보benefit 구조화후비구조화를유지하고,보강/parent-child를새로선택하지않았습니다. 운영반영없음.</p>'
    body+='<p>실제48경로·통제28기대값·192행검색범위·회귀42개는일치했지만,후보가남는의미오류·추출실패시명시범위복원·빈AND그룹은운영연결전에다뤄야할한계입니다. 추가API비용0,재생경과약15.4초·최고RSS약241MiB. 같은자료사후판정·조건부상세기준·검토자이전맥락·OCR가정·제목계층미완성·부수문장검증범위의한계가있습니다. 다음단계는운영코드변경이승인된뒤개발환경에서경계정책을정하고API경로·비용/지연·프로필색인전환을검증하는것입니다.</p>'
    report=C.R.ROOT/'docs/report/43_offline_validation_report.html'
    report.write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>43 노트북 오프라인 검사</title><style>body{max-width:1200px;margin:32px auto;padding:0 20px;font:16px/1.7 sans-serif;color:#182230}table{border-collapse:collapse;width:100%;margin:16px 0}th,td{border:1px solid #ced5dd;padding:8px;vertical-align:top}.guide{background:#f1f5f9;padding:14px}.best{background:#ecfdf5}.second{background:#fffbeb}h2{margin-top:32px}</style><body>'+body+'</body></html>')
    return summary

if __name__=='__main__':
    render()
