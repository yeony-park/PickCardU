# 10. Relational Critical Fact Evaluation

`10_relational_critical_fact_evaluation.ipynb`의 오프라인 관계형 평가 산출물이다. 기존 09 OCR/구조화 결과를 읽기만 하며 API를 다시 호출하지 않는다.

## 실행 상태

- 유효한 최신 실행: `runs/20260808T082345Z/`
- `latest_run.json`은 위 실행의 `summary.json`을 가리킨다.
- `20260808T080600Z`, `20260808T080808Z`, `20260808T080917Z`, `20260808T082236Z`는 단위 정규화 또는 관계/numeric 분모 분리를 확정하기 전 디버그 실행이다. 추적을 위해 보존하지만 성능 비교에는 사용하지 않는다.

## 산출물

- `label_audit.csv`: 카드별 v2 라벨 수, 중복 제거 수, 보충 안전성 라벨 수
- `atomic_fact_details.csv`: 원자 값별 정답·예측·오류 유형
- `relation_group_details.csv`: 혜택/표 행 단위 전체 일치 여부
- `run_summary.csv`: 개별 실행 지표
- `model_repeatability_summary.csv`: Luna·Terra 2회 평균과 모집단 표준편차
- `error_taxonomy.csv`: 대상·조건·수치·단위·누락 오류 집계
- `summary.json`: 실행 설정, 결과, 한계

정답 v2는 `data/ocr_benchmark/gold/critical_rules/critical_rules_v2.json`에 있으며 기존 v1은 변경하지 않는다.


## 구성

| 항목 | 설명 |
|---|---|
| `latest_run.json` | 현재 폴더의 실행·검증 또는 산출물 자료다. |
| `runs/` | 직속 하위 자료 또는 모듈 폴더다. |
