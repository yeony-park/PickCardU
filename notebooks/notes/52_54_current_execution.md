# 52–54 현재 실행 재개 메모

2026-10-22, 코드 저장 커밋 `140486e`, `8eacdd6`. 운영 코드·OCR 검증기·원본 DB/PDF/색인은 변경하지 않았다. 사용자 API 승인은 지속되며 누적8달러 상한이다. 원51 HTTP520 한 건만 상한0.003127달러로 보수 차감하는 예외를 승인받았고 재시도(stage510)는 성공했다. 원51의 실패 기록은 그대로다. 새 비용 미확인 요청은 여전히 자동 중단한다.

## 실행 중

- 함수 도구 실행 세션 `75445`: `/tmp/continue_structured52.py`. 현재 corrected52 검색280/280, 답변 생성 진행 중, 질문 원문 판정168개 이상이다. 진행 중 수치는 디렉터리를 다시 세어 확인한다. 최종 답변으로 턴을 끝내거나 프로세스를 놓고 종료하지 말고 결과를 회수한다.
- 제어기는240초 단위로 `/tmp/structured_notebook_executor.py`를 재실행한다. 새 진행이 저장된 뒤만 정상 일시중지하도록 임시 실행기를 개선했다. 실제 실험 코드는 동결되어 바꾸지 않는다. 최신 로그는 `/tmp/structured52_segment_*.log`, 각 노트북/데이터의 execution_segments에 기록된다. 실험 코드/HTML 생성용 새 runner를 repo에 만들지 않았다.
- 최초52 `notebooks/52_four_retrieval_modes.ipynb`/data52_four_retrieval_modes는239검색·0답변에서중단해원본보존했다. 예외문장에대상이등장하면전체행을거절하는오류가있어별도 `52_corrected_four_retrieval_modes.ipynb`와동명data디렉터리로재실행중이다. 원데이터의intentional_stop.json에원래raw파일보존해시가있다.
- 최초52 임베딩비용0.000005080달러는legacy stage520으로정확히한번,수정52상한1.999994920달러로합계2달러를지킨다. 동일40벡터는oldmatrixSHA로검증재사용,신규임베딩0. U본문만원계약검증후새manifest에다시묶는다. S_ONLY/SEQ/PAR는수정조회로새로구성했다.
- 첫107답변시점누적확인비용은0.369088830달러,미확인원520 1건별도다. 진행중요청(status started)은종료된미확인실패와구분한다. 최종비용은실행이끝난후ledger에서다시집계한다.

## 구현·검토 완료와 미실행

- 50/51 완료. 51첫추출만수정SQL로재조회했으며matched19→22, no_results15, clarify2, parser_failure1이다. `rerouted_repeat1_plans.json`, `routing_diagnostics.json` 참고. 추출이나DB표현의다른한계까지고친것은아니다.
- `52_query_predicate_correction.py`는원파일수정없는프로세스내교체/예산분리/원본보존guard다. 187쌍/75facts의전수차분검토와sourcePDFSHA/page검증후수락했다. 원50/51/52코드는고정이다.
- 53노트북은아직유료미실행이다. 수정52의SEQ와재조회r1만쓴다. A카드범위+U원문 vs B같은U에일치DB사실만답변입력추가.12질문×2프로필×A/B×2회=96조건. 입력같은A/B는같은회차만공유한다. 새중요오류는기존A오류가있더라도직접new_important_error_vs_A로차단한다.12개검사통과·reviewerBlocking/Major0이다.
- 54코드는구현·검토했으나질문32/선정설정/manifest/API는아직없다. 53선정후새32질문을작성한다.4방식/2프로필/3회=672unique, S_ONLY96은공통이다. 신규96추출후원문차분검토가필수이며entry없으면검색/임베딩/답변을막는다. fact/card/PDF소유권검증, 672개source/raw답변결속, 하위그룹/반복/새오류/조건손실/범위guard를마쳤다.14개검사통과·reviewerBlocking/Major0이다. 평가지표도이번추가절만커밋했고기존사용자문서수정은unstaged로보존했다.
- 전체회귀76개,20.13초통과. 실제명령: python -m pytest -q notebooks/scripts/50_test_query_contract.py notebooks/scripts/51_test_experiment_runtime.py notebooks/scripts/52_test_retrieval_modes.py notebooks/scripts/52_test_query_predicate_correction.py notebooks/scripts/53_test_selection_contract.py notebooks/scripts/54_test_retrieval_cache.py notebooks/scripts/54_test_confirmation_contract.py

## 원문 평가 현재와 다음

