# 실험 노트북 안내

`notebooks/`에는 OCR·검색·구조화 검색·답변 평가의 과거 실험 기록과 노트북 안에 저장된 출력이 있습니다. 번호는 자료를 찾기 위한 식별자이며 그 자체로 현재 실행 순서나 재현 가능성을 뜻하지 않습니다. 노트북별 실험 질문과 기록 범위는 아래에서 확인하고, 보고서가 있는 경우 [결과 보고서 안내](../docs/report/README.md)와 대응 결과를 함께 읽습니다.

## 실험 자료와 제약

- 노트북 **76개**: 기존 OCR·검색 자료 01~29와 구조화 검색 실험 30~73 계열입니다. 이전 목록에서 빠졌던 14·26번 기록, 52·73의 여러 비교 단계도 각기 별도 파일로 남아 있습니다.
- [scripts](scripts/)에는 실행기, 공통 처리, 오프라인 검사와 실험용 테스트가 있습니다. [notes](notes/README.md)에는 계획·방법·진행 기록을 모았습니다. 파일이 존재한다는 것만으로 실행 승인을 뜻하지 않습니다.
- `notebooks/data/`의 과거 DB·원응답·cache·vector 및 대량 상세 결과는 이 브랜치의 신규 게시 범위에서 제외되었습니다. 원격 MS에 이미 있던 일부 README·작은 요약 자료는 남아 있지만 전체 입력과 재실행 환경이 갖춰졌다는 뜻은 아닙니다. 결과 링크 일부는 로컬 보관 자료를 가리킬 수 있습니다.
- 일부 셀과 helper는 Python 패키지, 원문 PDF, 로컬 프로토타입 또는 제공자 API를 필요로 합니다. 실제 실행 전 각 노트북의 안전·비용 안내와 필요한 입력을 확인하세요. 개발·holdout 결과는 운영 품질 보증과 구분합니다.
- 22번 노트북은 원래 로컬 `test_scripts/search_retrieval/pipeline.py` 프로토타입을 참조합니다. 이 파일은 MS에 포함되지 않았고 과거 전체 노트북 실행은 확인하지 않았습니다.
- 운영 검색 코드는 [`feat/rag-engine-integration`](https://github.com/yeony-park/PickCardU/tree/feat/rag-engine-integration)에 있습니다. 이 MS 브랜치는 실험 자료를 보관합니다.

## 노트북 목록

| 파일 | 주제와 목적 |
|---|---|
| [`01_rag_evaluation.ipynb`](01_rag_evaluation.ipynb) | 같은 평가 질의에서 키워드·벡터·하이브리드 검색과 가중치를 비교합니다. |
| [`02_pdf_text_layer_check.ipynb`](02_pdf_text_layer_check.ipynb) | OCR 전에 PyMuPDF로 PDF 내장 텍스트 레이어를 확인하고 선택 표본을 기존 Vision 결과와 대조합니다. |
| [`03_ocr_engine_comparison.ipynb`](03_ocr_engine_comparison.ipynb) | 공통 표본으로 Vision, PaddleOCR, Upstage, Claude Vision의 OCR 결과를 비교합니다. |
| [`04_vision_upstage_validation.ipynb`](04_vision_upstage_validation.ipynb) | 동일 PDF 페이지의 Vision·Upstage 결과를 원본 기준으로 교차 검증합니다. |
| [`05_hybrid_verification_pipeline.ipynb`](05_hybrid_verification_pipeline.ipynb) | 위험도에 따라 두 OCR의 일치 여부와 사람 검토 경로를 다루는 초기 검증 파이프라인을 기록합니다. |
| [`06_ocr_validation_pipeline.ipynb`](06_ocr_validation_pipeline.ipynb) | 저장된 PDF의 OCR, 정규화, 엔진 간 비교, 검토 대기열 흐름을 보여줍니다. |
| [`07_goldset_vision_upstage_comparison.ipynb`](07_goldset_vision_upstage_comparison.ipynb) | 수기 gold 기준으로 Vision·Upstage 원문 OCR과 구조화 필드 평가를 비교합니다. |
| [`08_ocr_model_runtime_comparison.ipynb`](08_ocr_model_runtime_comparison.ipynb) | 같은 gold 표본에서 OpenAI API와 Codex CLI의 Terra·Luna OCR 실행 경로를 비교합니다. |
| [`09_core_numeric_condition_ocr_evaluation.ipynb`](09_core_numeric_condition_ocr_evaluation.ipynb) | 10개 카드·50페이지의 핵심 수치·조건 fact에 대해 OCR 반복 결과를 평가합니다. |
| [`10_relational_critical_fact_evaluation.ipynb`](10_relational_critical_fact_evaluation.ipynb) | critical fact를 원자 관계로 나누고 저장된 OCR·구조화 결과의 관계 평가를 재계산합니다. |
| [`11_numeric_error_attribution.ipynb`](11_numeric_error_attribution.ipynb) | 기존 10번 평가의 수치 오류를 gold, OCR 문맥, JSON과 대조해 발생 단계를 분류합니다. |
| [`12_clean_end_to_end_ocr_benchmark.ipynb`](12_clean_end_to_end_ocr_benchmark.ipynb) | 10개 카드의 OCR부터 정규화·구조화·평가까지 이어지는 실행 계약과 안전 경계를 설명합니다. |
| [`13_hierarchical_chunking_retrieval.ipynb`](13_hierarchical_chunking_retrieval.ipynb) | 검수된 소규모 corpus에서 계층 청킹과 키워드·벡터·RRF 검색을 비교합니다. |
| [`14_retrieval_holdout_contract.ipynb`](14_retrieval_holdout_contract.ipynb) | 기존 검색 holdout의 동결 질문·데이터 계약과 seal 검증 조건을 기록합니다. |
| [`15_korean_morphology_retrieval_ablation.ipynb`](15_korean_morphology_retrieval_ablation.ipynb) | 개발 질의에서 한국어 형태소 분석을 적용한 검색 조건을 비교합니다. |
| [`16_normalized_rrf_weight_ablation.ipynb`](16_normalized_rrf_weight_ablation.ipynb) | 고정 개발 질의에서 정규화 BM25와 벡터 RRF 가중치를 비교합니다. |
| [`17_gte_reranker_ablation.ipynb`](17_gte_reranker_ablation.ipynb) | 저장된 후보 검색 결과를 사용해 GTE reranker 조건을 개발셋에서 비교합니다. |
| [`18_answer_bearing_grouped_retrieval_reevaluation.ipynb`](18_answer_bearing_grouped_retrieval_reevaluation.ipynb) | 저장 결과로 answer-bearing·grouped·leaf-only 검색 진단을 다시 평가합니다. |
| [`19_mmr_redundancy_ablation.ipynb`](19_mmr_redundancy_ablation.ipynb) | 기존 검색 후보와 임베딩을 이용해 MMR 중복 제거 조건을 오프라인 비교합니다. |
| [`20_selective_mmr_gte_gold_type_oracle.ipynb`](20_selective_mmr_gte_gold_type_oracle.ipynb) | gold query type을 사용하는 사후 진단으로 selective MMR·GTE 후보를 비교합니다. |
| [`21_current_chunking_prebranch_reranker_evaluation.ipynb`](21_current_chunking_prebranch_reranker_evaluation.ipynb) | 현재 청킹 기준에서 실제 검색문 분기와 GTE·BGE 후보 재정렬을 개발 평가합니다. |
| [`22_structural_heading_chunking_ablation.ipynb`](22_structural_heading_chunking_ablation.ipynb) | 제목 계층·경로를 반영한 청킹과 검색문을 기존 기준선과 비교하는 개발 사전 검사입니다. |
| [`23_structural_chunking_reranker_comparison.ipynb`](23_structural_chunking_reranker_comparison.ipynb) | 구조 청킹 후보의 GTE·BGE reranker 비교를 위한 CPU 사전 검사를 기록합니다. |
| [`24_multicard_recommendation_retrieval_evaluation.ipynb`](24_multicard_recommendation_retrieval_evaluation.ipynb) | 고정 corpus의 다중정답 카드 추천을 위한 개발 사전 검사와 근거 회수 조건을 정리합니다. |
| [`25_llm_answer_quality_pipeline_comparison.ipynb`](25_llm_answer_quality_pipeline_comparison.ipynb) | OLD·STRUCT 검색 패키지와 입력 깊이별 답변 비교 조건을 동결하는 사전 검사입니다. |
| [`26_cross_package_holdout_evaluation.ipynb`](26_cross_package_holdout_evaluation.ipynb) | v2 cross-package holdout의 분리·봉인 계약을 기록하며 v1 결과와 구분합니다. |
| [`27_integrated_holdout_dataset.ipynb`](27_integrated_holdout_dataset.ipynb) | holdout 레코드 자체를 읽지 않고 source lineage companion과 제외 demo claim을 구성합니다. |
| [`28_integrated_holdout_candidate_evaluation.ipynb`](28_integrated_holdout_candidate_evaluation.ipynb) | 통합 holdout 후보의 실행 단계와 query-only embedding 범위를 사전 봉인합니다. |
| [`29_export_search_runtime_snapshot.ipynb`](29_export_search_runtime_snapshot.ipynb) | 앱 소유 검색 runtime snapshot을 fingerprint와 임시 복사본을 통해 내보냅니다. |
| [`30_structured_benefit_db.ipynb`](30_structured_benefit_db.ipynb) | 기존 Upstage JSON으로 만든 구조화 DB의 적재·출처·보존 결과를 확인합니다. |
| [`31_operational_chunking_comparison.ipynb`](31_operational_chunking_comparison.ipynb) | 기존 운영 benefit·parent-child 청킹과 새 검색 인덱스의 결과를 비교합니다. |
| [`32_structured_search_comparison.ipynb`](32_structured_search_comparison.ipynb) | 두 운영 프로필과 세 검색 방식의 검색·답변 비교를 정리합니다. |
| [`33_query_routing.ipynb`](33_query_routing.ipynb) | 질문에서 구조화 조건을 추출하고 검색 경로를 고르는 실험을 기록합니다. |
| [`34_bundle_comparison.ipynb`](34_bundle_comparison.ipynb) | 기존 청크 묶음 정책의 실패와 대안 묶음 비교를 정리합니다. |
| [`35_answer_budget.ipynb`](35_answer_budget.ipynb) | 답변 실패 시 재시도와 출력 토큰 예산 조건을 비교합니다. |
| [`36_profile_search_comparison.ipynb`](36_profile_search_comparison.ipynb) | 기본 검색 조건과 확인 불가 후보를 두 프로필에서 비교합니다. |
| [`37_operational_guard_audit.ipynb`](37_operational_guard_audit.ipynb) | 운영 답변 검증 guard가 다루는 범위와 저장된 실행 단계를 점검합니다. |
| [`38_query_condition_audit.ipynb`](38_query_condition_audit.ipynb) | 검색 조건 오류 유형과 원래 범위를 지키는 복귀 경로를 점검합니다. |
| [`39_answer_rubric.ipynb`](39_answer_rubric.ipynb) | 답변 평가 기준을 보완하고 새 평가 질문 계약을 동결합니다. |
| [`40_new_query_comparison.ipynb`](40_new_query_comparison.ipynb) | 새 개발 질의에서 검색·청킹 후보를 한 번 비교합니다. |
| [`41_query_expansion.ipynb`](41_query_expansion.ipynb) | 검색어 보강 전후의 전체 검색 조건과 결과를 비교합니다. |
| [`42_expanded_query_evaluation.ipynb`](42_expanded_query_evaluation.ipynb) | 확대된 새 질문 집합에서 검색 조건을 비교합니다. |
| [`43_offline_validation.ipynb`](43_offline_validation.ipynb) | 저장 답변의 원문과 조건 라우팅을 API 없이 사후 점검합니다. |
| [`44_structured_search_db_spec.ipynb`](44_structured_search_db_spec.ipynb) | 운영 코드·DB를 바꾸지 않고 구조화 SQLite 스키마와 저장값을 읽기 전용 확인합니다. |
| [`45_evidence_supplement_comparison.ipynb`](45_evidence_supplement_comparison.ipynb) | 카드와 순위를 고정하고 구조화 fact에 연결된 원문 근거 보완 조건을 비교합니다. |
| [`46_query_boundary_validation.ipynb`](46_query_boundary_validation.ipynb) | 조건 추출 실패·빈 AND·명시 카드 범위 보존의 경계를 점검합니다. |
| [`47_partial_condition_recovery.ipynb`](47_partial_condition_recovery.ipynb) | 부분 조건을 사용하는 경로와 전체 비구조화 검색 복귀를 비교합니다. |
| [`48_structured_retrieval_guidance.ipynb`](48_structured_retrieval_guidance.ipynb) | 원질문 검색에 구조화 fact 보조어를 더하는 후보를 비교합니다. |
| [`49_selected_pipeline_confirmation.ipynb`](49_selected_pipeline_confirmation.ipynb) | 선택된 검색 후보를 새 질문과 반복으로 개발 확인합니다. |
| [`50_query_contract_audit.ipynb`](50_query_contract_audit.ipynb) | 구조화 조회 입력·대상 일치·재정렬 범위의 계약을 검사합니다. |
| [`51_query_extraction_scope.ipynb`](51_query_extraction_scope.ipynb) | 질문 조건 추출 반복성과 발급사·카드 범위 식별을 확인합니다. |
| [`52_corrected_four_retrieval_modes.ipynb`](52_corrected_four_retrieval_modes.ipynb) | 예외 처리 오류를 별도 수정한 조회 규칙으로 네 검색 방식을 다시 비교합니다. |
| [`52_corrected_four_retrieval_modes_evaluation.ipynb`](52_corrected_four_retrieval_modes_evaluation.ipynb) | 수정 검색 비교에서 저장된 결과와 원문 판정을 평가합니다. |
| [`52_four_retrieval_modes.ipynb`](52_four_retrieval_modes.ipynb) | 구조화 단독·비구조화 단독·순차·병렬 방식의 첫 비교 기록입니다. |
| [`53_sequential_fact_supplement.ipynb`](53_sequential_fact_supplement.ipynb) | 순차 검색 결과에 같은 카드의 구조화 fact를 답변 입력에 추가하는 조건을 비교합니다. |
| [`54_new_question_confirmation.ipynb`](54_new_question_confirmation.ipynb) | 동결한 검색 설정을 새 질문 집합으로 확인하려다 사전검증에서 멈춘 기록입니다. |
| [`55_fact_retrieval_generation_diagnosis.ipynb`](55_fact_retrieval_generation_diagnosis.ipynb) | 필수 fact를 원문·DB·검색 결과·LLM 입력·답변 단계에 연결해 사후 진단합니다. |
| [`56_structured_normalization.ipynb`](56_structured_normalization.ipynb) | 같은 추출 계획에서 구조화 DB 정규화와 조건 추출의 영향을 분리합니다. |
| [`57_sequential_condition_evidence.ipynb`](57_sequential_condition_evidence.ipynb) | 같은 카드 안의 공통 조건 원문을 보완할 때 근거가 달라지는지 비교합니다. |
| [`58_generation_factor_comparison.ipynb`](58_generation_factor_comparison.ipynb) | 검색 근거 보완과 답변 지시문 변경 효과를 교차 비교합니다. |
| [`59_new_sequential_confirmation.ipynb`](59_new_sequential_confirmation.ipynb) | 새 질문에서 선택된 순차 검색 후보를 확인하는 실행 계획을 기록합니다. |
| [`60_structured_retrieval_repair.ipynb`](60_structured_retrieval_repair.ipynb) | DB·조건 추출·대상 매칭·검색 범위 오류를 나누어 보완합니다. |
| [`61_retrieval_evidence_comparison.ipynb`](61_retrieval_evidence_comparison.ipynb) | 카드 검색 결과와 답변 근거의 연결을 프로필별로 비교합니다. |
| [`62_fixed_prompt_answer_check.ipynb`](62_fixed_prompt_answer_check.ipynb) | 고정 프롬프트에서 검색된 카드의 중요 조건이 답변에 전달되는지 확인합니다. |
| [`63_core_evaluation_alignment.ipynb`](63_core_evaluation_alignment.ipynb) | 저장 결과를 질문 핵심 해결과 전체 중요 조건 평가로 나누어 재채점합니다. |
| [`64_scope_boundary_comparison.ipynb`](64_scope_boundary_comparison.ipynb) | 발급사·상품명 경계 수정이 카드 범위와 검색 근거에 미치는 영향을 비교합니다. |
| [`65_fixed_profile_confirmation.ipynb`](65_fixed_profile_confirmation.ipynb) | 고정 프로필에 구조화 카드 선별을 추가한 개발 후속 확인입니다. |
| [`66_visible_answer_policy.ipynb`](66_visible_answer_policy.ipynb) | 저장 답변을 직접 요구 해결·화면 사실·입력 지원·추가 설명 관점에서 재검토합니다. |
| [`67_structured_retrieval_repair.ipynb`](67_structured_retrieval_repair.ipynb) | 대상 표현 매칭과 질문 추출기를 분리해 구조화 조회 조건을 비교합니다. |
| [`68_structured_evidence_generation.ipynb`](68_structured_evidence_generation.ipynb) | 같은 검색 결과에서 구조화 fact 표현과 답변 지시문의 영향을 비교합니다. |
| [`69_repaired_sequential_confirmation.ipynb`](69_repaired_sequential_confirmation.ipynb) | 새 표현의 순차 검색·답변 후보를 확인하되 생성 전 원문 gate를 유지합니다. |
| [`70_cap_context_repair.ipynb`](70_cap_context_repair.ipynb) | 이용 금액 문맥 보완 후 검색 범위·답변·원문 오류를 분리해 개발 재검증합니다. |
| [`71_pre_generation_evidence_audit.ipynb`](71_pre_generation_evidence_audit.ipynb) | 검색 입력의 충분성과 구조화 근거 보완 범위를 답변 생성 전에 점검합니다. |
| [`72_input_presentation.ipynb`](72_input_presentation.ipynb) | 같은 검색 결과를 LLM에 전달하는 표현 방식을 비교합니다. |
| [`73_repaired_card_scope.ipynb`](73_repaired_card_scope.ipynb) | 수정 조회 이후 카드 목록만 전달하는 순차 검색을 비교합니다. |
| [`73_repaired_card_scope_evaluation.ipynb`](73_repaired_card_scope_evaluation.ipynb) | 저장된 73번 결과와 정정된 검색 지표·수동 답변 판정을 다시 결속합니다. |

## 실행기·보조 코드

- 루트의 `09_semantic_repeatability_runner.py`는 09 OCR 반복 실행용이고, `12_clean_end_to_end_runner.py`와 `12_clean_end_to_end_evaluator.py`는 12번 OCR 실행·오프라인 평가용입니다.
- [`scripts/`](scripts/) 아래에는 30~73번 실험에서 재사용하는 runner, 입력·결과 무결성 검사, 보고서용 분석 helper와 테스트가 있습니다. 실제 입력·선행조건은 대응 노트북과 `notes/`의 계획을 기준으로 확인하세요.
