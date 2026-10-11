# 질문 보강 검색과 질문 확대 평가 실행 계획

> **For agentic workers:** Execute according to the active AGENTS.md. Main Codex가 직접 구현하고 기존 proposal-reviewer와 reviewer가 순차 검토한다.

**Goal:** 질문 보강 후 전체 검색을 기존 세 방식과 비교한 뒤, 새로운 48질문으로 두 운영 프로필과 네 검색 방식을 평가해 적용 후보와 남은 한계를 선정한다.

**Architecture:** 41은 39의 동결된 18질문·40의 완료 결과를 재사용하는 개발 비교다. 조건 추출 결과와 기존 구조화 DB의 고정 대상 표현을 검색문에 추가하고 전체 자료를 검색한다. 42는 결과를 보기 전에 새로운 48질문과 원문 기반 정답을 동결하고 네 방식을 모두 순차 실행하는 같은 코퍼스 확증 평가다. 같은 질문 재생성 반복과 질문 수 확대를 구분한다.

**Tech Stack:** 기존 Python 환경·Pydantic·SQLite/Chroma 독립 복사본·운영 청킹·BGE CPU·OpenAI 기존 요청/원응답 캐시·39 평가기.

**Spec:** 현재 사용자 목표와 이 문서. 테스트 동시 실행 금지, 필요한 추가 검증 허용, API 사용 승인 유지, 운영 코드 읽기 전용, notebooks/ 작업 및 docs/report/ HTML, 번호 일치, 주요 검증 시 커밋.

## Global Constraints

- 30~40 결과·원응답·입력, 운영 코드·원본 PDF·사용자 pyproject 변경은 보존한다. 새 실행환경·의존성 설치 없음.
- OCR 통과 가정과 Upstage106문서 유지. 제목 계층과 검증기 수정 없음. 프로필 차이는 청킹뿐 아니라 재정렬 경로도 포함한다.
- component50, candidate20, Top3, vector0.4/BM25 0.6, parent-child 온전한 청크 budget5 및 기존4096/8192 토큰 한도, answer2400, parser3000, gpt-6-luna medium 유지.
- 모델에는 온전한 재정렬 문서 한 개씩 제출한다. 이전40의 singleton parity 근거를 대조하며 매 호출 전 메모리 확인한다. 시작 최소 가용 메모리는 이전40 peak2,626,724KiB+524,288KiB=3,151,012KiB다. 호출 전 여유는 max(1,048,576KiB, max(max(historical_peak_kib,current_peak_kib)-current_rss_kib,0)+524,288KiB); historical_peak_kib=2,626,724, /proc/meminfo MemAvailable이 이 값 미만이면 호출하지 않음다. 메모리 부족이면 저장 후 중단·재개하고 실패를 숨기지 않는다.
- 순차 범위 제한, 융합, 전체 검색의 명시 카드 범위는 기존 parser와 동일하게 유지한다. 전체 검색은 조건으로 카드 후보를 제한하지 않지만 질문에 명시된 카드 범위는 유지한다.
- 보강은 새로운 LLM 프롬프트/질문 생성기를 추가하지 않고 기존 추출 조건과 DB의 고정 alias를 결정적으로 직렬화한다. 사용자에게 없는 숫자·최적화 조건·카드명·혜택 사실을 추가하지 않는다. OR는 OR로 표현하며 제외 대상은 긍정 검색어로 추가하지 않는다. parser 실패·unresolved·비구조화 경로는 원질문 검색으로 복귀한다.
- 보강어는 기존 대상 alias 중 원 대상이 속한 목록만 사용한다. DB 정답/eligible card 이름이나 정답 근거는 검색문에 주입하지 않는다. 단어확장과 의미동일성은 동일하지 않으므로 여행 등 넓은 범주의 결과를 별도 보고한다.
- 추가어마다 equivalent_alias(동일표현)·hierarchical_expansion(범주확장)을 기록한다. 기존 DB의 여행→호텔·커피→브랜드는 범주확장이며 의미동일성 보장을 하지 않는다. 전체 방식 비교와 범주/표현별 하위결과를 함께 보고한다. 범주확장을 포함한 전체 정책도 실험 적용 후보가 될 수 있지만, 범주확장 하위결과와 원문 의미검토를 함께 통과해야 한다. 범주확장은 의미동일성을 보장하는 방법이라고 주장하지 않는다. 실제 parser의 대상/혜택종류가 원문 또는 고정 alias로 확인되지 않으면 복귀한다. 숫자는 보강문에 재직렬화하지 않아 잘못된 수치 조건을 추가하지 않는다.
- 41에서 같은 질문 답변·근거 payload는 기존40 캐시를 정확한 입력 해시로 재사용한다. 보강문 임베딩만 새로 생성하고 답변 LLM에는 원질문을 전달한다.
- API 비용은 기존 단가의 사용량 환산과 실제 청구액 미측정을 구분하고 단가 날짜·출처를 밝힌다. 41 신규 비용 상한USD1/물리 요청100, 42 USD3/물리 요청850. 다음 호출의 입력 UTF8 bytes+4096과 출력한도 및 최대2시도의 비용을 예약해 상한 초과 전에 멈춘다. 사용량 미확인 실패 요청 발생 시 새 호출을 멈춰 원응답/요금을 먼저 확인한다.
- 매 답변 원응답·사용량을 파싱 전 저장한다. 기존 운영 재시도 최대2회 외 임의 재생성 없음. API0 로컬 회귀 검증과 실제 유료 평가를 구분한다.

