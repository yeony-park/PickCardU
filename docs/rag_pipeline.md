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
