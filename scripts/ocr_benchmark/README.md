# OCR benchmark runners

OCR 엔진 실행과 benchmark 평가를 위한 재사용 runner를 보관한다.

## 구성

현재 로컬 구조를 기준으로 한 직속 항목이다. 캐시·비밀값·대용량 실행 산출물은 목록에서 제외한다.

| 항목 | 설명 |
|---|---|
| `evaluate_ocr_benchmark.py` | 실행·평가·검증에 사용하는 Python 파일. |
| `run_mistral_ocr.py` | OCR 실행 runner. |
| `run_pymupdf.py` | PyMuPDF 텍스트 추출 runner. |
| `run_upstage_document_parse.py` | OCR 실행 runner. |