## Task 1: 보강 규칙·실행 공통 경로 검증

Files: `41_query_expansion.py`, `41_followup_runtime.py`, `41_test_followup.py`, `data/41_query_expansion/`.
Interfaces: `expand_query(question: str, plan: dict) -> dict` returns search_question, added_terms, fallback_reason. Runtime provides stage-private exact-payload API cache, embeddings, singleton memory guard, private index preparation, protected input verification.

- [ ] 원문에 없는 수치 추가 금지, OR 유지, 제외대상·unresolved 처리, 카드 범위 유지, 원질문 답변 전달, 캐시 입력 바인딩을 실패 테스트로 기록한다.

```python
def test_unresolved_uses_original_query():
    result = expand_query('생활비 25만원이면 좋은 카드', {
        'route': 'unstructured', 'groups': [], 'card_keys': [],
        'unresolved': ['소비 예산은 전월실적이 아님']})
    assert result['search_question'] == '생활비 25만원이면 좋은 카드'
```

- [ ] 보강은 원질문 뒤에 검증된 대상표현·원문에 있는 혜택종류만 추가하고 숫자는 추가하지 않는다. 정답 목록은 입력받지 않는다. helper가 노트북 밖 장시간·캐시 재개 책임을 가진 이유를 노트북에 기록한다.
- [ ] API0 pytest와 이전 singleton parity baseline 해시를 확인하고 reviewer 검토 후 유료 실행한다.

## Task 2: 41 기존18질문 보강 검색 비교

Files: `41_query_expansion.ipynb`, `41_render_report.py`, `docs/report/41_query_expansion_report.html`.
Consumes: 39 dataset/scorer, 40 plans/retrieval/answers/api cache, unchanged operational chunks. Produces: 36 new logical rows and joined 144-condition comparison, query-level paired changes, cache/cost/memory/time ledger.

- [ ] 40 전체108완료·39 hash를 확인하고 개발 프로토콜을 결과 전에 동결한다.
- [ ] 보강문 생성→임베딩→두 프로필 검색→원질문 답변을 순차 실행한다. 원문과 보강문, 추가된 표현, 복귀 여부를 질문별 공개한다.
- [ ] 전체·수치/조건·카드상세·의미 질문, 실패 포함 정답 포함/답변충족/인용/범위/수치 진단을 네 방식 모두 보고한다. 기존40 시간은 역사적 측정, 이번 시간은 신규 측정으로 분리한다.
- [ ] 개발 결과만으로 운영 설정을 확정하지 않는다. 비교한 조건을 모두 남기고 42 프로토콜은 답변 실행 전에 고정한다.

## Task 3: 42 새로운48질문과 판정 기준 동결

Files: `42_freeze_questions.py`, `data/42_expanded_query_evaluation/dataset.json`, `questions.json`, `protocol.json`, `source_audit.json`.
Interfaces: `build_dataset() -> dict` produces query_id/question/gold_plan/task/gold_cards/witness_groups and raw OCR page/span audit. questions.json는 query_id/question만 포함한다. 실행 runner는 questions.json와 실제 parser 응답만 사용하며 정답은 결과 평가 함수에만 별도 전달한다. No evaluated retrieval/answers used for labels.

- [ ] 수치/조건16·카드상세16·의미/혜택16으로 작성한다. 마지막 유형에는 일반/모호/자료미확인 대조4개를 포함한다. 양성44개는 지원되는 원문 근거를 요구한다. 32/39 질문과 정확한 중복 없음. 같은 템플릿·같은카드 반복은 독립표본으로 주장하지 않고 카드·업종·질문 유형 분포를 공개한다.
- [ ] 원문 자료에서 작성한 질문 명세와 카드/업종/동일표현/범주확장/보강미적용 분포를 검색·답변 전에 동결한다. 특정 확장어가 유리하도록 결과를 본 뒤 질문을 교체하지 않는다. 대조4질문은 양성 점수와 별개의 필수 방어 기준이다.
- [ ] 39와 동일한 보수적 원문 fragment 검증으로 정답을 작성한다. 검색 selector를 정답 생성기로 사용하지 않는다. 관계가 불분명한 조건은 제외 사유를 저장한다. OCR 원문 일치와 PDF 사실 검증을 구분한다.
- [ ] 39 평가기를 변경하지 않는다. 이미 알려진 동의어 누락·부정문 수치 오탐은 사전 등록 진단으로 원응답을 확인하고 자동 점수와 별도 기록한다. 사후 점수 교체 없음.

