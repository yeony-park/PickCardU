# 검색 조건 오류와 새 질문 비교 실행 계획

> **For agentic workers:** Execute according to the active AGENTS.md. Main Codex가 구현하며 기존 proposal-reviewer와 reviewer가 순차 독립 검토한다.

**Goal:** 승인된 테스트1~4를 순서대로 실행하고 운영 검증 범위, 조건 추출 오류의 영향, 보완된 평가, 새 질문의 청킹·검색 비교를 재현 가능한 자료와 HTML로 남긴다.

**Architecture:** 운영 함수는 읽기 전용으로 호출한다. 진단은 오프라인 고정 응답을 주입하고, 새 질문 비교만 기존 승인 API를 한 차례 호출한다. 정확한 요청별 캐시·원응답·사용량을 보존한다.

**Tech Stack:** 기존 skn25 Python, Pydantic, FastAPI TestClient, OpenAI SDK, SQLite/Chroma 복사본, 기존 BGE 모델.

**Spec:** 이 문서의 사용자 요구사항: 테스트1~4 순차 실행, 반복 실행5 제외, 운영 코드와 OCR·제목 계층 수정 금지.

## Global Constraints

- 새 코드·노트북·입력·캐시는 notebooks/ 안, HTML은 docs/report/ 안, 노트북과 보고서 번호 일치.
- 30~36 실험과 운영 원본은 수정·삭제하지 않는다. 기존 사용자 pyproject 변경을 보존한다.
- 기존 환경·모델·Upstage106문서·운영 청킹 사용. 청크 길이·토큰 제한 변경 없음.
- 두 운영 프로필의 재정렬 차이는 유지하며 프로필 비교라고 명시한다.
- parent-child는34번에서 선택한 온전한 청크 최대5개·토큰 확인 묶음, 답변 출력2400 고정.
- 새18질문은 수치/조건6·카드 상세6·의미/혜택6. 새로 작성한 동일 코퍼스 확증 질문이며 독립 비공개 holdout이 아니다.
- 모든 실패 행을 분모에 포함한다. 일반·정답 미확정 질문은 양성 지표에서 제외하고 별도 보고한다.
- API 신규 물리 요청과 캐시 재사용을 분리하고 실패 사용량도 보존한다. 비용은 사용량×공식 단가, 청구액은 미측정.
- 반복 실험5, 질문 보강 방식, 제목 계층 개선은 실행하지 않는다.

## Task 1: 37 운영 검증 진단

Files: 37_followup_common.py,37_operational_guard_audit.py,37_operational_guard_audit.ipynb, data/37_operational_guard_audit/, docs/report/37_operational_guard_audit_report.html.
Consumes: 운영 AnswerOutput,validate_grounding,OpenAIService.answer,create_app.
Produces: guard_results.json의 입력·실제 결과·예상 방어범위, 신규 API0 증거.

- [x] 실제 운영 검증에 정상·잘못된 혜택률·적립/할인 혼동·월/일 혼동·다른 카드 인용·없는 인용·빈 주장·형식 오류 주입.
- [x] 실제 운영 answer 재시도에 고정 SDK 응답을 주입해 형식 실패 후 정상 응답과 의미오류 통과를 확인.
- [x] 실제 API 경로에 가짜 검색 handle을 주입해 근거0이면 provider.answer를 호출하지 않는지 확인. 테스트는 존재하는 운영 행동 측정이며 새 운영 코드를 구현하지 않는다.
- [x] 고정 응답 테스트는 LLM의 실제 오답 빈도 측정이 아니라고 기록하고 결과 노트북·HTML을 실행·생성한다.

## Task 2: 38 추출 오류와 복귀 진단

Files: 38_query_condition_audit.py,38_query_condition_audit.ipynb,data/38_query_condition_audit/,docs/report/38_query_condition_audit_report.html.
Consumes: 기존 QueryPlan,select_cards,기존33 실제 parser_results,dataset_freeze.
Produces: 통제 오류별 형식 유효성·복귀 여부·후보 보존/손실·기존 실제 추출 정확도.

- [x] 실패·미해결·후보0·일반 경로 및 정상 형식의 업종/혜택 종류/숫자/월·일/AND·OR/카드 범위 오류를 고정한다.
- [x] 기존 검색의 복귀 조건과 명시카드 범위를 그대로 평가하고 잘못된 범위가 복귀에도 남는 경우를 구분한다.
- [x] 통제 오류 발생률을 실제 LLM 발생률로 해석하지 않는다. 후보 손실과 최종 답변 오류를 구분한다.
- [x] 기존33의 실제 추출 결과를 다시 계산하고 프롬프트 개선·재추출 없이 별도로 보고한다.

## Task 3: 39 평가 기준·새 질문 동결

Files: 39_answer_rubric.py,39_test_answer_rubric.py,39_freeze_new_questions.py,39_answer_rubric.ipynb,data/39_answer_rubric/,docs/report/39_answer_rubric_report.html.
Consumes: OCR 원문 fact 필드와 문서 정보; 구조화 select_cards의 결과를 정답으로 사용하지 않는다.
Produces: scorer_contract.json,dataset.json,회귀 테스트 결과,답변 실행 전에 기록한 해시.

