# PickCardU 문서

프로젝트의 API 계약, RAG 파이프라인 설명, 평가 기준과 실험 결과를 모아 둔 폴더다.

## 문서 구성

| 경로 | 설명 |
|---|---|
| [`API_SPEC.md`](API_SPEC.md) | 현재 구현된 FastAPI RAG 서비스의 HTTP 요청·응답·오류 계약을 설명한다. |
| [`API_RUNBOOK.md`](API_RUNBOOK.md) | release 활성화, 환경 설정, 실행, health 점검과 rollback 절차를 설명한다. |
| [`rag_pipeline.md`](rag_pipeline.md) | 검색과 답변 생성 파이프라인의 구조를 설명한다. |
| [`EVALUATION_METRICS.md`](EVALUATION_METRICS.md) | OCR·검색·reranker·MMR 평가 및 진단 지표를 정의한다. |
| [`report/`](report/) | 번호가 붙은 OCR·검색·답변 품질 실험 결과 보고서를 보관한다. |
| [`adr/`](adr/) | 주요 기술 결정과 근거를 기록한다. |
| [`ocr_pipeline_full_run_2026-09-03.md`](ocr_pipeline_full_run_2026-09-03.md) | OCR 파이프라인 전체 실행 기록이다. |
| [`ocr_json_validation_report.html`](ocr_json_validation_report.html) | OCR JSON 검증 결과다. |

보고서 안의 일부 로컬 상세 산출물 링크는 embedding cache, provider 원응답, 전체 payload·근거 package, pair score와 반복 실행 파일을 공개 대상에서 제외했기 때문에 열리지 않을 수 있다.