## Task 4: 42 전체384조건 확증 평가·후보 선정

Files: `42_expanded_query_evaluation.py`, `42_expanded_query_evaluation.ipynb`, `42_render_report.py`, `docs/report/42_expanded_query_evaluation_report.html`.
Consumes: frozen48 queries and 41 expansion, original shared QueryPlan/prompt, two operational profiles. Produces: 48 parser + 384 retrieval/answer logical rows, all-condition summary, subtype and paired wins/losses/ties, cost/performance evidence.

- [ ] U/S_THEN_U/S_PLUS_U/Q_EXPAND_U × benefit/parent-child를 순차 실행한다. 동일 질문/프로필/범위 component를 캡처·고정하고 전체 검색 approximate와 범위 exact 차이를 유지한다.
- [ ] primary는 양성44질문의 답변 조건 동시 충족, 실패0점. Hit@3/MRR은 같은 질문 분모로 비교하며 Witness는 부분정답 진단이다. 일반/모호4질문은 양성 점수에서 분리한다.
- [ ] 각 설정의 Wilson95% 구간과 paired wins/losses/ties, 두 설정의 불일치에 대한 양측 exact McNemar를 보고한다. 여러 비교·상관된 질문·같은 corpus의 한계를 밝혀 유의성만으로 일반화하지 않는다. 같은 질문 재호출 분산은 미측정이다.
- [ ] 기준 후보 benefit S_THEN_U 대비 인용 소유권/명시 범위 실패 증가 또는 원문 확인된 잘못된 혜택이 있는 설정은 채택 보류한다. 하위유형에서 답변충족이2질문 이상 감소하면 평균이 좋아도 보류한다. 기준과 다른 프로필 비교는 청킹 단독 원인으로 해석하지 않는다.
- [ ] 보강 효과는 먼저 동일 프로필의 Q_EXPAND_U vs U로 평가하고, 그 뒤 기존 기준 benefit S_THEN_U와 비교한다. 보강 효과 판정은 동일 프로필 U 대비 paired wins>losses 및 모든 guardrail 통과를 필요조건으로 둔다. 그 뒤 모든 guardrail 통과와 양성44개에서 기준 대비 순개선 최소2질문을 후보 변경의 필요조건으로 둔다. 순차이0~1질문은 기존 기준을 유지하고 최고 관측값을 별도로 표시한다. 대조4질문의 원문 확인된 무근거 단정/범위·인용 오류가 기준보다 증가하면 보류한다. MRR·자원은 품질 통과 뒤 동률 설명용이다. 미실행/불완전 증거로 승자를 정하지 않는다.
- [ ] 방식별 승패가 다른 모든 질문과 모든 guardrail 위반은 원문·답변을 직접 대조한 판정을 병기한다. 자동 점수는 유지한다. 비용/메모리 cap으로 전체384행이 미완료면 부분 결과를 보존하되 후보를 선정하지 않고 목표를 완료 처리하지 않는다.
- [ ] 회귀·유료실행 결과·실패 원인·주요 개선/퇴행 원응답을 독립 reviewer가 순차 검토한다. 추가검증은 오류 귀속/평가 오탐/검색 보강 조건 보존을 해결하는 데 필요한 범위로 실행한다.

## Completion evidence

- 두 번호의 실행 가능한 노트북과 결과 HTML, 모든 표의 한국어 지표 안내·원자료 링크·최종 요약.
- 동결 입력/프로토콜/코드 hash, 신규 논리36 및384행과 실패 포함 집계, 실제 요청/캐시/usage 비용, wall/CPU/peak RAM·최저 가용 RAM·chunk/candidate/storage.
- 원문/보강 검색문과 동일 원질문 답변 바인딩, protected source preservation, API0 검증, 독립 계획·코드·결과 검토, milestone commit.
- 실험 적용 후보 선정까지 수행한다. 운영 코드 반영은 현재 읽기 전용 범위에 포함되지 않는다. 이는 운영 적용을 위한 다음 단계이며 이 목표에서 운영 적용 완료로 보고하지 않는다.
