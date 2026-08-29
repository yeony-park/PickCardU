# OCR benchmark data

OCR benchmark의 gold, 평가 요약, 보조 자료를 보관한다.

## 구성

현재 로컬 구조를 기준으로 한 직속 항목이다. 캐시·비밀값·대용량 실행 산출물은 목록에서 제외한다.

| 항목 | 설명 |
|---|---|
| `mistral_raw/` | Mistral OCR 제공사 원본 응답. |
| `pymupdf/` | PyMuPDF 문서별 텍스트 추출 결과. |
| `reports/` | OCR benchmark 집계 보고서. |
| `upstage_raw/` | Upstage Document Parse 원본 응답. |
| `vision/` | Vision OCR 비교·검증 자료. |
| `gold/` | 정답·라벨 자료 |
| `mistral_raw/` | Mistral OCR 제공사 원본 응답 |
| `normalized/` | 엔진별 정규화 결과 |
| `pymupdf/` | Mistral OCR 제공사 원본 응답 |
| `reports/` | Mistral OCR 제공사 원본 응답 |
| `text/` | 텍스트 비교 자료 |
| `upstage_raw/` | Mistral OCR 제공사 원본 응답 |
| `vision/` | Mistral OCR 제공사 원본 응답 |
