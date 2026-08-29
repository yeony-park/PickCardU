# Notebook Data

로컬 작업본은 노트북 실행에 사용하는 평가셋, 원본 대조 정답, 중간 산출물과 raw 응답을 보관한다. 공개 저장소에는 폴더 안내와 핵심 summary·decision·resources만 포함하며, embedding cache, provider 원응답, 전체 payload·근거 package, pair score와 반복 실행 파일은 제외한다.

## 구성

| 경로 또는 파일 | 설명 |
|---|---|
| `21_current_chunking_prebranch_reranker_evaluation` | 21번 현재 청킹 분기 전 reranker 재평가 자료다. |
| `01_rag_evaluation/` | `01_rag_evaluation.ipynb`의 RAG 검색 평가셋과 라벨링 안내다. |
| `02_pdf_text_layer_check/` | `02_pdf_text_layer_check.ipynb`의 PyMuPDF 추출 텍스트와 초기 OCR 정답 데이터다. |
| `03_ocr_engine_comparison/` | `03_ocr_engine_comparison.ipynb`의 4엔진 OCR 비교 정답, raw 응답, 지표다. |
| `05_hybrid_verification_pipeline/` | `05_hybrid_verification_pipeline.ipynb`의 라우팅·하이브리드 검증 산출물이다. |
| `06_ocr_validation_pipeline/` | `06_ocr_validation_pipeline.ipynb`의 BC·NH 교차검증 원문 캐시·정규화·검토 큐다. |
| `07_goldset_vision_upstage_comparison/` | `07_goldset_vision_upstage_comparison.ipynb`의 수기 정답셋 기반 최신 OCR 실행과 평가 결과다. |
| `08_ocr_model_runtime_comparison/` | `08_ocr_model_runtime_comparison.ipynb`의 API·Codex CLI 모델·실행 경로 비교 원문과 지표다. |
| `09_core_numeric_condition_ocr_evaluation/` | critical fact OCR 평가의 저장 산출물이다. |
| `10_relational_critical_fact_evaluation/` | 관계형 critical fact 평가 산출물이다. |
| `11_numeric_error_attribution/` | 수치 오류 귀속 분석 산출물이다. |
| `12_clean_end_to_end/` | end-to-end OCR benchmark 실행 자료다. |
| `13_hierarchical_chunking_retrieval/` | 계층 청킹·retrieval 산출물이다. |
| `15_korean_morphology_retrieval_ablation/` | 형태소 retrieval ablation 산출물이다. |
| `16_normalized_rrf_weight_ablation/` | RRF 가중치 ablation 산출물이다. |
| `17_gte_reranker_ablation/` | GTE reranker 평가 산출물이다. |
| `18_answer_bearing_grouped_retrieval_reevaluation/` | answer-bearing·leaf-only 후속 진단 산출물이다. |
| `19_mmr_redundancy_ablation/` | MMR 중복성 진단 산출물이다. |
| `20_selective_mmr_gte_gold_type_oracle/` | selective MMR·GTE oracle 산출물이다. |
| `22_structural_heading_chunking_ablation/` | 구조 청킹·제목 경로 검색문 ablation과 독립 검토 산출물이다. |
| `23_structural_chunking_reranker_comparison/` | 새 구조 청킹의 GTE·BGE 재정렬, 후보 상한·자원·판정 산출물이다. |
| `24_multicard_recommendation_retrieval_evaluation/` | closed-corpus 다중정답 카드 추천과 운영형 evidence bundle 후속의 후보·근거·자원·판정 산출물이다. |
| `25_llm_answer_quality_pipeline_comparison/` | OLD·STRUCT와 K3/K5 LLM 답변의 payload·응답·자동 품질 점수·blind packet 감사·자원·판정 산출물이다. |
| `27_integrated_holdout_dataset/` | 검색·LLM 답변 공통 평가용 질문, 정답 카드, 필수 claim과 공개 benchmark 계약을 보관한다. |
| `28_integrated_holdout_candidate_evaluation/` | sealed 30문항의 query embedding·ranking/BGE freeze·검색 지표·LLM 응답 60건·offline scoring recovery·최종 판정 산출물이다. |

raw OCR 응답에는 API 결과가 포함될 수 있다. API 키는 이 폴더에 저장하지 않는다.
