# 실험 결과 보고서

`docs/report/`는 과거 OCR·검색·구조화 검색 실험 결과의 HTML 정본을 보관합니다. `docs/` 직속에 있던 과거 보고서 복사본은 상대 링크만 다를 뿐 본문이 같아 제거했고, 각 보고서는 이 위치에서 관리합니다.

보고서는 실험 시점의 저장 결과를 설명하며 현재 코드의 성능이나 운영 채택 상태를 자동으로 뜻하지 않습니다. 상세 실행자료·provider 원응답·cache·DB·vector 등은 공개 범위에서 제외된 것이 있어 링크가 열리지 않을 수 있습니다. 보고서가 가리키는 파일이 없으면 과거 실행을 새로 하라는 의미가 아닙니다.

평가 지표의 정의와 해석은 [`../EVALUATION_METRICS.md`](../EVALUATION_METRICS.md), 구조화 검색 실험 보고서의 포함 범위는 [`STRUCTURED_SEARCH_REPORTS.md`](STRUCTURED_SEARCH_REPORTS.md)에서 확인할 수 있습니다.

## 보고서 인덱스

| 파일 | 주제 |
|---|---|
| [`01_pymupdf_text_extraction_report.html`](01_pymupdf_text_extraction_report.html) | PyMuPDF 텍스트 추출 결과 보고서 |
| [`02_vision_paddle_upstage_ocr_comparison_report.html`](02_vision_paddle_upstage_ocr_comparison_report.html) | Vision OCR, PaddleOCR, Upstage, Claude Vision 비교 보고서 |
| [`03_hybrid_verification_pipeline_report.html`](03_hybrid_verification_pipeline_report.html) | 혼합형 OCR 검증 파이프라인 결과 보고서 |
| [`04_vision_upstage_cross_validation_report.html`](04_vision_upstage_cross_validation_report.html) | Vision OCR · Upstage 교차 검증 보고서 |
| [`05_goldset_ocr_structured_evaluation_report.html`](05_goldset_ocr_structured_evaluation_report.html) | 수기 정답셋 기반 OCR·구조화 평가 보고서 |
| [`06_semantic_critical_fact_ocr_evaluation_report.html`](06_semantic_critical_fact_ocr_evaluation_report.html) | 핵심 혜택 사실에 대한 OCR 반복 평가 |
| [`07_relational_critical_fact_evaluation_report.html`](07_relational_critical_fact_evaluation_report.html) | 관계형 Critical Fact 평가 및 수치 오류 귀속 |
| [`08_clean_end_to_end_ocr_benchmark_report.html`](08_clean_end_to_end_ocr_benchmark_report.html) | 동일 조건에서 OCR부터 구조화까지 전체 평가 |
| [`09_hierarchical_chunking_retrieval_report.html`](09_hierarchical_chunking_retrieval_report.html) | 계층 청킹 검색 비교 |
| [`10_search_normalization_morphology_ablation_report.html`](10_search_normalization_morphology_ablation_report.html) | 검색 정규화와 Kiwi 형태소 ablation |
| [`11_normalized_rrf_weight_ablation_report.html`](11_normalized_rrf_weight_ablation_report.html) | 정규화 RRF 가중치 ablation |
| [`12_gte_reranker_ablation_report.html`](12_gte_reranker_ablation_report.html) | 현재 청킹의 Reranker·MMR·질의 분기 종합 평가 |
| [`13_structural_heading_chunking_ablation_report.html`](13_structural_heading_chunking_ablation_report.html) | 구조 청킹과 제목 경로 검색문 ablation |
| [`14_llm_answer_quality_pipeline_comparison_report.html`](14_llm_answer_quality_pipeline_comparison_report.html) | LLM 답변 품질: 기존·구조 패키지와 Top3·Top5 비교 |
| [`15_human_blind_answer_evaluation_report.html`](15_human_blind_answer_evaluation_report.html) | 사용자 익명 A/B 답변 평가 |
| [`16_cross_package_holdout_v2_report.html`](16_cross_package_holdout_v2_report.html) | 26번 cross-package holdout v2: 공통 채점표가 성립하지 않아 무효 종료한 비교 |
| [`17_integrated_holdout_candidate_evaluation_report.html`](17_integrated_holdout_candidate_evaluation_report.html) | 28번 통합 holdout: 검색과 LLM 답변을 같은 30문항으로 평가 |
| [`30_structured_benefit_db_report.html`](30_structured_benefit_db_report.html) | 30. 기존 Upstage JSON → 구조화 검색 DB |
| [`31_operational_chunking_comparison_report.html`](31_operational_chunking_comparison_report.html) | 31. 현재 운영 benefit·parent-child 청킹 |
| [`32_structured_search_comparison_report.html`](32_structured_search_comparison_report.html) | 32. 두 운영 프로필 × 세 검색 방식 |
| [`33_query_routing_report.html`](33_query_routing_report.html) | 33. GPT-6 Luna 조건 추출·경로 선택 |
| [`34_bundle_comparison_report.html`](34_bundle_comparison_report.html) | 34. 청크 묶음 실패와 묶음 정책 비교 |
| [`35_answer_budget_report.html`](35_answer_budget_report.html) | 35. 답변 재시도와 출력 토큰 예산 비교 |
| [`36_profile_search_comparison_report.html`](36_profile_search_comparison_report.html) | 36. 실패 해결 후 두 운영 프로필·세 검색 방식 비교 |
| [`37_operational_guard_audit_report.html`](37_operational_guard_audit_report.html) | 37 운영 답변 검증 방어 범위 |
| [`38_query_condition_audit_report.html`](38_query_condition_audit_report.html) | 38 검색 조건 오류와 복귀 경로 |
| [`39_answer_rubric_report.html`](39_answer_rubric_report.html) | 39 답변 평가 보완과 새 질문 동결 |
| [`40_new_query_comparison_report.html`](40_new_query_comparison_report.html) | 40 새 질문의 검색·청킹 단일 비교 |
| [`41_query_expansion_report.html`](41_query_expansion_report.html) | 41 검색문 보강 후 전체 검색 비교 |
| [`42_expanded_query_evaluation_report.html`](42_expanded_query_evaluation_report.html) | 42 질문 수 확대 확증 비교 |
| [`43_offline_validation_report.html`](43_offline_validation_report.html) | 43 저장 답변 원문 재검토와 조건 라우팅 오프라인 검사 |
| [`45_evidence_supplement_comparison_report.html`](45_evidence_supplement_comparison_report.html) | 45. 카드·순위 고정, 동일 예산 원문 근거 보완 비교 |
| [`46_query_boundary_validation_report.html`](46_query_boundary_validation_report.html) | 46. 질문 조건 추출과 실패 경계 |
| [`47_partial_condition_recovery_report.html`](47_partial_condition_recovery_report.html) | 47. 부분조건을 우선 활용하면 전체 복귀보다 나은가? |
| [`48_structured_retrieval_guidance_report.html`](48_structured_retrieval_guidance_report.html) | 48. 원질문을 보존한 구조화 검색 보조 |
| [`49_selected_pipeline_confirmation_report.html`](49_selected_pipeline_confirmation_report.html) | 49. 새 질문으로 확인한 구조화 검색 후보 |
| [`50_query_contract_audit_report.html`](50_query_contract_audit_report.html) | 50. 구조화 조회 계약과 입력 경계 검사 |
| [`51_query_extraction_scope_report.html`](51_query_extraction_scope_report.html) | 51. 조건 추출 반복과 검색 범위 검사 |
| [`52_corrected_four_retrieval_modes_report.html`](52_corrected_four_retrieval_modes_report.html) | 52. 수정 조회 후 네 검색 방식 비교 |
| [`52_four_retrieval_modes_report.html`](52_four_retrieval_modes_report.html) | 52. 최초 네 검색 방식 비교의 중단 기록 |
| [`53_sequential_fact_supplement_report.html`](53_sequential_fact_supplement_report.html) | 53. 카드 범위만 전달할 때와 구조화 근거를 답변에 추가할 때 |
| [`54_new_question_confirmation_report.html`](54_new_question_confirmation_report.html) | 54. 신규 32질문 × 3회 추출 후, 원문 의미 불일치로 중단 |
| [`55_fact_retrieval_generation_diagnosis_report.html`](55_fact_retrieval_generation_diagnosis_report.html) | 카드는 찾았는데, 필요한 조건도 찾아서 정확히 답했는가? |
| [`56_structured_normalization_report.html`](56_structured_normalization_report.html) | 56. 구조화 검색의 값 정규화와 조건 추출을 분리해서 비교 |
| [`57_sequential_condition_evidence_report.html`](57_sequential_condition_evidence_report.html) | 57. 같은 카드에서 공통 조건을 더 찾으면 필요한 근거가 채워지는가? |
| [`58_generation_factor_comparison_report.html`](58_generation_factor_comparison_report.html) | 조건 근거를 더 주면, 답변 지시를 바꾸면 좋아지는가? |
| [`59_new_sequential_confirmation_report.html`](59_new_sequential_confirmation_report.html) | 새 질문 32개에서 순차 검색을 확인한다 |
| [`60_structured_retrieval_repair_report.html`](60_structured_retrieval_repair_report.html) | DB·조건 추출·매칭·범위를 분리해 확인한다 |
| [`61_retrieval_evidence_comparison_report.html`](61_retrieval_evidence_comparison_report.html) | 61 · 카드 발견과 실제 답변 근거를 나눠 비교 |
| [`62_fixed_prompt_answer_check_report.html`](62_fixed_prompt_answer_check_report.html) | 찾은 카드의 중요한 조건까지 답변에 전달됐는가 |
| [`63_core_evaluation_alignment_report.html`](63_core_evaluation_alignment_report.html) | 63 · 직접 질문 해결과 전체 중요조건을 구분해 다시 평가하기 |
| [`64_scope_boundary_comparison_report.html`](64_scope_boundary_comparison_report.html) | 64 · 가맹점 오인 범위를 교정하고 검색 근거를 비교하기 |
| [`65_fixed_profile_confirmation_report.html`](65_fixed_profile_confirmation_report.html) | 65 · 구조화 검색으로 카드 범위를 줄인 뒤 기존 검색을 한 번 실행하기 |
| [`66_visible_answer_policy_report.html`](66_visible_answer_policy_report.html) | 직접 질문의 정답과 부가 설명의 완전성을 분리했습니다 |
| [`67_structured_retrieval_repair_report.html`](67_structured_retrieval_repair_report.html) | 67. DB 표현·대상 매칭·질문 추출기를 분리해 확인 |
| [`68_structured_evidence_generation_report.html`](68_structured_evidence_generation_report.html) | 68. 같은 검색 결과에서 구조화 사실과 지시문을 비교 |
| [`69_repaired_sequential_confirmation_report.html`](69_repaired_sequential_confirmation_report.html) | 69 · 원문 충돌을 발견해 답변 생성을 차단한 실험 |
| [`70_cap_context_repair_report.html`](70_cap_context_repair_report.html) | 구조화 검색 보완 후, 검색 범위·최종 답변·원문 오류를 분리해 재검증 |
| [`71_pre_generation_evidence_audit_report.html`](71_pre_generation_evidence_audit_report.html) | 검색 입력은 충분했는가, 구조화 근거는 무엇을 보완했는가 |
| [`72_input_presentation_report.html`](72_input_presentation_report.html) | 같은 검색 결과를 LLM에 어떻게 전달할 것인가 |
| [`73_repaired_card_scope_report.html`](73_repaired_card_scope_report.html) | 수정된 구조화 조회 후 카드 범위만 전달하면 어떻게 될까 |
| [`structured_search_integration_report.html`](structured_search_integration_report.html) | 구조화 순차 검색의 worktree 통합·기본 경로 전환 확인 |

## 기존 세부 목록

[`STRUCTURED_SEARCH_REPORTS.md`](STRUCTURED_SEARCH_REPORTS.md)는 30~73 계열 구조화 검색 보고서의 범위와 관련 코드·제외 자료를 설명합니다.

`04_vision_upstage_cross_validation_report.html`의 자동 통과 판정은 명시된 비율·금액·전월 실적 핵심 사실 범위에 한정됩니다. 전체 OCR 정확도나 최종 정답률로 해석하지 않습니다.
