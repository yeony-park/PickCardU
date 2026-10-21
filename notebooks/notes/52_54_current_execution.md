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
- **평가 해석 정정(98개 판정 후,112개 답변 열람 후)**: 원 동결 계약은 실제 질문 해결과 중요한 조건 보존을 요구하며 required_meanings는 source anchor다. optional 주석의 미열거를 주질문 실패와 혼동했던 원98판정은 source_judgments_annotation_interpretation_v1.json에 byte동일 보존했다. proposal-reviewer 독립 검토 후 evaluation_interpretation_supplement.json/question_rubrics.json에 해석을 명시했다. 별도 question_source_judgments.json에 question_verdict와 annotation_coverage를 나누며 원98의 전후 민감도15건을 evaluation_interpretation_sensitivity.json에 기록했다. actual 중요오류는 그대로다. 개발 중 사후 해석 정정이므로 확인시험/독립인간/블라인드 평가라고 주장하지 않는다. 아직 주석별 포함 여부를 별도 재검토하지 않은 항목은 uncertain으로 남기고 비율의 확인범위를 공개한다.
- 53/54는 유료 답변 전 must_answer/material_if_recommended_or_claimed/context_anchor를 동결한다. 공유 오프라인 helper 53_question_evaluation.py는 PASS시 필수항목의 누락/불확실/중요오류/적용누락/범위오류를 거절하고 optional 주석만의 누락은 주평가를 바꾸지 않는다. 두 노트북 manifest는 helper와 rubric·해석supplement를 묶는다. 신규9검사 포함 전체85개20.75초 통과, 추가reviewer Blocking/Major0 확인을 마쳤다. 코드 보존 커밋0101441이다. 52동결 실행코드는 수정하지 않는다.
- 원문확인중요사례: D35 parentPAR는SIMPLE의국내전용BC를무시하고발급사이름케이뱅크라BC가아니라고잘못제외했다. D37 parentU는NHEarth생활공유1만원을커피월한도1만원카드로추천했으나커피2000×2=4000상한이다. H05 benefitSEQ본문은매월1일맞지만claim조건에1월1일로써기간오류가있다. API정상과의미정확성은다르다.
- **IBK KPass 앱 예외**: PDF2쪽은철도/고속버스공식앱예매허용,택시앱결제제외다. 구조화fact_b1dcc4598090e0ee2af4f17d는세대상을합치고exceptions앱제외를평탄화했다. U텍스트도같은들여쓰기목록이며여러답변이모든앱제외로확대했다. source_review/IBK_KPass_page2.png 및 IBK_KPass_app_exclusion_diagnosis.json참고. D39 S_ONLY/benefitSEQ와D40 benefitU는제외될수있다는모호한설명으로PARTIAL, 나머지D39/D40의명시적전체앱제외는FAIL중요오류다. D40S_ONLY는조회0근거없음으로FAIL. 데이터/OCR는수정하지않는다.
- 후보카탈로그의certainty=uncertain_for_full_question은확정무효로보아BGE순위실패를만들면안된다. 현재D38 parentPAR의GoodDay/HanaShinsegae는금액범위에서실제월3회가능하므로유효다. LLM이횟수명시없다고확답못하는문제는순위실패가아니다. D40두PAR모두IBKKpass가top3에있다. 현재확인된조건부LLM진입사례는없지만최종80PAR쌍을정리해야한다.
- NHEarth와YouthSweetSleep의철도/고속버스/택시유효판정을H21/D39/D40에의미동등하게추가할수있다. 아직일부qid에는미추가다. SweetSleepPDF1쪽20%여객운송·전월40/80만·월1만/1.5만·일5천·모바일KBPay/알파원조건을확인했다. Green v2는철도/고속버스5%에코머니적립은있지만할인이아니고택시동시혜택도아니다.

## 남은 순서

52 API전체회수→원문280행판정/80PAR순위검토→조건부LLM진입gate(필요한경우에만80쌍전체추가비교)→수정52평가/직접HTML/53허용→53 96조건과원문선정/직접HTML→54설정동결후새32질문/PDF정답·3회추출/새차분전수검토/672답변·평가/직접HTML→보존/코드검토/curatedcommit. 운영반영은하지않는다. 새보고서는같은번호html이며generator를만들지않는다. 첫토큰/UI표시미측정,단계합계는온라인종단실측아니다. 검색/답변캐시첫시간·재생시간을섞지말것. 기존사용자변경(옛스크립트이동,노트북30~45,pyprojects,DB_SPEC,metrics기존45행등)은절대stage하지말것.

## 추가 재개 정보

