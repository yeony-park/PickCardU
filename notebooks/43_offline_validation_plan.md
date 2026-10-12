# 저장 답변 원문 재검토와 조건 라우팅 오프라인 검사 계획

> **For agentic workers:** Execute according to the active AGENTS.md. Main Codex가 구현·조율하고 기존 proposal-reviewer와 reviewer가 순차 독립 검토한다.

**Goal:** 운영 코드를 수정하지 않고, 저장 답변 평가의 오탐과 구조화 후보 선택·복귀 경로의 위험을 확인한다.

**Architecture:** 42의 공개 질문·실제 parser 응답·검색 근거·답변을 읽기 전용으로 재사용한다. benefit U/순차/융합의 양성44×3=132답변과 대조4×3=12답변을 원문 상대 판정으로 점검한다. 실제48개 조건 계획 및 통제된 반례는 기존 QueryPlan과30의 선택기를 호출해 재생하며, 검색 모델·LLM·임베딩은 호출하지 않는다.

**Tech Stack:** 기존 승인 Python 환경, Pydantic, 기존 구조화 fact 선택기, JSON 원응답, pytest, nbclient, HTML 보고서.

**Spec:** 이번 사용자의 노트북 선검사 요청과 운영 코드 변경 금지. 앞선 두 설명의1번과2번 중 오프라인 검사 가능 부분이 범위다.

## Global Constraints

- 새 파일은 notebooks/43_* 및 notebooks/data/43_offline_validation/에만 작성하고 HTML은 docs/report/43_offline_validation_report.html에 작성한다.
- 운영 코드·DB·색인·OCR·이전30~42결과와 사용자 pyproject 변경은 수정하지 않는다. 이전 결과 해시/mtime를 검사한다.
- 새로운 API/모델 호출·패키지 설치·활성 색인 전환·서버 실행 없음. 유료실험 동시 실행 없음.
- OCR 통과 가정과불완전한제목계층유지. 이번 비교는benefit프로필만이며청킹우열을재선정하지않는다.
-42의동결점수는변경하지않는다. 새로운원문판정은사후진단이며독립평가또는운영채택근거로승격하지않는다.
- 독립 계획 검토의 조건을 적용한다: 핵심요구 PASS와잘못된핵심혜택추천을분리하고,오류증가시우세판정을차단한다. 판정계약/질문별필수요소·fixture예상을검토/실행전에동결한다. 근거우선순위는저장원문페이지→소유검색근거→구조화fact→답변표현이며부정/유보는자료부재와전역부재를구분한다.
- 실제42의parser예외/스키마/카탈로그검사→selector→fallback정책을실험용복제진입점에서재생한다. 운영통합실행과동일하다고주장하지않고저장48경로의일치·코드해시·정책차이를명시한다. 빈그룹/null·수치경계·잘못된단위·명시카드와후보0의반례도포함한다.

## Task1: 입력과 판정 범위 고정

Files:43_offline_validation.py,43_offline_validation.ipynb,data/43_offline_validation/protocol.json.

- [x] 42완료감사·원행/정답바인딩·원본보존을확인하고43별도보존목록을만든다.
- [x] benefit세방식144행의해시를고정한다. 검토자는U/순차/융합의관측순위를점검기준으로쓰지않는다.
- [x] PASS=원질문의대상·행동·수치·AND/OR를적어도하나의소유인용된추천카드가충족. detail은해당카드의요구혜택과지원조건을설명. PARTIAL=관련혜택일부만확인. FAIL=핵심요구미충족. UNCERTAIN=주장과자료관계불명확. 조건부서비스는조건을밝히면허용하고미확인적용을확정하지않는다.
- [x] 여러카드로AND를나눠충족시키거나,할인을적립으로대체하거나,유보/부정claim의대상문자열만으로PASS하지않는다. 목록밖카드도인용/근거가있으면인정한다. 코드가새로운LLM judge처럼의미판정을자동생성하지않는다.
- [x] 임의의완전한사실검증성공률을주장하지않고,핵심요구판정과부수주장오류·검토불확실성을분리해보고한다. 전체원문검토는Codex/독립검토자판단이며사람의블라인드검증이아니다.

## Task2: 저장 답변 원문 재검토

Files:data/43_offline_validation/review_packets.json,source_review.json,answer_review_summary.json.

- [x] 검토패킷에원질문·답변·소유근거·구체card/page/chunk 참조를넣는다. 기존42자동점수는사후연결에사용한다.
- [x]144조건을PASS/PARTIAL/FAIL/UNCERTAIN/대조기대행동으로점검하고판정이같은정확한동일답변·근거는중복제거해같은검토기록에연결한다.
- [x]44양성분모에서설정별판정분포와순차vsU/융합의승패동률·유형별차이를보고한다. 불확실은통과로합산하지않는다. 관측최고와기존기준후보유지를구분한다.
- [x] E02의유보문자동통과,E14/E15목록밖대안,E31합산,E39영화관표현,E38유효PointPlan,E44마트표현을경계사례로직접확인한다. 기존기록은출처가있는참고로만쓰고이번범위의누락을메우지않는다.

## Task3: 기존 선택기와 복귀 경로의 오프라인 재생

Files:43_routing_replay.py,43_test_routing_replay.py,data/43_offline_validation/routing_results.json.

- [x] 기존42실제48parser행을동일QueryPlan/selector에재입력하고저장eligible_cards·fallback과일치하는지대조한다.
- [x] 독립적으로예상결과를정한합성fact/계획으로할인vs적립,같은카드AND,OR,월vs일한도,명시카드범위,후보0,unresolved,일반질문,parser실패/형식오류를검사한다.
- [x] 형식은정상이지만대상·행동·수치·AND/OR가잘못된계획도주입해복귀되지않는지·정답카드가후보에서빠지는지측정한다. 이위험을발견해도운영코드나기존정책을고치지않는다.
- [x] pytest는정해진복귀/범위/조건논리와저장일치를검증한다. 위험반례는현재동작에대한관측으로기록하며테스트통과를운영안전성보장으로바꾸지않는다.
- [x] 출력한도/메모리/실제LLM추출빈도·API지연은이번오프라인검사로측정할수없음을명시한다.

## Task4: 보고·검증·보존

Files:43_render_report.py,43_offline_validation.ipynb,docs/report/43_offline_validation_report.html,data/43_offline_validation/completion_audit.json.

- [x] 모든설정·유형의분모/판정/한계및근거링크,실행요청0·추가사용량비용0·측정시간/메모리를보고한다. 새판정분포의의미·예시·한계는노트북/HTML안에정의하고docs의기존지표문서는이범위에서수정하지않는다.
- [x] 노트북표시셀을실행하고새회귀·원행해시·리뷰누락·HTML링크/지표안내·운영파일/기존결과보존을검사한다.
- [x] 기존reviewer의순차독립코드/결과검토를받고주요산출물을main Codex가커밋한다. 운영반영/활성화/배포는진행하지않는다.

완료 근거: `data/43_offline_validation/completion_audit.json`, `verification_commands.json`, `final_independent_review.json`. 운영 적용 전 경계 사례는 후속 항목이며 이번 범위에서 수정하지 않았다.
