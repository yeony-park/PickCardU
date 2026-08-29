# Normalized RRF weight ablation

이 디렉터리는 기존 개발 질의 30개로 Vector:Normalized BM25 RRF 가중치를 비교한 결과다. 독립 holdout은 사용하지 않았으며 운영 또는 미관측 데이터 일반화를 주장하지 않는다.

## Experiment phases

- Initial predeclared weights: `0.4:0.6`, `0.5:0.5`, `0.6:0.4`, `0.7:0.3`, `0.8:0.2`
- Initial automatic decision: `retain_baseline_0.5_0.5` / `vector_0.5_bm25_0.5`
- Adaptive exploratory follow-up requested after observing the `0.4:0.6` result: `0.3:0.7`, `0.2:0.8`
- Follow-up reference decision: `no_exploratory_candidate_for_confirmatory_retest`
- Extended best observed by the fixed comparison order: `vector_0.3_bm25_0.7`
- The adaptive follow-up does not retroactively alter the initial automatic decision.

## Reproduction

- 환경: `conda run -n skn25`
- 실행: `nbclient 0.10.4`, `NotebookClient(timeout=900, kernel_name="python3")`
- 노트북: `notebooks/16_normalized_rrf_weight_ablation.ipynb`
- network/API/new embeddings/package installs: 0
- Chroma integrity: 15번 실행 후 frozen tree hash를 시작 전에 검증하고, 동일 file-hash map에서 tree digest를 재구성하며 snapshot 전후와 마지막 저장 후 원본 불변성을 확인한다.
- Fresh-run comparison: 이전 7개 성능·candidate raw hash와 현재 fresh-run hash를 파일별 exact 비교하고, depth-50 차이가 있으면 summary에 원인과 새 결과를 기록한다.


## 구성

| 항목 | 설명 |
|---|---|
| `rrf_weight_ablation_per_query.csv` | 질의·사실별 상세 평가 산출물. |
| `rrf_weight_ablation_summary.csv` | 집계 요약 산출물. |
| `rrf_weight_ablation_summary.json` | 집계 요약 산출물. |
| `rrf_weight_candidate_recall.csv` | 검색 후보 목록 또는 후보 coverage 산출물. |
| `rrf_weight_candidates.csv` | 검색 후보 목록 또는 후보 coverage 산출물. |
| `rrf_weight_run_manifest.json` | 실행 무결성·재현 확인 자료. |
| `rrf_weight_union_coverage.csv` | 평가 또는 검증용 표 형식 산출물. |
