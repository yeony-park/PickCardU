# 12. Clean End-to-End OCR Benchmark

`12_clean_end_to_end_ocr_benchmark.ipynb`의 재현 가능한 실행 골격과 참조 정보만 보관한다. 기존 01~11 산출물은 이동하거나 복사하지 않는다.

- `current/`: 새 실행 결과. 실행 시 `runs/<run_id>/{raw,normalized,structured,evaluated,status}`, `run_manifest.json`, live 실행의 `summary.json`을 만든다.
- `baseline/`: 09~11 및 기존 Upstage 산출물의 optional 프로젝트 상대경로와 SHA-256 참조만 사용한다.
- `historical/`: 12번에서 더 이상 current가 아닌 이전 실행 또는 실패 실행을 보관한다. 자동 이동은 하지 않는다.

## 실행 계약

필수 입력은 10개 PDF, 대응하는 gold raw TXT·structured JSON, `critical_rules_v2.json`이다. 실제 PDF 페이지 합계가 50인지 확인하며 dry-run은 OpenAI OCR 100회, Upstage 10회, oracle을 제외한 구조화 30회로 계산한다. 09~11과 기존 Upstage 결과가 없어도 clean run 생성은 실패하지 않는다.

runner는 `ocr`, `structure`, `evaluate` stage를 분리하며 기본 실행은 dry-run이다. normalized v2의 canonical 텍스트는 `pages[].text`다. `[PAGE n]`은 다중 페이지 TXT 직렬화 시 orchestration 계층만 추가하고, panel marker 제거는 gold 텍스트 평가 함수에만 허용한다. provider 텍스트에 같은 marker가 있으면 normalized 데이터에 충돌 횟수를 기록하며 PAGE 충돌이 있는 TXT 직렬화는 거부한다.

BC Biz AirMoney의 gold raw는 structured gold의 `annotation_scope`에 따라 **selected excerpt**이며 IBK Point3.8은 불완전하거나 범위가 모호하므로 full-page CER에서 제외한다. 나머지 8개는 `full_page_candidate`지만 시각 감사 전 후보일 뿐 확정하지 않는다. 특히 Hyundai는 PDF 1~2쪽 시각 확인을 권장한다. Woori의 `[page1]`/`[page 1]` 차이는 parser에서 공백을 허용하며 marker 리터럴 일치를 요구하지 않는다.

runner는 명시적인 engine 선택과 `--live-api`가 함께 있을 때만 호출하고, raw provider 응답은 `current/runs/<run_id>/raw/`에 먼저 보존한다.

구조화는 기존 normalized 30개를 모두 상수 `gpt-5.6-luna`와 동일 prompt/strict schema로 처리하며 환경변수로 모델을 바꾸지 않는다. gold structured에서는 ID, page, 값의 type/shape, 표 열 수만 value-less contract로 만들고 gold 값과 context terms는 요청에서 완전히 제외한다. 응답 전체는 `raw/field_extraction/`, 파싱 결과는 `structured/`에 따로 저장한다. 16쪽 카드도 우선 단일 호출하고 실제 context-limit 오류일 때만 page 번호 순서의 절반으로 결정론적으로 분할하며 충돌 없는 merge를 검증한다. 예상 30회와 별도로 전역 `MAX_STRUCTURE_CALLS=60`을 적용하며 각 성공·실패 시도를 `raw/field_extraction_attempts/`에 보존한다.

평가는 외부 호출 없이 `evaluated/` 아래 JSON/CSV만 쓴다. manifest에서 `excluded_until_visual_audit`인 8개는 공식 집계에서 제외하고 `candidate_unapproved` preview로만 표시한다. 따라서 현재 공식 TXT aggregate는 승인 카드 0개라 null이다. BC selected excerpt는 structured gold `text_labels`의 page와 `raw_start_marker`로 양쪽 텍스트를 같은 페이지·marker부터 페이지 끝까지만 자른다. marker 줄은 NFKC·공백 정규화 후 선행 Markdown heading(`#`+공백)만 제거해 exact 비교하고 code fence 구분 줄만 무시한다. 다른 페이지, fuzzy matching, 그 밖의 문자열 변형이나 전체 페이지 대체는 허용하지 않으며 marker가 없으면 `missing_marker`다. IBK ambiguous도 별도다. structured label exact와 critical v2 projection은 30/30 구조화·integrity가 유효할 때만 계산하며, 그 전에는 분모 0/null이다. critical v2의 table-row는 explicit `table_id/row_index/column_index` locator가 없으므로 relation pass 분모에서 제외하고 `needs_review`로 남긴다. 페이지·ID 유사도 기반 table heuristic과 field fallback은 진단 건수일 뿐 accuracy가 아니다. source-supported audit 수와 실제 relation-scorable 분모를 별도로 보고하며 BC처럼 table-row가 없는 사실은 기존 비표 지표에 포함한다. unit accuracy 분모는 relation-scorable `numeric__`/`supplementary_numeric__` source fact 중 expected-unit 계약이 있는 모든 행이다. prediction unit 누락/null은 오답이며 atomic relation도 실패한다. `field__` source의 TEXT/BOOLEAN 등 canonical type은 별도 unit prediction을 요구하지 않는다. 통합 micro와 함께 TXT candidate preview, structured 주요 exact, critical 주요 지표를 엔진별로 같은 계약 아래 기록한다. incomplete CLI는 기본 nonzero이고 의도적으로 허용할 때만 `--allow-incomplete`를 쓴다. bundle CSV에는 generation ID가 있으며 summary가 마지막 commit marker다.