제어기75445는 계속 실행 중이며19번째 segment부터 임시 executor의 최소 segment를900초로 늘렸다(/tmp/structured_minimum_segment_seconds). 논리 결과/API마다 기존저장·검증은유지하고캐시전체재생반복만줄인다. 52실행코드·manifest는수정하지않았다. 현재답변190개이상, 새주판정189개다. 정확한수는다시센다. 원문평가보충계약에조건부앱주의와전체앱제외확정을구분하는추가독립검토를기록했다. 이때주판정182개후였고평균/승자집계전이었다. 53/54아직유료미실행이므로수정된보충계약을manifest에동결할수있다. 원98대비정정15건이며원파일/스냅샷은그대로다. 80개PAR초안은source_review/ranking_source_review_draft.json(모든37근거존재질문은유효카드top3포함, H25/26/32순위평가비대상). 실행완료후execution_resultsSHA로source_review.json과conditional_trigger_review.json을묶어야한다.

H20답변은아직전체7개를읽지않았다. 현재읽은benefitU는claim/본문은전월실적이나추천문구에충전실적30만원이라고좁혀적어중요조건오류가능성이있다. parentU는IAnywhere정답은맞지만함께추천한zgm living의관리비합산여부를모른다고답해질문일부미해결이다. benefitSEQ는두카드실적/관리비합산제외를정확히답했다. H20전체완료후다시읽고판정할것. H19는benefit U/SEQ/PAR PASS, parent3방식은최소전월40만원누락PARTIAL, S_ONLY근거없음FAIL로189판정저장했다.

## 52 완료 체크포인트 (2026-10-22)

- 제어기75445는280답변을 완료하고 정상 종료했다. 280/280을 모두 읽고 question_source_judgments.json에 저장했다. 원98판정은 byte동일 보존했고 정정15개는 sensitivity에 전후를 기록했다. 추가D36 benefitU는 첫문장(횟수명시카드는그린)이 다른카드의횟수미명시도전달하므로주질문PASS로정정했다. 후보평균을보기전에정정했다. D33 benefitU/H18 parentU는할인요청에포인트/캐시백추천을더한사례라 material_omission=False/off_target_recommendation=True로구분했고PARTIAL은유지했다.
- H32 S_ONLY의근거없음은실제개인승인자료가없는요청에서숫자를만들지않은보류로PASS, 접근불가이유·추가정보안내부족은secondary missing으로기록했다. 보고서에명시했다. 나머지H32는개인조회불가를밝혔고추가로소개한공개롯데카드조건을직접PDF로검증했다(PARIS5쪽,LOCA1.2 1쪽,Play/Eat3쪽). 개인자료를보았다고주장하지않았다.
- 최초전체집계: common S_ONLY16/40; benefit U31, SEQ36, PAR32; parent U27,SEQ30,PAR31 /모두40. SEQbenefit90%가개발최고이나중요오류3건/H05·D40퇴행이남아있다. actualscope축소15질문에서benefit5개개선0퇴행,parent3개개선0퇴행. U와SEQ본문동일25개에서benefit2개개선2퇴행,parent모두동률. primary/method책임을구분할것.
- 신규52평가노트북 52_corrected_four_retrieval_modes_evaluation.ipynb를만들어전체280원문·질문·rawSHA/PDF·rubric결속및집계를실행했다. 동결유료노트북코드는그대로다. 카탈로그page/observation·SEQ진단모드·비용범위Major3개를고쳤고reviewer추가Blocking/Major0확인. 부정질문중gold없고negative_diagnostic일때만corpus_inventory SHA근거를허용하는5줄도독립검토완료. 전체86검사18.59초통과.
- source_review.json/conditional_trigger_review.json은완료execution_resultsSHA에80쌍을결속했다. 실제동결조건부셀도검증했다(conditional_gate_verification.json). 37근거문항은두PAR모두유효카드top3, H25/26/32는진입비대상. 추가LLM0호출이며BGE우월성비교를한것은아니다.
- docs/report/52_corrected_four_retrieval_modes_report.html은직접작성완료. 로컬링크/HTML구문/한국어표기/숫자분모검사통과. completion_status.json은completed_development_comparison,stage53_allowed=True,production_adopted=False다.
- 52답변물리API255,정적응답30,검증재시도5(인용혼합4: D36bU,H11bU,H20bU,H20bSEQ;토큰소진1:H11pSEQ) 모두복구했다. 의미오류는별도품질집계에포함했다. 수정52비용.243092165달러+old52임베딩.000005080=52전체.243097245. 누적known.515293475,원520한건unknown상한.003127계상시.518420475. 실제청구액미측정. 21개runtime구간wall6245.252초/CPU6080.611초/peakRSS2817916KiB/newBGEpairs560. 캐시·구간자원이지온라인전체응답속도가아니다.
- 중요한신규원인: DiscountPlan H11/H27 사실 fact_7923e642f3e8c43f426fade3의cap에는1회1만원만있고'할인받기전이용금액기준'이빠져있다. PDF5쪽과원normalized.json p5_b0012에는그문구가있다. source_review/DiscountPlan_cap_basis_diagnosis.json에원문/정규화본문/DB대조를저장했다. S_ONLY의할인액1만원/2만원20%=4000원오답에입력누락도기여했다. 원본DB/OCR수정안함. 전체3403행오류율을측정한것은아니다.
- 53 NB_LAST_CELL=1 오프라인실행(session86681)완료: 12질문96조건manifest/rubric/해석보완/코드가동결됐다. API아직0. 기존prepaid_review_acceptance는이전초안기록이므로최신review를별도paid_entry_review로기록한뒤승인된API를시작한다. 53/54를아직완료로보고하지말것.

