# 원문 근거 연결·고정 카드 근거 보완 Implementation Plan

> **For agentic workers:** Execute according to the active AGENTS.md. Main Codex가 구현·검증하고 기존 reviewer가 읽기 전용으로 검토한다.

**Goal:** 원문 관계를 검증한 근거 보완이 카드·순위 고정 상태에서 답변을 개선하는지 확인한다.

**Architecture:** 42번의 동결된 48질문·실제 QueryPlan·S_THEN_U 검색/답변, 30번 SQLite, 31번 기존 청크를 읽기 전용 재사용한다. 파서/임베딩/검색/BGE는 호출하지 않는다. 45번에 새 근거 패키지·검토·보고서만 작성한다.

**Tech Stack:** 기존 Python 환경, SQLite immutable read, Pydantic/OpenAI 기존 답변 생성기, pytest, nbclient, Markdown→HTML.

**Spec:** 현재 사용자 승인 계획과 `docs/STRUCTURED_SEARCH_DB_SPEC.md`.

## Global Constraints

- 새 작업은 notebooks/45_* 및 notebooks/data/45_evidence_supplement_comparison/, 결과 HTML은 docs/report/45_evidence_supplement_comparison_report.html.
- 운영 코드·DB·OCR·원본 PDF·이전 실험·사용자 의존성 변경 보존. 근거 중간 절단/재청킹/새 임베딩 없음.
- 입력 카드/순위 고정. 일부 AND 활용·카드 재정렬·실패 처리 변경·운영 반영은 제외.
- 전체 48×2프로필×2방식=192논리조건, 양성44와 진단4 분리. benefit 주 후보, parent-child 보조 비교.
- 순차 실행. 카드별 최대5근거/기존64000 UTF-8 bytes 상한. 카드별 첫 기준근거를 보존.
- 완전 충족 그룹의 matching_fact만 사용. 요구조건 순서로 근거를 우선 배치하고 잔여 자리를 기존 근거로 채운다.
- gpt-6-luna/medium·기존 prompt/schema·2400 출력 한도·기존 재시도 유지. 새 API는 변경된 B 답변에만 허용.
- 기존 답변과 신규 생성의 시점/확률성 차이를 명시. 독립 holdout/통계적 우월/운영 채택 주장 금지.

## Review Focus

proposal-reviewer의 사전 검토 조건을 적용한다: 저장 파서/임베딩 산출물만 읽기; requirement→fact→원문→chunk 연결 및 평가 계약을 답변 생성 전 동결; 실제 변경/동일/매핑실패/복귀를 구분; 가림 쌍대 검토와 행별 퇴행 guard.

## Task 1: 입력·원문 연결

- [ ] 입력 해시/mtime 고정, 기존 실제 계획/카드/근거 동일성 검사.
- [ ] 다른 카드/페이지/잘못된 fragment 좌표/관계 범위/다른 혜택 수치 혼입의 실패 테스트를 먼저 실행.
- [ ] 같은 fact의 원문 줄/발췌와 기존 청크를 검증하고 불확실/누락을 공개.

## Task 2: 고정 카드 근거 보완

- [ ] 카드 순위/첫 근거 보존·중복·5개/64000bytes·fallback 무변경·AND조건별 우선 배치 테스트.
- [ ] A와 B 패키지 및 평가 계약을 고정. 신규 답변 전에 코드/검증 결과 리뷰.
- [ ] 동일 패키지는 A 답변 재사용, 변경된 B만 새 답변 생성. 모든 시도 원응답/사용량/실패 보존.

## Task 3: 평가·보고

- [ ] 기존 자동 점수와 별도 가림 원문 판정을 구분하고 같은 입력은 동일 검토 기록 재사용.
- [ ] 전체/실제보완/유형별 요구근거 회수·조건설명·핵심요구·부수오류·인용·성공·승패·비용/자원 공개.
- [ ] 행별 핵심요구/수치·조건오류/진단/성공 퇴행이 있으면 보완방식을 개선후보로 선정하지 않음. 동률이면 A 유지.
- [ ] 노트북 실행, 결과 감사, 원본 보존, 독립 리뷰 후 main Codex가 커밋.
