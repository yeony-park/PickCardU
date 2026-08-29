# Experiment 05 Output

`05_hybrid_verification_pipeline.ipynb`의 라우팅·하이브리드 검증 실험 산출물이다.

## 구성

| 파일 | 설명 |
|---|---|
| `manual_field_gold_claude_verified.json` | Claude가 원본 렌더링 페이지 이미지를 직접 읽고 새로 작성한 43개 필드 정답 데이터다. |
| `pipeline_summary.json` | 파이프라인 실행 요약이다. |
| `routing_results.json` | 필드별(문서 단위가 아님) OCR 라우팅 결과다. |

현재 운영 파이프라인의 이전 교차검증 구현은 `notebooks/data/06_ocr_validation_pipeline/`과 `06_ocr_validation_pipeline.ipynb`를 참고한다. 수기 정답셋 기반 최신 평가는 `07_goldset_vision_upstage_comparison.ipynb`를 참고한다.


| `manual_field_gold_claude_verified.json` | 구조화된 평가 또는 설정 자료. |
| `pipeline_summary.json` | 집계 요약 산출물. |
| `routing_results.json` | 구조화된 평가 또는 설정 자료. |
