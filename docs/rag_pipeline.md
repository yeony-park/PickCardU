# 카드 안내서 RAG 파이프라인

## 결정된 범위

- 원본: `data/raw/*/*.pdf` 106개, 617페이지
- 1차 본문: `gpt-5.6-luna`, reasoning `max`, PDF를 PyMuPDF 200 DPI PNG로 렌더링
- 2차 구조 검증: Upstage Document Parse (`ocr=force`, 좌표 포함)
- 본문 충돌 처리: Luna를 원문으로 유지하고 Upstage가 덮어쓰지 않음
- 구조: Upstage heading/list/table/bbox를 이용한 페이지 내 section parent → child
- 검색: SQLite FTS5 keyword, OpenAI dense vector, RRF hybrid, weighted hybrid
- 생성: 검색 parent만 전달하고 서버가 실제 문서·페이지 citation을 해석
- PP-StructureV3: 로컬에서는 비활성. 구조 불일치가 발생한 페이지만 보류 큐에 기록

기존 `data/ocr_benchmark`는 10개 대표 문서 비교용이므로 전수 산출물을 섞지 않습니다. 런타임 산출물은 `data/rag/runtime`에 저장되고 Git에서 제외됩니다.

## 데이터 흐름

1. `manifest.json`: 원본 경로, SHA-256, 페이지 수
2. `runtime/luna_200dpi`: authoritative Markdown 본문
3. `runtime/upstage`: block type, reading order, bbox, table
4. `runtime/canonical`: 페이지별 본문·레이아웃·불일치·PP-Structure 후보
5. `runtime/chunks`: parent와 검색용 child JSONL
6. `runtime/hybrid_index.sqlite3`: FTS5 및 임베딩
7. `reports`: 검증·청킹·검색 평가 결과

## 실행

```bash
.venv/bin/python scripts/rag_pipeline/build_manifest.py

# PyMuPDF 200 DPI, 6페이지 배치, 배치 체크포인트
.venv/bin/python scripts/rag_pipeline/run_luna_parse.py \
  --workers 6 --batch-pages 6 --max-attempts 3

# Luna 완료 후 실행. 현재 코드 단가 기준 617페이지 약 $6.17
.venv/bin/python scripts/rag_pipeline/run_upstage_validation.py \
  --workers 1 --max-attempts 3

# 이미 받은 raw 응답을 새 normalizer로 복구할 때는 외부 호출을 차단
.venv/bin/python scripts/rag_pipeline/run_upstage_validation.py \
  --offline-recover-only --workers 3

.venv/bin/python scripts/rag_pipeline/build_verified_corpus.py
.venv/bin/python scripts/rag_pipeline/build_chunks.py
.venv/bin/python scripts/rag_pipeline/build_eval_queries.py

# 기본 build-index는 로컬 FTS5 keyword index만 만듭니다.
.venv/bin/python scripts/rag_pipeline/hybrid_rag.py build-index
.venv/bin/python scripts/rag_pipeline/hybrid_rag.py evaluate --mode keyword

# 외부 전송을 명시 승인한 뒤에만 child 본문을 embeddings API로 보냅니다.
.venv/bin/python scripts/rag_pipeline/hybrid_rag.py build-index \
  --embed --confirm-external-upload

.venv/bin/python scripts/rag_pipeline/hybrid_rag.py evaluate \
  --confirm-external-upload
.venv/bin/python scripts/rag_pipeline/hybrid_rag.py answer \
  --confirm-external-upload \
  "비즈 에어머니 카드의 공항 라운지는 연간 몇 번 이용할 수 있나요?"
```

진행 상황은 다음 명령으로 확인합니다.

```bash
.venv/bin/python scripts/rag_pipeline/pipeline_status.py
```

## 로컬 자연어 검색·Luna 답변 테스트

브라우저에서 검색 결과와 원문 근거를 직접 확인하려면 저장소 루트에서 로컬 서버를 실행합니다.

```bash
# Keyword 검색만 사용하며 외부 API를 호출하지 않음
.venv/bin/python scripts/rag_pipeline/serve_search_ui.py
```

그다음 `http://127.0.0.1:8765/`에 접속합니다. 서버는 시작할 때 현재 chunk corpus와 index fingerprint가 일치하는지 검증하며, 검색 결과에서 근거 child·확장 parent·문서 페이지·채널별 점수·검색 시간을 함께 보여줍니다.

