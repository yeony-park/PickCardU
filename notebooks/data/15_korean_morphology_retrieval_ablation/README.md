# Korean morphology retrieval ablation

이 디렉터리는 기존 개발 질의 30개로 수행한 morphology retrieval ablation 결과다. 독립 holdout은 사용하지 않았고, 이 결과만으로 운영 또는 미관측 데이터 일반화를 결론내릴 수 없다.

## Reproduction

- 환경: `conda run -n skn25`
- 도구: `nbclient 0.10.4`, `NotebookClient(timeout=900, kernel_name="python3")`
- 노트북: `notebooks/15_korean_morphology_retrieval_ablation.ipynb`
- network/API calls: 0; 기존 embedding cache만 사용

## Interpretation

- decision: `do_not_promote_morphology` (`non_promotion_no_positive_primary_delta`)
- evidence RRF MRR@5 delta: `-0.004167` (사전 기준 `>= +0.025`)
- changed queries: `18` / 30
- 설정은 개발셋 후보 비교용이며 형태소 적용의 보편적 우월성을 뜻하지 않는다.


## 구성

| 항목 | 설명 |
|---|---|
| `morphology_ablation_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `morphology_ablation_summary.csv` | 집계 요약 산출물. |
| `morphology_ablation_summary.json` | 집계 요약 산출물. |
| `morphology_changed_queries.csv` | 평가 또는 검증용 표 형식 산출물. |
| `morphology_run_manifest.json` | 실행 무결성·재현 확인 자료. |