남은순서:52결과curatedcommit→53유료96논리조건·96원문판정·A/B선정·직접HTML→54설정동결/새32질문·원문gold/must/material/context사전동결→96추출·새predicate차분원문검토→672답변·3회평가·직접HTML→검증/커밋. 운영코드수정없음. docs/EVALUATION_METRICS.md의기존사용자prefix변경은계속unstaged로보존하고이번추가단락만append패치로stage할것.

## 53 완료 및 54 사전등록 체크포인트 (2026-10-22)

- 52 결과/직접HTML 보존 커밋 b1eaa37. 54 신규32질문·must/material/context·PDF근거와 검토 전 초안을 유료 출력 전에 9a8aed7b로 사전등록했다. 54 질문 분포 benefit8/compound_detail8/issuer_product8/numeric4/negative4, 단순말바꿈보다 새상품·주제확대. proposal_reviewer Major5개(추가율/적립수치/미확인판정/실적제외범위/직접질문목록·한도)를 고치고 Blocking/Major0 수락받았다. I02/7CORE 원본PDF이미지도 main이 추가 확인했다.
- 53 유료 제어기83407은96/96 완료 후 정상 종료했다. 유료 실행 중 미완료 source_judgments는 in_progress로 옮겨 평가셀의 조기 집계를 막았고 최종96개를 읽은 뒤 source_judgments.json으로 확정했다. 원본응답/요청/재시도 보존. H11 benefit A:r2의 대상금액1만원상한 누락과 H21 IBK앱범위 모호함은 reviewer가 PARTIAL material_omission 경계를 독립 확인했다. 평가규칙 자체는 사전동결 그대로다.
- 실제동결평가셀1/4만 오프라인 실행해96개 raw/augmented SHA·질문·PDF소유권·rubric·동일입력공유를 검증했다. evaluation_summary: benefit A18/24(18PASS,5PARTIAL,1FAIL) vs B19/24(19,4,1), H11에서1승0패11동률, B선택. parent A15/24(15,8,1) vs B17/24(17,7,0), D36에서1승0패11동률이나 H11:B:r2가 A에있던DiscountPlan전월40만원·월한도/FLEX30만원을 잃어 조건손실1건→A유지. 중요오류benefit A/B각1(H21r2전부앱제외),parent0. 최종API생성실패parentA H11r1 1건은포함한수치다.
- 53 논리96=유료최초84+동일회차A공유B12. 실제API87=84+검증재시도3. 물리실패4모두max_output_tokens2400; H11pAr1은최초·재시도모두실패, H11pBr2/H22pAr2는재시도성공. 토큰한도를도중에변경하지않았다. 비용.088692140달러, 누적known.603985615; 원520unknown상한.003127포함예산차감.607112615<8. 입력671228/출력103069토큰. 신규검색·임베딩·재정렬0. GPU미사용.
- 53 답변확보시간24개평균/중앙/P95/최대: bA15.950/14.716/34.660/40.320초,bB12.653/12.469/18.702/20.668초,pA22.913/14.282/64.057/67.249초,pB21.428/13.694/64.057/67.994초. 동일입력공유B는캐시읽기0.001초를새생성으로비교하지않고같은회차A완료시간을승계했다. 바뀐입력18개씩별도통계resource_summary.json. TTFT·UI표시·온라인전체시간미측정. 유료실행2구간1534.847초,마지막구간CPU91.693초/RSS372580KiB;전체CPU/최대RSS미측정. 약1.2초오프라인사전셀구간은유료batch합에서제외했다.
- docs/report/53_sequential_fact_supplement_report.html 직접작성완료, 링크/한국어문구/96분모검사. 53·54관련35검사4.17초통과. completion_status=completed_development_comparison,stage54_allowed=True,production_adopted=False. 53노트북평가출력도갱신했으나전체유료노트북재실행은아니다. 52/53참고주석포함여부는전수별도판정이아니므로uncertain과미측정한계를보고한다.

다음:53curatedcommit→54 frozen_configuration(BGE,benefitB,parentA,52/53summarySHA)→오프라인cell1 manifest동결→현재코드·검토해시paid_entry기록→NB_LAST_CELL=2로신규96추출만→cell3 predicate차분probe·전체원문검토→paid_answer_entry→672검색/답변3회→원문672판정·평가·직접HTML→검증/curatedcommit. 54질문32및rubric/source는이미유료출력전동결되었으므로사후편의수정금지. 53끝난PTY83407은회수완료,현재유료프로세스없음. 운영코드·OCR·원본DB/PDF/색인은계속수정금지. 원520하나만미확인비용예외,새미확인실패는중단규칙유지.