Vector, RRF hybrid, weighted hybrid도 비교하려면 다음과 같이 명시적으로 활성화합니다.

```bash
.venv/bin/python scripts/rag_pipeline/serve_search_ui.py --enable-external-models
```

검색 결과의 상위 parent를 근거로 GPT-5.6 Luna 답변까지 생성하려면 generation을 별도로 활성화합니다.

```bash
.venv/bin/python scripts/rag_pipeline/serve_search_ui.py \
  --enable-external-models --enable-generation
```

`--enable-external-models`는 `OPENAI_API_KEY`와 100% embedding coverage를 요구합니다. Vector/Hybrid 검색 시 사용자가 입력한 질의를 OpenAI Embeddings API로 전송합니다. `--enable-generation`은 검색 후 사용자가 Luna 생성 버튼을 누를 때 질문과 화면에 표시된 상위 parent 본문을 OpenAI Responses API로 전송합니다. PDF 원본 전체는 전송하지 않습니다.

생성 모델은 `gpt-5.6-luna`, reasoning `medium`이며 최대 24,000자의 parent 문맥만 사용합니다. 모델은 서버가 부여한 source ID만 인용할 수 있고, 서버가 이를 실제 문서·페이지로 해석합니다. 근거가 부족하면 citation 없이 `insufficient_evidence=true`로 응답합니다.

- [자연어 검색·Luna 답변 테스트 HTML](../data/rag/reports/rag_search_tester.html)
- [팀 공유용 단계별 성능 대시보드](../data/rag/reports/rag_pipeline_dashboard.html)

모든 외부 실행은 source/config hash가 동일한 완료 산출물을 건너뜁니다. Luna는 문서 전체가 아니라 최대 6페이지씩 저장하므로 중단 후 같은 명령을 다시 실행하면 완료 배치를 재사용합니다. 다중 페이지 응답에 누락·빈 페이지가 있으면 해당 배치를 1페이지 단위로 자동 재시도합니다. 단, 36 DPI 회색조 미리보기가 완전히 흰색인 진짜 백지는 `is_blank=true`로 정상 완료 처리합니다. 기존 벤치마크의 `pdftoppm` 산출물은 일부 PDF에서 글자가 렌더링되지 않는 문제가 확인되어 전수 파싱 결과로 재사용하지 않습니다.

## 교차검증과 PP-StructureV3

페이지별로 다음을 비교합니다.

- 정규화 텍스트 유사도
- 숫자·단위 multiset 정밀도/재현율
- Upstage heading이 Luna 본문에 정렬되는 비율
- Markdown 표 개수와 행·열 구조
- block bbox 커버리지

`table_count_mismatch`, `table_structure_mismatch`, `heading_alignment_low`, `bbox_coverage_low`가 발생하면 해당 페이지만 `pp_structure_v3.status=deferred`로 기록합니다. 텍스트 차이만으로는 PP-Structure를 요구하지 않습니다. 향후 가상 서버를 구성하면 이 페이지 목록만 원격 검증기에 보내면 됩니다.

## 검색 평가

기존 구조화 OCR 골드에서 130개 query seed를 만듭니다. 조건은 다음 네 가지입니다.

- `keyword`: SQLite FTS5 BM25
- `vector`: `text-embedding-3-small` cosine
- `hybrid`: vector/keyword 후보의 RRF
- `weighted`: 채널별 min-max 후 alpha 0.2/0.5/0.8

Recall@1/3/5, MRR@10, nDCG@10, p50/p95 검색 시간을 기록합니다. 골드의 context term으로 만든 seed는 초기 회귀 테스트용이며, 최종 모델 선택 전에는 semantic paraphrase·복합 조건·답 없음 질의를 별도로 보강해야 합니다.

제품 평가는 전체 catalog 검색, 보유카드 후보군 검색, 보유카드에서 근거가 없을 때의 catalog fallback을 분리합니다. 카드 내부 evidence 탐색을 측정할 때는 `card_name`을 metadata filter로 적용하고 질문의 내부 파일명 접두사는 제거해야 합니다.

## 외부 전송 경계

- Luna 단계: 200 DPI 페이지 이미지 → OpenAI/Codex
- Upstage 단계: 원본 PDF → Upstage
- dense vector 단계: child 청크 본문 → OpenAI Embeddings API
- 생성 단계: 질문과 검색된 parent 본문 → OpenAI Responses API