- [x] 상세 질문에서 추천 배열 없이도 대상 카드의 주장을 평가한다. AND 조건은 같은 카드의 여러 주장으로 충족할 수 있지만 서로 다른 혜택의 수치를 합치지 않는다.
- [x] 합산은 명시적으로 주석한 동일 혜택의 구성요소에 한정한다. 다른 업종의 숫자나 다른 카드·다른 기간을 합산하면 탈락한다.
- [x] 회귀 테스트를 먼저 작성하고 계산 허용·잘못된 합산 거부·분리 주장 허용·기간 혼동 거부·상세 질문 허용을 검증한다.
- [x] 18질문·요구조건·원문 근거 ID·양성 카드·평가 기준을 동결한다. 숫자·조건 기준의 정답은 원문 필드 기준 보수적 부분 정답이며 PDF 독립 검증 결과가 아니다.
- [x] 일반 조언·불가능 조건을 별도로 표시한다. OCR에 정답이 없다는 이유만으로 실제 혜택이 없다고 확정하지 않는다.

## Task 4: 40 새 질문 108조건 비교

Files: 40_new_query_comparison.py,40_render_reports.py,40_new_query_comparison.ipynb,data/40_new_query_comparison/,docs/report/40_new_query_comparison_report.html.
Consumes:39 동결 평가·질문,기존33 추출 프롬프트와 schema,운영 청킹·검색 및34 budget5.
Produces: 조건 추출18행,검색108행,답변108행,구성/하위집단/질문별 비교,신규 요청 사용량·비용·실행시간.

- [x] 동결 해시와 소스 보존 상태를 확인한 뒤 새 질문의 조건 추출·질문 임베딩을 한 차례 실행한다.
- [ ] 신규 독립 인덱스 복사본을 사용한다. 동일 질문/프로필/범위의 BM25·vector 후보는 한 번 캡처해 비교 동안 고정한다. 글로벌 approximate와 범위 exact 차이는 유지·고지한다.
- [ ] U·구조화선행·융합을 두 프로필에서 실행하며 동일 payload는 캐시 공유한다. 같은 질문을 의도적으로 반복 생성하지 않으며 운영의 실패 재시도만 허용한다.
- [ ] 모든 조건과 하위집단, 질문별 승/패/동률, 실패·인용 소유권·원문 근거·답변 조건 충족을 보고한다. 새 평가 수치를 이전 평가 수치와 직접 비교하지 않는다.
- [ ] 결과를 확인한 뒤 기준/질문/프롬프트/설정을 바꾸지 않는다. 의미 정확성의 제한과 주요 오류 원문을 함께 공개한다.
- [ ] 37~40 노트북 셀 실행, HTML 내용/링크, 원본 해시·mtime 보존, 비용 재계산, reviewer 독립 검토를 완료한 뒤 main이 실험 파일만 커밋한다.

## 완료 증거

사전 독립 검토 반영:39 정답은 field_evidence의 Upstage 원문 줄·페이지를 대조하고 출처를 저장한다.38은 gold selector·오류 selector·최종 검색을 구분한다.37은 정상 대조군과 단일 오류 주입의 오수락/오거부를 기록한다.40의 주 지표는 양성 질문별 답변 조건 동시 충족이며 인용 소유권·명시카드 범위·검증된 수치 오류를 별도 방어 기준으로 보고한다. 운영 채택은 하지 않는다. 정답 포함과 근거 회수는 검색 진단으로 함께 보고한다. 비용 상한USD1, 조건 추출18·질문 임베딩 배치1·답변 최대108개 고유 입력×원래2시도=216, 전체 물리 요청 최대235. 상한에 도달하면 새 호출을 중단하고 미실행 조건을 공개하며 임의로 예산을 늘리지 않는다. 신규 응답은 단일 관측으로 분산·안정성 미측정이다.

각 단계 결과 JSON·노트북 실행 기록·번호 일치 HTML,39 사전 동결과 회귀 테스트,40 전체108행 및 실패 포함 집계,원문 오류 검토,요청별 비용·사용량,소스 보존 감사,독립 리뷰·Git 커밋으로 검증한다. 브라우저 시각 확인이 불가능하면 HTML 구조 검증과 구분해 미검증을 기록한다.

## 현재 실행 상태

- 37~39: 진단·평가 동결·노트북 표시 셀·HTML 확인 완료. 평가의 합산 최소비율 한계는 사후 고정 예제로 확인했고 동결 평가를 바꾸지 않았다.
- 40: 108조건 중 검색22·답변22 완료,86답변 미실행. 조건 추출18개·질문 벡터18개·물리 API41개는 정상 저장됐다.
- 최초 실행은 커널 메모리 부족으로 종료됐다. 단일 문서 재정렬5쌍(4,012토큰 포함)의 점수 차이 검증과23개 사전 테스트를 통과했다. 재개 코드·진단은2ce8ff4에 저장했다.
- 재개 시 가용 메모리1,090,664KiB가 필요 여유1,435,748KiB보다 적어 신규 모델/API호출 전에 중단됐다. 새 요청0회, 현재 요금 환산액USD0.040899290, 실제 청구액은 미측정이다.
- partial_audit.json은 원본과 기존22행·41응답·비용·네 보고서/노트북의 보존을 확인한다. full_goal_complete=false이며 전체108조건 완료 근거가 아니다.
- 같은PC에서 시작 전 약3GiB 이상의 가용 메모리를 확보한 후 동일40_memory_recovery.py로 재개한다. 이는 필요 여유의 권장값이며 이후 메모리 부족 부재를 보장하지 않는다. 완료 뒤 전체 비교·답변 원문 확인·최종 독립 감사·커밋을 진행한다.
