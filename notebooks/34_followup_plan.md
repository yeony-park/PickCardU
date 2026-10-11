# 구조화 검색 순차 후속 실험 Implementation Plan

> **For agentic workers:** Execute according to the active `AGENTS.md`. The main Codex may implement directly or assign bounded work to registered custom agents. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 청크 묶음 실패와 답변 생성 실패를 분리하고, 고정 Upstage 자료에서 두 운영 프로필과 세 검색 방식의 품질을 다시 비교한다.

**Architecture:** 30~33번 자료·운영 소스·모델은 읽기 전용으로 참조한다. 원래 검색 함수의 코드와 전역 환경을 복제한 실험 전용 함수에 묶음 생성 의존성만 주입하며 원래 함수·모듈 전역을 수정하지 않는다. 변경 가능한 인덱스는 34번에 복사하고, 기존 캐시는 읽기만 하며 새 응답·재정렬 점수는 새 번호 아래 저장한다.

**Tech Stack:** 승인된 conda skn25, 운영 Python/Pydantic/OpenAI SDK, SQLite FTS5/Chroma, 로컬 BGE tokenizer/model, HTML, ipynb.

**Spec:** 이번 대화의 후속 실험 1~6 및 `notebooks/30_structured_search_plan.md`의 고정 자료·검색 계약. 이전 결과는 `docs/report/32_structured_search_comparison_report.html`, `docs/report/33_query_routing_report.html`.

**추가 실행 통제:** 초기34 replay에서 Q10의 Chroma 근사 component 후보 변동이 확인되어 strict parity로 중단했다. 성공했던33의 BM25/vector 전체 후보 순서·점수를 재사용하고 과거실패 Q05/Q28 U만 현재 운영 retriever에서1회 생성하여 query/embedding/scope/index/chunk hash와 함께 동결한다. fixed5가 새 trace에서도 동일 초과 실패를 먼저 재현해야 해결 사례로 계산한다. 정책별 동일 후보의 조건부 비교이며 같은 시점 fresh end-to-end 엔진 비교가 아니다. 변경 bundle만 새 BGE 점수를 계산하고 융합 규칙은 유지한다.

## Global Constraints

- 파일 생성·수정은 notebooks/ 및 docs/report/만 허용. 노트북과 HTML 보고서 앞 번호 일치.
- 운영 코드·원본 자료·모델·30~33번 결과를 수정·삭제·이동하지 않는다. 기존 사용자 pyproject 두 파일 변경도 보존한다.
- OCR 검증기·제목 계층 수정 없음. 고정 Upstage 106문서 통과 가정과 현재 제목 계층 한계를 유지한다.
- 개별 운영 청크·임베딩·4096 document token 한도·8192 요청 max length·기존 검색/답변 프롬프트와 스키마 고정.
- Python 실행은 PYTHONDONTWRITEBYTECODE=1 conda run --no-capture-output -n skn25 python -B. 새 의존성 설치 없음.
- 새 LLM 호출은 gpt-6-luna, reasoning.effort=medium. 기존 실험 API 사용 승인을 적용하며 공개 카드 근거와 합성 질문만 전송한다.
- 질문 보강 후 전체 검색은 후속 실험으로 남긴다. 외부 push·운영 채택 없음.
- 33개 기존 질문은 이미 결과를 확인한 자료다. 개발 20개에서 설정을 선택하고 나머지 13개에서 선택 설정을 확인하되 독립 holdout이라 부르지 않는다. 모든 조건의 기존 자료 전체 점수는 사후 진단으로 표시한다.

### Task 1: 원본 보존 및 묶음 원인·정책 비교 (34)

**Files:** Create `notebooks/34_followup_common.py`, `notebooks/34_bundle_experiment.py`, `notebooks/34_test_bundle_policy.py`, `notebooks/34_bundle_comparison.ipynb`, `docs/report/34_bundle_comparison_report.html`; outputs under `notebooks/data/34_bundle_comparison/`.

**Interfaces:** `prepare_inputs()` copies mutable 31 indexes to 34 and freezes protected hashes; `bundle_builder(query, policy, tokenizer)` returns a callable with the original `(rows, chunks)` interface; `FollowupExperiment.search(question, embedding, profile, keys=None, policy='fixed5')` returns original search output; `run()` writes query-level token audit and full retrieval matrix.

