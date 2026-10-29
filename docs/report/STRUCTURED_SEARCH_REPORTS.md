# 구조화 검색 실험 보고서 인덱스

이 문서는 구조화 검색 기능 이식에 포함한 실험 보고서와 코드의 범위를 안내한다. 각 보고서의 질문·입력 버전·평가 기준·한계를 함께 읽는다. 실험별 점수를 같은 조건의 연속 측정으로 간주하지 않는다.

## 포함한 내용

- 결과 보고서 HTML과 노트북 파일에 이미 저장된 출력
- `notebooks/*.ipynb`의 실험 설명과 실행 코드
- `notebooks/scripts/`의 실행·공통 함수·테스트 코드
- `notebooks/notes/`의 계획·방법론

## 별도 보관한 자료

과거 테스트 DB, API 원응답, 벡터, 상세 결과와 실행 snapshot은 이번 코드 병합에 포함하지 않았다. 보고서의 해당 로컬 링크가 열리지 않는 경우가 있으며, 이것이 결과 재실행 또는 DB 재생성 지시는 아니다. 과거 원자료는 structured-search worktree에 보존한다.

코드 회귀 검증은 네트워크 없는 FakeProvider·작은 임시 fixture를 사용한다. 과거 106문서/DB3 전수 대조와 로컬 구조화 DB 무결성 검사는 해당 자료가 없는 checkout에서 명시적으로 건너뛴다. 실제 운영용 Luna DB와 검색 release는 별도 준비·검증한다.

## 보고서 목록

- [기존 Upstage JSON → 구조화 검색 DB](30_structured_benefit_db_report.html)
- [현재 운영 benefit·parent-child 청킹](31_operational_chunking_comparison_report.html)
- [두 운영 프로필 × 세 검색 방식](32_structured_search_comparison_report.html)
- [GPT-6 Luna 조건 추출·경로 선택](33_query_routing_report.html)
- [34 청크 묶음 비교](34_bundle_comparison_report.html)
- [35 답변 출력 예산 비교](35_answer_budget_report.html)
- [36 구조화 검색 후속 비교](36_profile_search_comparison_report.html)
- [운영 답변 검증 방어 범위](37_operational_guard_audit_report.html)
- [검색 조건 오류와 복귀 경로](38_query_condition_audit_report.html)
- [답변 평가 보완과 새 질문 동결](39_answer_rubric_report.html)
- [새 질문의 검색·청킹 단일 비교](40_new_query_comparison_report.html)
- [41 검색문 보강 후 전체 검색 비교](41_query_expansion_report.html)
- [42 질문 수 확대 확증 비교](42_expanded_query_evaluation_report.html)
- [43 노트북 오프라인 검사](43_offline_validation_report.html)
- [45. 고정 카드 원문 근거 보완 비교](45_evidence_supplement_comparison_report.html)
- [46. 조건 추출·오타·실패 경계 검증](46_query_boundary_validation_report.html)
- [47. 부분조건 우선 검색과 전체 복귀 비교](47_partial_condition_recovery_report.html)
- [48. 구조화 단서를 이용한 검색 보조 비교](48_structured_retrieval_guidance_report.html)
- [49. 새 질문으로 확인한 구조화 검색 후보](49_selected_pipeline_confirmation_report.html)
- [50 구조화 조회 계약 검사](50_query_contract_audit_report.html)
- [51 조건 추출 반복과 범위 검사](51_query_extraction_scope_report.html)
- [52 수정 조회 후 네 검색 방식 비교](52_corrected_four_retrieval_modes_report.html)
- [52 최초 비교 중단과 조회 규칙 오류](52_four_retrieval_modes_report.html)
- [53 · 순차 검색의 구조화 근거 추가 비교](53_sequential_fact_supplement_report.html)
- [54 · 새 질문 확인시험의 사전검증 결과](54_new_question_confirmation_report.html)
- [55 · 검색 근거와 답변 반영의 원인 진단](55_fact_retrieval_generation_diagnosis_report.html)
- [56 구조화 검색 값 정규화와 조건 추출 비교](56_structured_normalization_report.html)
- [57 순차 검색의 같은 카드 조건 근거 보완](57_sequential_condition_evidence_report.html)
- [58 · 순차 검색의 조건 근거와 답변 지시 비교](58_generation_factor_comparison_report.html)
- [59 · 새 질문에서 순차 검색과 답변 확인](59_new_sequential_confirmation_report.html)
- [60 · 구조화 조회 원인 분리와 보완](60_structured_retrieval_repair_report.html)
- [61 · 실제 카드의 검색 근거 비교](61_retrieval_evidence_comparison_report.html)
- [62 · 검색 근거와 고정 프롬프트 답변 검증](62_fixed_prompt_answer_check_report.html)
- [63 · 질문 핵심과 중요 설명 평가 정렬](63_core_evaluation_alignment_report.html)
- [64 · 고정 프로필의 카드 범위 교정과 순차 검색](64_scope_boundary_comparison_report.html)
- [65 · 고정 운영 프로필에 구조화 순차 검색 추가](65_fixed_profile_confirmation_report.html)
- [66 · 화면 답변 평가 정책과 기존 결과 재검토](66_visible_answer_policy_report.html)
- [67 · 구조화 DB·조회·질문 추출 비교](67_structured_retrieval_repair_report.html)
- [68 · 구조화 근거 표현과 답변 지시문 비교](68_structured_evidence_generation_report.html)
- [69 · 수정 순차 검색의 답변 생성 전 원문 검사](69_repaired_sequential_confirmation_report.html)
- [70 · 구조화 검색 보완 후 개발 재검증](70_cap_context_repair_report.html)
- [71 · 답변 생성 전 검색·근거 추가 최소 검사](71_pre_generation_evidence_audit_report.html)
- [72 · LLM 근거 전달 형식 비교](72_input_presentation_report.html)
- [73 · 수정 조회 후 카드 범위 순차 검색 비교](73_repaired_card_scope_report.html)
- [구조화 순차 검색 worktree 통합 확인](structured_search_integration_report.html)
