# Reports

노트북 실행 결과와 원본 대조 해석을 HTML 보고서로 정리한 폴더다.

## 구성

| 파일 | 설명 |
|---|---|
| `01_pymupdf_text_extraction_report.html` | PyMuPDF 텍스트 추출 가능 여부와 깨짐 탐지 결과다. |
| `02_vision_paddle_upstage_ocr_comparison_report.html` | Vision, Paddle, Upstage, Claude Vision의 공통 표본 OCR 비교 보고서다. |
| `03_hybrid_verification_pipeline_report.html` | 초기 하이브리드 OCR 검증·라우팅 실험 보고서다. |
| `04_vision_upstage_cross_validation_report.html` | BC·NH 46페이지 Vision·Upstage 대조와 원본 기준 재판정 보고서다. |
| `05_goldset_ocr_structured_evaluation_report.html` | 수기 전체·필드 정답셋 기준 OpenAI Vision·Upstage OCR 및 구조화 평가 보고서다. |
| `06_semantic_critical_fact_ocr_evaluation_report.html` | 10개 카드의 semantic fact 및 Luna·Terra 반복 OCR 비교 보고서다. |
| `07_relational_critical_fact_evaluation_report.html` | 10번 관계형 평가 결과와 11번의 OCR·JSON 수치 오류 발생 단계 귀속을 상세히 정리한다. |
| `08_clean_end_to_end_ocr_benchmark_report.html` | 12번 clean end-to-end OCR·Luna 구조화·critical fact 평가의 최종 단발 실행 보고서다. |
| `09_hierarchical_chunking_retrieval_report.html` | 13번 계층 청킹·retrieval baseline 및 ablation 결과를 정리한 보고서다. |
| `10_search_normalization_morphology_ablation_report.html` | 13번 검색 정규화와 15번 Kiwi 형태소 ablation을 같은 개발 질의 기준으로 비교한 결과 보고서다. |
| `11_normalized_rrf_weight_ablation_report.html` | 16번 Vector:정규화 BM25 RRF 가중치 7개와 reranker 후보군 coverage를 비교한 결과 보고서다. |
| `12_gte_reranker_ablation_report.html` | 17~21번 현재 청킹 reranker·MMR·실제 분류기·BGE 비교를 통합한 평가 보고서다. |
| `13_structural_heading_chunking_ablation_report.html` | 22번 구조 청킹+제목 경로 검색문과 기존 exact 기준선의 개발셋 비교 보고서다. |
| `14_llm_answer_quality_pipeline_comparison_report.html` | 25번에서 OLD·STRUCT 패키지와 Top3·Top5 입력의 LLM 답변 품질을 동일 gold 기준으로 비교한 개발평가 보고서다. |
| `15_human_blind_answer_evaluation_report.html` | 25번의 저장 답변을 사용자가 익명 A/B로 직접 비교하고 배정 해제한 소표본 평가 기록이다. |
| `16_cross_package_holdout_v2_report.html` | 26번 OLD·STRUCT retrieval/evidence 패키지의 cross-package holdout v2가 공통 근거 projection 실패로 무효 종료된 기록이다. |
| `17_integrated_holdout_candidate_evaluation_report.html` | 28번 sealed 30문항에서 OLD·STRUCT의 검색·근거 회수와 LLM 답변 60건을 함께 평가한 결과 보고서다. |
| `EVALUATION_METRICS.md` | OCR·검색·reranker·MMR 평가·진단 지표 사전이다. |

`04`의 자동 통과 판정은 비율·금액·전월 실적 핵심 사실 범위에 한정된다. 전체 OCR 정확도 또는 최종 정답으로 해석하지 않는다.