- [x] 독립 proposal-reviewer에게 이 계획의 실험 타당성·범위·선택 규칙을 읽기 전용 검토한다.
- [x] 원본 manifest와 BGE model manifest를 먼저 검증하고, 30~33 산출물까지 포함한 크기·mtime·sha256 스냅샷을 34 아래 저장한다. 새 입력 복사·캐시의 기존 자료 해시와 provenance를 기록한다.
- [x] 자동 검증이 의미 있는 정책 행동을 먼저 실패 테스트로 작성한다: seed 필수, 외부 카드 제외, whole chunk 보존, 초과 청크 건너뛰기, 단일 seed 초과 명시 실패, 원래 함수 globals 불변.

```python
def test_budget_skips_oversized_related_but_preserves_whole_short_chunk():
    bundles = build_budgeted(rows, chunks, query='편의점 혜택', max_chunks=5, fits=fits)
    assert bundles[0]['selected_chunk_ids'] == ['seed', 'short']
    assert chunks['short'].text in bundles[0]['text']
    assert chunks['large'].text not in bundles[0]['text']
```

- [x] 원래 tokenizer의 special token 계약으로 모든 후보 묶음과 개별 청크 토큰 수를 측정하고 Q05/Q28 오류를 재현한다. 이 단계는 새 LLM/임베딩 호출 0회다.
- [x] fixed5가 원래 pipeline의 cards/evidence와 leaf/rerank/bundle trace를 재현하는지 확인한다. 시간·캐시 hit trace만 비교에서 제외한다. 과거 저장된 성공 결과와도 비교한다.
- [x] 정책 fixed5, fixed4, budget5, budget8을 parent-child × U/S_THEN_U/S_PLUS_U에서 비교한다. budget 정책은 원래 seed→관련 청크→다른 후보 순서로 전체 청크를 추가하고 실제 직렬화 묶음이 4096 및 query+document 모델 한도 안에 드는 경우만 선택한다. seed 자체 초과는 축소하지 않고 실패로 기록한다.
- [x] 질문 ID·split·하위 유형 및 Q05/Q28 모두 개발 집합임을 실행 전 동결한다. 개발 20개를 먼저 실행한다. Q05/Q28의 U 및 S_PLUS_U 실패 제거와 신규 실패 0건을 우선 통과 조건으로 두고, S_THEN_U의 기존 정답 카드 포함 질문 퇴행 없음 및 필수 근거 평균 하락 없음을 요구한다. 통과 설정 중 S_THEN_U 근거 회수율, 전체 묶음 토큰 수 순서로 선택한다. 동률은 원래 fixed5, budget5, budget8, fixed4 순서로 결정한다. 아무 후보도 통과하지 않으면 기준 설정 유지하고 미해결 한계를 기록한다.
- [x] 선택 설정과 선택 규칙 hash를 저장한 후 나머지 13개를 실행한다. 이 13개는 post-selection verification / 이미 본 질문에서 설정 선택 후 확인이며 독립 확증셋이 아니다. 기준·선택 설정 비교와 전체 사후 진단 행렬을 구분한다.
- [x] focused pytest·원본 보존·독립 리뷰·396행 감사·노트북/보고서 작성 완료. 체크포인트 커밋 ed41a97 보존.

### Task 2: 캐시 재시도 및 답변 출력 예산 (35)

**Files:** Create `notebooks/35_answer_experiment.py`, `notebooks/35_test_answer_cache.py`, `notebooks/35_answer_budget.ipynb`, `docs/report/35_answer_budget_report.html`; outputs under `notebooks/data/35_answer_budget/`.

**Interfaces:** `ResponseCache.parse(**kwargs)` implements SDK-shaped parse with faithful saved exception handling and exact-payload readthrough; `run_answer(question, evidence, budget_policy)` calls original `OpenAIService.answer`; outputs capture logical attempts and unique provider requests separately.

- [x] 34의 선택 설정과 기존 benefit 검색 근거를 고정한다. 새 캐시의 오류 예외가 원래 운영 재시도에 도달하는 것을 mock failing test로 먼저 확인한다.

```python
def test_cached_validation_failure_reaches_original_retry():
    answer = service_with_faithful_cached_failure.answer(question, evidence)
    assert answer['status'] == 'answered'
    assert fake_api.attempts == 2
```

