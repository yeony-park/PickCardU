# 실험 결과 보고서

노트북 실행 결과와 원본 대조 해석을 HTML 보고서로 정리한 폴더다.

공개 저장소에는 보고서 본문과 핵심 집계 결과만 포함한다. 보고서 안의 일부 로컬 상세 산출물 링크는 공개 대상에서 제외된 파일을 가리킬 수 있다.

## 구성

| 파일 | 설명 |
|---|---|
| [`01_pymupdf_text_extraction_report.html`](01_pymupdf_text_extraction_report.html) | PyMuPDF 텍스트 추출 가능 여부와 깨짐 탐지 결과다. |
| [`02_vision_paddle_upstage_ocr_comparison_report.html`](02_vision_paddle_upstage_ocr_comparison_report.html) | Vision, Paddle, Upstage, Claude Vision의 공통 표본 OCR 비교 보고서다. |
| [`03_hybrid_verification_pipeline_report.html`](03_hybrid_verification_pipeline_report.html) | 초기 하이브리드 OCR 검증·라우팅 실험 보고서다. |
| [`04_vision_upstage_cross_validation_report.html`](04_vision_upstage_cross_validation_report.html) | BC·NH 46페이지 Vision·Upstage 대조와 원본 기준 재판정 보고서다. |
| [`05_goldset_ocr_structured_evaluation_report.html`](05_goldset_ocr_structured_evaluation_report.html) | 수기 전체·필드 정답셋 기준 OpenAI Vision·Upstage OCR 및 구조화 평가 보고서다. |
| [`06_semantic_critical_fact_ocr_evaluation_report.html`](06_semantic_critical_fact_ocr_evaluation_report.html) | 10개 카드의 semantic fact 및 Luna·Terra 반복 OCR 비교 보고서다. |
| [`07_relational_critical_fact_evaluation_report.html`](07_relational_critical_fact_evaluation_report.html) | 관계형 평가와 OCR·JSON 수치 오류 발생 단계 귀속 결과를 정리한다. |
| [`08_clean_end_to_end_ocr_benchmark_report.html`](08_clean_end_to_end_ocr_benchmark_report.html) | clean end-to-end OCR·Luna 구조화·critical fact 평가 결과다. |
| [`09_hierarchical_chunking_retrieval_report.html`](09_hierarchical_chunking_retrieval_report.html) | 계층 청킹·retrieval baseline 및 ablation 결과다. |
| [`10_search_normalization_morphology_ablation_report.html`](10_search_normalization_morphology_ablation_report.html) | 검색 정규화와 Kiwi 형태소 ablation 비교 결과다. |
| [`11_normalized_rrf_weight_ablation_report.html`](11_normalized_rrf_weight_ablation_report.html) | Vector:정규화 BM25 RRF 가중치와 reranker 후보군 coverage 비교 결과다. |
| [`12_gte_reranker_ablation_report.html`](12_gte_reranker_ablation_report.html) | 현재 청킹의 reranker·MMR·GTE·BGE 비교 결과다. |
| [`13_structural_heading_chunking_ablation_report.html`](13_structural_heading_chunking_ablation_report.html) | 구조 청킹+제목 경로 검색문과 기존 기준선의 개발셋 비교 결과다. |
| [`14_llm_answer_quality_pipeline_comparison_report.html`](14_llm_answer_quality_pipeline_comparison_report.html) | 검색 패키지와 입력 깊이에 따른 LLM 답변 품질 비교 결과다. |
| [`15_human_blind_answer_evaluation_report.html`](15_human_blind_answer_evaluation_report.html) | 저장 답변을 사용자가 익명 A/B로 비교한 소표본 평가 기록이다. |
| [`16_cross_package_holdout_v2_report.html`](16_cross_package_holdout_v2_report.html) | cross-package holdout v2가 공통 근거 projection 실패로 무효 종료된 기록이다. |
| [`17_integrated_holdout_candidate_evaluation_report.html`](17_integrated_holdout_candidate_evaluation_report.html) | sealed 30문항의 검색·근거 회수 및 LLM 답변 통합 평가 결과다. |

`04`의 자동 통과 판정은 비율·금액·전월 실적 핵심 사실 범위에 한정된다. 전체 OCR 정확도 또는 최종 정답으로 해석하지 않는다.

평가 지표의 정의와 해석 방법은 [`../EVALUATION_METRICS.md`](../EVALUATION_METRICS.md)를 참고한다.
