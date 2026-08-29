# Notebooks

실험, 평가, 검증 파이프라인을 실행하는 Jupyter 노트북 폴더다. 모든 노트북은 `conda run -n skn25 ...` 환경에서 실행한다.

## 실험 구분

- **Current:** `12_clean_end_to_end_ocr_benchmark.ipynb`와 `data/12_clean_end_to_end/current/`. 새 end-to-end 실행만 기록한다.
- **Baseline:** 09~11의 검증 결과와 기존 Upstage 산출물. 12번에서는 복사하지 않고 상대경로와 SHA-256으로 참조한다.
- **Historical:** 01~08의 기존 실험. 현재 위치를 유지하며 12번 데이터 폴더로 이동하지 않는다.

## 구성

| 파일 | 설명 |
|---|---|
| `21_current_chunking_prebranch_reranker_evaluation` | 21번 현재 청킹 분기 전 reranker 재평가 자료다. |
| `09_semantic_repeatability_runner.py` | 09번 API 반복 OCR 실행과 재현성 측정을 수행하는 runner. |
| `09_semantic_repeatability_runner.py` | 09번 API 반복 OCR 실행과 재현성 측정을 수행하는 runner. |
| `01_rag_evaluation.ipynb` | vector, keyword, hybrid 검색과 가중치를 정량 평가한다. |
| `02_pdf_text_layer_check.ipynb` | PyMuPDF 텍스트 레이어·추출 품질을 점검하고 Vision OCR과 비교한다. |
| `03_ocr_engine_comparison.ipynb` | Vision OCR, PaddleOCR, Upstage, Claude Vision을 공통 정답셋으로 비교한다. |
| `04_vision_upstage_validation.ipynb` | Vision과 Upstage의 초기 검증 실험을 기록한다. |
| `05_hybrid_verification_pipeline.ipynb` | OCR 결과 라우팅·검증 파이프라인의 초기 실험을 기록한다. |
| `06_ocr_validation_pipeline.ipynb` | PDF 로드 → OCR → 원문 저장 → 정규화 → 엔진 간 사실 비교 → 검토 큐를 수행한다. cron 수집은 포함하지 않는다. |
| `07_goldset_vision_upstage_comparison.ipynb` | 수기 전체·필드 정답셋을 기준으로 OpenAI Vision과 Upstage OCR, 공통 구조화 JSON을 평가한다. |
| `08_ocr_model_runtime_comparison.ipynb` | 07 기준선을 참조해 OpenAI API Terra·Luna와 Codex CLI Terra·Luna의 OCR·필드 평가를 비교한다. |
| `09_core_numeric_condition_ocr_evaluation.ipynb` | 10개 카드의 critical fact를 기준으로 Upstage와 API Luna·Terra의 수치·문자·단위 정확도 및 반복성을 평가한다. |
| `10_relational_critical_fact_evaluation.ipynb` | critical fact v2를 원자 관계로 구성하고 기존 09 결과의 대상·조건·수치·표 행 관계 및 위험 오답을 오프라인 재평가한다. |
| `11_numeric_error_attribution.ipynb` | 10번 평가의 수치 오답을 gold→OCR 문맥→JSON 순서로 대조해 OCR·구조화·표 의미 규칙 단계로 귀속한다. |
| `12_clean_end_to_end_ocr_benchmark.ipynb` | 10개 카드·50페이지의 OCR→동일 strict 구조화→오프라인 평가 계약과 안전한 실행 절차를 정의한다. |
| `12_clean_end_to_end_runner.py` | 기본 dry-run, 명시적 stage/live guard, fingerprint cache와 run lock을 지원하는 재실행 가능한 OCR·구조화 runner다. |
| `12_clean_end_to_end_evaluator.py` | coverage-aware TXT·structured·critical v2 지표를 JSON/CSV로 쓰는 오프라인 evaluator다. |
| `13_hierarchical_chunking_retrieval.ipynb` | 승인된 10개 gold TXT·structured fixture를 계층적으로 청킹하고 OpenAI embedding·로컬 Chroma 기반 keyword/vector/RRF hybrid 검색을 비교한다. |
| `15_korean_morphology_retrieval_ablation.ipynb` | Kiwi 형태소 기반 retrieval ablation을 기록한다. |
| `16_normalized_rrf_weight_ablation.ipynb` | normalized BM25·vector RRF 가중치와 후보군을 비교한다. |
| `17_gte_reranker_ablation.ipynb` | GTE reranker 후보 재정렬을 평가한다. |
| `18_answer_bearing_grouped_retrieval_reevaluation.ipynb` | answer-bearing·grouped·leaf-only 후속 진단을 기록한다. |
| `19_mmr_redundancy_ablation.ipynb` | 현재 청킹의 MMR 중복성 ablation을 기록한다. |
| `20_selective_mmr_gte_gold_type_oracle.ipynb` | selective MMR·GTE gold-type oracle 진단을 기록한다. |
| `22_structural_heading_chunking_ablation.ipynb` | 구조 청킹과 제목 경로 검색문을 기존 exact 기준선과 비교한다. |
| `23_structural_chunking_reranker_comparison.ipynb` | 새 구조 청킹에서 GTE·BGE 재정렬과 Top20/Top50 후보를 비교한다. |
| `24_multicard_recommendation_retrieval_evaluation.ipynb` | 10개 문서 안의 다중정답 카드 추천·근거 회수와 운영형 evidence bundle 후속을 개발평가한다. |
| `25_llm_answer_quality_pipeline_comparison.ipynb` | OLD·STRUCT 검색·근거 패키지와 Top3·Top5 입력의 strict JSON LLM 답변 품질을 개발 질의로 비교한다. |
| `27_integrated_holdout_dataset.ipynb` | 검색과 LLM 답변을 같은 질문·정답 구조로 평가하는 공개 benchmark용 통합 데이터셋을 구성한다. |
| `28_integrated_holdout_candidate_evaluation.ipynb` | sealed 30문항으로 OLD·STRUCT의 검색·근거 package와 LLM 답변을 같은 scoring 계약으로 평가한다. |
| `data/` | 노트북 번호·이름별로 평가셋, 정답 데이터, raw OCR 응답, 지표, 검증 결과를 보관한다. |

API를 호출하는 노트북은 프로젝트 루트의 `.env`에 있는 키와 `RUN_*`, `TARGET_ISSUERS` 설정을 확인한 뒤 실행한다.