- [x] 이전 exact payload cache는 읽기만 하며 새 요청은 35에 기록한다. 새 실패 응답은 가능한 raw status, incomplete_details, usage와 오류를 함께 기록한다. HTTP headers/API 키는 저장하지 않는다. 과거 기록에 없는 상태는 미측정으로 유지한다.
- [x] 과거 실패의 raw status가 없으므로 먼저 기존 실패행의 faithful retry2400을 오프라인 재현한다. 출력 예산 비교에서는 raw 없는 실패 요청을 같은2400으로 한 번 새 호출하여 별도35캐시에 저장하고 fixed2400/adaptive가 그 동일한 첫 응답을 공유한다. fixed4800은 별도 요청·첫 응답이며 그 자체 exact cache만 재사용한다. 새 호출만으로 성공할 수도 있어 과거 실패 원인 확정이나 토큰 증가 효과로 해석하지 않는다. historical cache replay와 fresh failure revalidation을 분리하고 단일 fixed4800 비교의 응답 변동 한계를 보고한다.
- [x] 기존 동작2400 → faithful retry2400 → 같은 근거의 faithful retry4800 순서로 재시도 복구와 출력 예산 효과를 분리한다. 별도로 raw status에서 출력 잘림이 확인될 때만 2400→4800을 비교한다. 4800 단독 성공은 원인 확정으로 해석하지 않는다. 거절·네트워크 오류를 토큰 부족으로 바꾸지 않는다. 캐시의 EOF만 있는 경우 잘림 원인 미확정으로 따로 표시한다.
- [x] 개발 집합의 모든 실패 및 대표 정상 질문에서 비교 후 전체 개발 집합으로 품질·자원 조건을 검증한다. 선택 규칙은 답변 성공 수, 기존 조건 동시 충족 통과 질문 퇴행 없음, 근거 없는 비율 증가 없음, 신규 output tokens 순서다. 동률은 2400, 조건부 재시도, 4800 순서로 결정한다.
- [x] 출력 예산이 다른 캐시와 원래 재시도 prompt를 혼용하지 않는다. 논리 행 수, provider 요청별 input/cached input/output/reasoning tokens, 실패 요청·재시도·시간과 성공 1건당 자원을 분리한다. 과거 usage 없는 실패는 미측정으로 남긴다.
- [x] 원본 보존·focused tests·reviewer 및 번호 일치 보고서/노트북 검증 완료. 594행·166개 신규 요청·54집계 행을 감사했고 c1fe030 체크포인트 커밋으로 보존했습니다. 후속 자원 미측정 표기와 감사 실행 옵션 통제도 검증했습니다.

### Task 3: 두 프로필 × 세 검색 방식 및 선별 손실 (36)

**Files:** Create `notebooks/36_comparison.py`, `notebooks/36_verify_followups.py`, `notebooks/36_profile_search_comparison.ipynb`, `docs/report/36_profile_search_comparison_report.html`; outputs under `notebooks/data/36_profile_search_comparison/`.

**Interfaces:** `run()` consumes 34 retrieval matrix, 35 frozen answer policy, 32 frozen cases, 33 frozen parser plans; outputs all six query-level retrieval/answer/score conditions. `verify()` checks every required artifact, policy/input/source hashes, notebook execution, report links, paid-request accounting and preservation snapshots.

- [x] 같은 질문·자료·모델·output policy로 benefit/parent-child × U/S_THEN_U/S_PLUS_U의 검색·답변을 실행한다. 실패 포함 전체 분모 및 양쪽 성공 질문의 공통 분모를 명시한다.
- [x] 원래 점수 규칙은 보존한다. 질문별 승리/패배/동률 및 수치/조건·카드명·의미 질문 하위 집합 결과를 함께 공개한다.
- [x] 정상 실행인데 두 방식 점수가 다른 모든 S_THEN_U 질문의 근거·조건 회수·추천/상세 응답 형식을 확인한다. Q10 추가 할인 계산, 상세 질문의 빈 추천 목록, 음성 대조의 근거 부족/답변 완료 상태를 별도 진단한다. 원래 문자열 점수를 사후 수정하지 않는다.
- [x] 구조화 선별의 확인 불가 필드 때문에 알려진 정답이 사라진 질문을 확인한다. 이미 관측된 선별 손실에 대한 strict+unknown 후보 정책을 사전 등록된 별도 진단으로 실행한다. unknown은 조건 충족으로 표시하지 않는다. 후보 팽창률·정답 복구·최종 근거 및 답변 품질을 비교하며 기본 6조건의 승자 선정에는 섞지 않는다. 나머지13개/전체 결과는 탐색적 한계를 표시한다.
- [x] 모든 HTML 결과 표 근처에 영어 지표의 쉬운 한국어 뜻, 분모·높고 낮음·통과 예·한계를 넣는다. 비용/자원 미측정은 추정하지 않는다. 관측 최고·실험 선택·운영 채택 없음을 구분한다.
- [x] 모든 새 notebook 코드 셀을 실행하고 전체 runner 실행과 노트북 표시 셀 실행을 구분한다. 링크 대상과 HTML 구조, 모든 조건 행 수, provenance, API model/reasoning을 감사한다.
- [x] reviewer를 순차 재사용하여 독립 검토하고 필요한 수정/검증을 완료한다. main Codex가 새 파일만 stage/commit하고 원래 두 pyproject는 제외한다.
- [x] 최종 요구사항별 증거를 36 completion audit에 남긴다. 실제 완료가 확인되면 active goal을 complete로 갱신한다.