키 값은 `.env`에서 읽고 산출물이나 로그에 기록하지 않습니다. 임베딩과 생성 단계는 위 전송 범위에 대한 승인을 확인한 뒤 실행합니다.

실제 전수 실행과 현재 baseline 수치는 `data/rag/reports/pipeline_performance.md`에서 확인할 수 있습니다.

온라인 채팅의 개인화 경로는 다음 절을 따른다. 위 오프라인 실험/작업 도구와 별개이며 HTTP 소비자는 `API_SPEC.md`를 기준으로 한다.

## 저장형 채팅의 온라인 개인화 경로

`/chat → Next 같은 출처 proxy → FastAPI conversation/turn 예약 → 의도 판별 → 검색 → 답변 → 서버 근거 검증 → 저장` 흐름을 사용한다. RAG release·청킹·기존 embedding은 변경하지 않는다.

- 설문은 채팅 SQLite의 conversation에 처음 한 번 저장한다. 같은 대화에서 복원하고 새 채팅에서는 초기화한다. My Page 원본은 localStorage 이름 목록이며, 새 질문마다 정확한 sourcePath 기반 document_id로 변환해 request snapshot을 저장한다.
- 첫 질문은 설문 또는 ready/needs_review wallet이 있으면 기존 rewrite provider를1회 호출한다. 설정 없는 일반 질문은0회이며 명백한 설정 누락 표현은 좁은 서버 확인 규칙만 적용한다. 후속 질문은 최근 완료 최대2쌍/과거5,500자의 기존 rewrite1회에 개인화 판별을 통합한다.
- 판별 입력은 질문·bounded 대화·참조 이름/발급사/ref·설정의 존재/상태/보유 수다. 설문값·보유 상세는 판별 전에 전달하지 않는다. `use_survey/use_wallet`, 조회/비교와 기존 global/previous/clarification·retrieve/recall을 서버가 검증한다. 일반 global 질문은 현재 원문을 사용해 과거 개인화 조건의 재유입을 막는다.
- 현재 질문의 명시 조건과 겹치는 설문 차원은 적용 context에서 제외한다. 나머지 소비 영역·혜택 enum만 검색 힌트로 사용한다. 답변용 standalone_query(최대500자)와 retrieval_query(최대1,024자)를 분리하며 월 사용액 구간을 강제 전월실적 필터로 바꾸지 않는다.
- 보유 조회는 최대106장의 허용 집합을 BM25/vector 후보 제한 전에 적용한다. 보유/신규 비교는 신규 집합에서 보유 키를 먼저 제외하고 같은 질문 embedding을 재사용한다. 최종 입력에 양쪽 온전한 근거를1개씩 먼저 예약하며 하나라도 없으면 답변 provider를 호출하지 않는다. 모든 보유 카드의 정밀 조회/비교는 최대5개이고 초과하면 확인 질문을 한다.
- 답변용 query+적용 context+근거 전체 JSON의 UTF-8 bytes를 collapse와 provider 직전의 동일 serializer로 측정한다. 기본64,000 한도는 기존 환경 설정으로 조절하며, proxy8,192-byte 요청 한도와 구분한다. 문자열/질문/청크를 임의로 잘라 예산을 맞추지 않는다.
- 확정 query/대상/적용 context와 ordered recall을 turns.execution_context_json에 저장한다. 같은 request identity의 명시적 실패 재시도만 이를 재사용한다. 현재 release의 실제 적용 키와 새 금융 근거는 다시 확인하며 과거 답변·실행 snapshot을 혜택 근거로 재사용하지 않는다.
- recall은 서버 저장 카드명·발급사·원래 순서만 안내한다. index loader·검색·embedding·답변 provider 없이 처리하고, 실패 후에도 저장된 순서를 사용한다. 정상 금융 답변은 기존 citation 존재·카드 소유권·출력 계약 검증을 유지한다.

구조화된 출력은 분류 형식을 제한하는 수단이지 의미적 정답을 보장하는 수단이 아니다. 공식 [Structured Outputs 문서](https://developers.openai.com/api/docs/guides/structured-outputs)도 잘못된 내용이 나올 수 있음을 명시한다. 가짜 provider 검증은 저장·범위·호출 수·바이트 가드를 확인하며, 실제 다양한 표현의 분류 정확도·추천 품질·추가 비용/지연은 별도 실호출 평가 대상이다.