- `source_judgments.json`은부분수동판정이다. 현재D33–D40(56), H01–H06(42)을모두읽어98개를저장했다. 진행수치위참고. 다음은H07부터다. 답변raw를읽고각행의augmented답변SHA/PDF출처/이유와PASS/PARTIAL/FAIL/UNCERTAIN을저장한다. 기존원문관찰은source_review/anchor_source_judgments.json, verified_additional_cards.json에있다. 새로운유효대안도해당카드원문으로인정하며기준카드의수치를다른카드에강제하지않는다.
- 수동입력용임시스크립트 `/tmp/record_initial_source52.py`, record_D36_source52.py, record_D37_D38_source52.py, record_D39_D40_source52.py, record_H01_H04_source52.py, record_H05_H06_source52.py가있다. 재실행하면중복추가위험이있으니내용을참고하되이미판정한행을다시append하지말것. P.F.write_json을쓰려면C.install(X)로새52경로를등록한다. raw답변에query_id/profile/mode/repeat를합친객체가향후execution_results행과같고그SHA를판정에묶는다.
- **평가 해석 정정(98개 판정 후,112개 답변 열람 후)**: 원 동결 계약은 실제 질문 해결과 중요한 조건 보존을 요구하며 required_meanings는 source anchor다. optional 주석의 미열거를 주질문 실패와 혼동했던 원98판정은 source_judgments_annotation_interpretation_v1.json에 byte동일 보존했다. proposal-reviewer 독립 검토 후 evaluation_interpretation_supplement.json/question_rubrics.json에 해석을 명시했다. 별도 question_source_judgments.json에 question_verdict와 annotation_coverage를 나누며 원98의 전후 민감도11건을 evaluation_interpretation_sensitivity.json에 기록했다. actual 중요오류는 그대로다. 개발 중 사후 해석 정정이므로 확인시험/독립인간/블라인드 평가라고 주장하지 않는다. 아직 주석별 포함 여부를 별도 재검토하지 않은 항목은 uncertain으로 남기고 비율의 확인범위를 공개한다.
- 53/54는 유료 답변 전 must_answer/material_if_recommended_or_claimed/context_anchor를 동결한다. 공유 오프라인 helper 53_question_evaluation.py는 PASS시 필수항목의 누락/불확실/중요오류/적용누락/범위오류를 거절하고 optional 주석만의 누락은 주평가를 바꾸지 않는다. 두 노트북 manifest는 helper와 rubric·해석supplement를 묶는다. 신규9검사 포함 전체85개20.75초 통과, 추가reviewer 검토 진행 중이다. 52동결 실행코드는 수정하지 않는다.
- 원문확인중요사례: D35 parentPAR는SIMPLE의국내전용BC를무시하고발급사이름케이뱅크라BC가아니라고잘못제외했다. D37 parentU는NHEarth생활공유1만원을커피월한도1만원카드로추천했으나커피2000×2=4000상한이다. H05 benefitSEQ본문은매월1일맞지만claim조건에1월1일로써기간오류가있다. API정상과의미정확성은다르다.
- **IBK KPass 앱 예외**: PDF2쪽은철도/고속버스공식앱예매허용,택시앱결제제외다. 구조화fact_b1dcc4598090e0ee2af4f17d는세대상을합치고exceptions앱제외를평탄화했다. U텍스트도같은들여쓰기목록이며여러답변이모든앱제외로확대했다. source_review/IBK_KPass_page2.png 및 IBK_KPass_app_exclusion_diagnosis.json참고. D39 S_ONLY/benefitSEQ와D40 benefitU는제외될수있다는모호한설명으로PARTIAL, 나머지D39/D40의명시적전체앱제외는FAIL중요오류다. D40S_ONLY는조회0근거없음으로FAIL. 데이터/OCR는수정하지않는다.
- 후보카탈로그의certainty=uncertain_for_full_question은확정무효로보아BGE순위실패를만들면안된다. 현재D38 parentPAR의GoodDay/HanaShinsegae는금액범위에서실제월3회가능하므로유효다. LLM이횟수명시없다고확답못하는문제는순위실패가아니다. D40두PAR모두IBKKpass가top3에있다. 현재확인된조건부LLM진입사례는없지만최종80PAR쌍을정리해야한다.
- NHEarth와YouthSweetSleep의철도/고속버스/택시유효판정을H21/D39/D40에의미동등하게추가할수있다. 아직일부qid에는미추가다. SweetSleepPDF1쪽20%여객운송·전월40/80만·월1만/1.5만·일5천·모바일KBPay/알파원조건을확인했다. Green v2는철도/고속버스5%에코머니적립은있지만할인이아니고택시동시혜택도아니다.

## 남은 순서

52 API전체회수→원문280행판정/80PAR순위검토→조건부LLM진입gate(필요한경우에만80쌍전체추가비교)→수정52평가/직접HTML/53허용→53 96조건과원문선정/직접HTML→54설정동결후새32질문/PDF정답·3회추출/새차분전수검토/672답변·평가/직접HTML→보존/코드검토/curatedcommit. 운영반영은하지않는다. 새보고서는같은번호html이며generator를만들지않는다. 첫토큰/UI표시미측정,단계합계는온라인종단실측아니다. 검색/답변캐시첫시간·재생시간을섞지말것. 기존사용자변경(옛스크립트이동,노트북30~45,pyprojects,DB_SPEC,metrics기존45행등)은절대stage하지말것.