live runner는 provider 초기화·호출 전에 `.locks/<run_id>.lock`을 원자적으로 획득한다. 같은 run ID는 한 프로세스만 실행하며 다른 run ID는 병렬 실행할 수 있다. lock에는 PID, host, boot/process identity, 시작 시각과 scope가 기록되고 정상 종료와 예외에서 해제된다. 비정상 종료로 lock이 남으면 파일을 직접 삭제하지 않는다. 같은 host에서 기록 프로세스가 실제로 종료됐거나 PID가 재사용됐음을 identity로 확인할 수 있을 때만 `--recover-stale-lock`을 명시한다.

## Stage runner

기본 실행은 네트워크를 사용하지 않는 dry-run이다.

```bash
conda run -n skn25 python notebooks/12_clean_end_to_end_runner.py
conda run -n skn25 python notebooks/12_clean_end_to_end_runner.py --cards BC/BC_Biz_AirMoney --engines openai_luna,openai_terra,upstage
conda run -n skn25 python notebooks/12_clean_end_to_end_runner.py --stage structure --run-id 20260812T190400Z --engines openai_luna,openai_terra,upstage
conda run -n skn25 python notebooks/12_clean_end_to_end_runner.py --stage evaluate --run-id 20260812T190400Z
```

실제 유료 호출은 `--live-api`를 별도로 명시해야 한다. 아래 명령은 실행 전에 카드·엔진·키·비용을 다시 확인한다.

```bash
# LIVE / PAID API — 승인 후에만 실행
conda run -n skn25 python notebooks/12_clean_end_to_end_runner.py \
  --stage ocr \
  --run-id YYYYMMDDTHHMMSSZ \
  --cards BC/BC_Biz_AirMoney \
  --engines openai_luna,openai_terra,upstage \
  --live-api
```

구조화 30회도 별도의 명시적 live 명령만 허용한다.

```bash
# LIVE / PAID API — 30개 normalized 입력·비용 확인 후에만 실행
conda run -n skn25 python notebooks/12_clean_end_to_end_runner.py \
  --stage structure \
  --run-id 20260812T190400Z \
  --engines openai_luna,openai_terra,upstage \
  --live-api

# OFFLINE — provider 호출 없음
conda run -n skn25 python notebooks/12_clean_end_to_end_runner.py \
  --stage evaluate \
  --run-id 20260812T190400Z \
  --execute-offline \
  --allow-incomplete
```

같은 host에서 소유 PID가 종료된 stale lock임을 확인한 경우에만 마지막에 `--recover-stale-lock`을 추가한다. 살아 있는 PID 또는 다른 host의 lock은 복구하지 않는다.

전체 OCR 계획은 OpenAI 페이지 100회와 Upstage 문서 10회, 합계 110회다. 구조화 정상 계획은 30회이며 context-limit 분할이 실제 발생하면 추가 호출이 생길 수 있다. BC smoke OCR 계획은 OpenAI 4회와 Upstage 1회다. live 실행은 실패가 하나라도 있으면 stage summary에 기록하고 nonzero로 종료하며, 복구 재실행은 append-only attempt 이력과 `recovered` current 상태를 남긴다.


## 구성

| 항목 | 설명 |
|---|---|
| `baseline/` | 09~11 검증 결과와 기존 Upstage 산출물을 참조하는 기준 자료 폴더. |
| `current/` | 새 end-to-end 실행 기록을 보관하는 현재 자료 폴더. |
| `historical/` | 01~08 기존 실험 자료를 보관하는 과거 자료 폴더. |
| `quarantine/` | 직속 하위 자료를 보관하는 폴더다. |