## 실행·판정 메모

청크 개수 증가만으로 토큰 초과 해결을 가정하지 않는다. 34에서 실패를 제거하더라도 기존 parent-child S_THEN_U는 이미 검색/답변 33/33 성공했으므로 그 조건의 답변 열세 원인은 36의 정상 응답 근거 진단으로 확인한다. 후속 실험을 진행하며 새로 확인되는 오류는 원인과 영향을 기록하고 한 변수씩 분리하여 추가 검증한다.

## 최종 실행 결과와 보존 상태

- 34: 396행 묶음 정책 비교, 과거99행 재현 일치. 토큰 확인 최대5개를 선택했습니다. 원래 개별 청크와 모델 한도는 유지했습니다.
- 35: 594행 답변 예산 비교가 모두 실행 성공했고 기본2400을 선택했습니다. 기존 실패 요청2개가 동일2400의 새 호출에서 성공해 과거 토큰 부족은 확정하지 않습니다. 신규 요청166개입니다.
- 36: 기본 두 프로필×세 검색198행과 별도 확인불가 후보54행을 감사했습니다. 새 요청44개, 새 BGE274쌍입니다. 후보 추가는 평균6→13.48개로 늘었지만 Hit·필수 근거가 하락해 채택하지 않습니다.
- 구조화 선행에서 정상 응답 점수가 다른 Q10/Q12/Q29/Q30의 인용·조건·추천 형식을 확인했습니다. 계산값·분리 claim·빈 추천 목록의 평가 한계와 실제 적립 답변 누락을 구분했습니다. 원래 점수는 유지했습니다.
- 추가 검증: Chroma 후보 변동 통제, 재개 시52개 후보 저장본 재사용, 중복 청크 식별자, 최적화 Python에서 감사 누락 차단, 새로운 judge/정답 점수 금지 계약. focused tests29개 통과, 보호파일16861개 변경/누락0.
- 모든34/35/36 노트북의 코드12셀을 실행했고 HTML/링크/분모를 검사했습니다. 전체 유료 runner 실행은 노트북 표시 셀과 별개입니다. 브라우저 시각 렌더링은 환경 제약으로 미검증이며 HTML 구조와 링크만 검사했습니다.
- 독립 reviewer의 최종 검토에 남은 blocker/major는 없습니다. 체크포인트와 최종 감사의 연결은 notebooks/data/36_profile_search_comparison/finalization.json에 보존합니다. push·운영 채택은 없고 사용자 pyproject 두 변경을 보존합니다.
- 다음 별도 실험은 새로운 질문과 상세·분리 claim·계산 값을 평가하는 고정 기준, 다른 세션에서 보완된 제목 계층의 별도 자료 버전 비교입니다. 질문 보강 방식은 계속 후속 실험으로 남깁니다.

## 비용 보고서 후속 추가

- 2026-10-11 공식 OpenAI 요금을 확인해 저장 usage의 일반 입력·캐시 읽기·캐시 쓰기·출력을 분리 계산했습니다. 추론 토큰은 출력에 포함해 중복 계산하지 않습니다.
- 확인 가능한30~36 기록577개 중573개 소계는 USD0.550730735,34~36 신규210개는 USD0.266199825입니다. 사용량 없는 실패4개와 실제 청구액은 미측정입니다. 기존OCR·Codex대화·CPU·전력·세금·환율 등은 제외합니다.
- 35 전용 집계는35 데이터만 읽고 쓰며,36 누적 집계는 별도로 저장합니다. 생성API 호출 없이 보고서·노트북을 재생성했고 독립 리뷰 및 focused tests37개,비용577개 재계산·HTML 검증을 통과했습니다.
- 현재 우선 운영검증 후보는 benefit+질문라우팅+구조화 선행+기본2400입니다. 답변 관측 최고인 benefit융합과의 통과 차이는 Q10 하나이며 합산값 표현의 문자열 평가 영향입니다. 근거 회수41.13% 대35.89%를 우선 검증 근거로 삼되 운영 채택이나 비용 우위로 확정하지 않습니다.
