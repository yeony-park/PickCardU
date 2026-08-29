# Tests

OCR benchmark runner와 평가 계약을 pytest로 검증한다.

## 구성

현재 로컬 구조를 기준으로 한 직속 항목이다. 캐시·비밀값·대용량 실행 산출물은 목록에서 제외한다.

| 항목 | 설명 |
|---|---|
| `test_evaluate_ocr_benchmark.py` | 해당 runner·계약을 검증하는 pytest 파일. |
| `test_run_mistral_ocr.py` | 해당 runner·계약을 검증하는 pytest 파일. |
| `test_run_pymupdf.py` | 해당 runner·계약을 검증하는 pytest 파일. |
| `test_run_upstage_document_parse.py` | 해당 runner·계약을 검증하는 pytest 파일. |
