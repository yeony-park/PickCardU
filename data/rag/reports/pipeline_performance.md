# PDF 파싱·RAG 성능 비교 및 전수 실행 결과

- 기준 시각: 2026-08-20 15:23 KST
- 전수 범위: 카드 안내서 106개, 617페이지
- 본문 파서: `gpt-5.6-luna`, reasoning `max`, PyMuPDF 200 DPI PNG
- 구조 검증: Upstage Document Parse, 실제 응답 모델 `document-parse-260630`

## 결론

1. 기존 10문서 표본에서는 원본 PDF를 직접 넣은 API Luna가 품질 1위(`0.8546`)였고, 200 DPI CLI Luna는 가장 느리면서 종합 품질이 가장 낮았습니다(`0.6860`). 두 조건은 입력 방식이 달라 DPI만의 효과를 뜻하지 않습니다.
2. 사용자가 지정한 200 DPI Luna 전수 파싱은 106문서/617페이지 모두 완료됐습니다. Upstage 2차 검증도 같은 범위를 모두 완료했습니다.
3. 전역 검색에서는 weighted hybrid α=0.5가 Recall@5 `0.5462`, MRR@10 `0.4292`, nDCG@10 `0.4811`로 가장 좋았습니다. RRF는 Recall@1 `0.3385`로 가장 높았습니다.
4. 교차검증 결과 205페이지가 표·heading 구조 추가 검증 대상으로 분류됐습니다. PP-Structure v3 원격 검증기는 이 페이지만 대상으로 붙이면 됩니다.

## 기존 파서 성능 비교

아래 값은 `data/ocr_benchmark/reports/openai_surface_comparison.md`의 대표 10문서/50페이지 결과입니다. 품질 점수는 골드가 있는 6문서만 대상으로 합니다.

| 조건 | 성공률 | 초/페이지 | 토큰/페이지 | 텍스트 유사도 | CER↓ | 종합 점수 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| API Luna max/high, 원본 PDF | 98% | 54.90 | 9,678 | 80.90% | 19.68% | 0.8546 |
| CLI Sol medium, 200 DPI | 100% | 17.83 | 5,755 | 64.93% | 36.06% | 0.6947 |
| CLI Sol high, 200 DPI | 100% | 28.63 | 6,298 | 64.75% | 36.35% | 0.6932 |
| CLI Terra medium, 200 DPI | 100% | 16.33 | 5,648 | 64.07% | 36.79% | 0.6901 |
| CLI Terra high, 200 DPI | 100% | 25.13 | 6,003 | 64.13% | 36.87% | 0.6883 |
| CLI Luna max, 200 DPI | 100% | 113.64 | 9,687 | 63.37% | 37.65% | 0.6860 |

BC Biz AirMoney 2페이지의 별도 구조 골드에서는 Upstage가 숫자 exact match `96.3%`, 표 TEDS `0.937`, 표 구조 TEDS-S `1.000`으로 강했습니다. 이 결과는 단일 문서 기준이므로 전수 정확도로 일반화할 수 없습니다.

## 전수 실행 실측

| 단계 | 완료 | 관측 wall time | wall 초/페이지 | 산출물 elapsed 합계/페이지 | 토큰·비용 |
| --- | ---: | ---: | ---: | ---: | --- |
| Luna 200 DPI | 106/106, 617/617 | 3시간 49분 | 22.27 | 109.02초 | 총 7,996,167 tokens, 비용 미노출 |
| Upstage 검증 | 106/106, 617/617 | 약 21분 15초 | 2.07 | 0.974초 | 보수적 추정 $6.40, 정상 1회 기준 $6.17 |

Luna는 input 4,117,205, cached input 171,776, output 3,878,962 tokens를 사용했습니다. 평균은 페이지당 약 12,960 tokens입니다. wall 초/페이지는 병렬 실행 효과를 포함하고, 산출물 elapsed 합계/페이지는 문서별 처리시간을 합한 값입니다.

Upstage는 총 117회 시도를 기록했습니다. 초기에 동시 작업 3개로 실행했을 때 429가 발생해 작업자 1개로 낮췄고, 재시도분을 포함한 보수적 비용은 $6.40입니다. Upstage가 elements에서 생략한 내용 없는 7페이지는 Luna 빈 본문, 렌더 dominant-color 비율, PDF native text/image/drawing 수를 이용해 로컬에서 검증했으며 그 근거를 artifact에 보존했습니다.

## 교차검증과 청킹

| 항목 | 결과 |
| --- | ---: |
| 검증 완료 문서 | 106 |
| 문서 verdict `pass` | 10 |
| 문서 verdict `review_required` | 96 |
| 페이지 `review_required` | 257/617 (41.65%) |
| PP-Structure v3 보류 큐 | 205/617 (33.23%) |
| parent chunks | 2,079 |
| child chunks | 3,070 |
| page-parent fallback children | 269 |
| atomic table children | 586 |

페이지 이슈 수는 `table_count_mismatch` 131, `table_structure_mismatch` 72, `uncertain_primary_text` 76, `numeric_mismatch` 22, `text_similarity_low` 17, `heading_alignment_low` 5입니다. 한 페이지에 여러 이슈가 함께 있을 수 있습니다. Luna 본문을 Upstage 텍스트로 자동 덮어쓰지 않았고, Upstage heading/table/bbox를 구조 source로 사용했습니다.

## 검색 성능

평가셋은 기존 구조화 골드에서 만든 130개 질의이며 6개 문서만 포함합니다.

| 조건 | Recall@1 | Recall@3 | Recall@5 | MRR@10 | nDCG@10 | p50 | p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SQLite FTS5 keyword | 0.1538 | 0.4385 | 0.4846 | 0.3012 | 0.3651 | 1.71ms | 3.20ms |
| OpenAI vector | 0.2462 | 0.3769 | 0.4231 | 0.3305 | 0.3812 | 222.84ms | 258.39ms |
| RRF hybrid | **0.3385** | 0.4615 | 0.5385 | 0.4231 | 0.4754 | 222.88ms | 244.73ms |
| weighted α=0.2 | 0.3000 | 0.4615 | 0.5077 | 0.3927 | 0.4424 | 222.94ms | 246.47ms |
| weighted α=0.5 | 0.3308 | **0.4846** | **0.5462** | **0.4292** | **0.4811** | 222.69ms | 248.90ms |
| weighted α=0.8 | 0.2846 | 0.4308 | 0.4769 | 0.3707 | 0.4164 | 222.42ms | 232.81ms |

child 3,070개의 `text-embedding-3-small` 임베딩은 1536차원으로 100% 완료됐고 802,879 input tokens를 사용했습니다. 평가 질문 130개의 임베딩은 4,817 tokens, batch latency 1,827.685ms였습니다. 표의 latency는 query embedding을 제외한 로컬 검색 시간입니다. 현재 vector 후보 계산은 SQLite에 저장한 모든 vector를 Python으로 순회하므로 keyword보다 약 130배 느립니다. 운영 전에는 pgvector/HNSW 또는 동등한 ANN index로 바꿔야 합니다.

현재 질의는 gold context를 질문으로 변환한 회귀용 seed입니다. 최종 검색기 선택 전에는 정확 키워드, semantic paraphrase, 복합조건, 비교, 보유카드 필터, 답 없음 질의를 수작업으로 추가하고 문서 단위 test split을 고정해야 합니다.

같은 query와 판정식으로 검색 범위만 바꾼 로컬 감사 결과는 다음과 같습니다. `정답 카드 제한`은 제품 성능이 아니라 카드 라우팅이 이미 맞았을 때의 oracle intrinsic 상한입니다.

| keyword 범위 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 전역 106문서 | 0.1538 | 0.4385 | 0.4846 | 0.5615 | 0.3012 | 0.3651 |
| 정답 발급사 제한 | 0.1923 | 0.4846 | 0.5769 | 0.7000 | 0.3565 | 0.4393 |
| 정답 카드 제한, oracle | 0.4308 | 0.7692 | 0.8385 | 0.9231 | 0.6005 | 0.6796 |

카드 필터 없는 smoke query `비즈 에어머니 공항 라운지 연간 이용 횟수`에서는 다른 카드의 라운지 문서가 먼저 검색됐습니다. `card_name=BC_Biz_AirMoney` 필터를 적용하면 정답 근거 `연 2회` parent가 1위였습니다. 현재 질문에 내부 카드 filename이 들어가지만 FTS의 `card_name`은 랭킹 대상이 아니어서 잡음으로 작동합니다. oracle 카드 검색에서 이 접두사를 제거하면 Recall@1이 `0.4308 → 0.7692`, Recall@3이 `0.7692 → 0.8923`으로 올랐습니다.

현재 relevance는 context term 또는 answer term 중 하나만 parent에 있어도 정답으로 인정합니다. answer scalar를 반드시 요구하면 전역 Recall@5는 `0.4846 → 0.4308`, MRR@10은 `0.3012 → 0.2770`으로 내려갑니다. 최종 비교는 수작업 evidence parent ID 또는 정규화된 answer span을 사용하고, 전체 catalog와 보유카드 후보군을 별도 트랙으로 측정해야 합니다.

## 생성 smoke test

weighted hybrid α=0.5, `card_name=BC_Biz_AirMoney`, top-3 parent 조건으로 GPT-5.6 Luna 생성 두 건을 실행했습니다.

| 유형 | 결과 | citation | tokens | 검색 지연 |
| --- | --- | --- | ---: | ---: |
| 근거 있음 | 공항 라운지 `연 2회`, `1일 1회`, 매년 `1월 1일~12월 31일` | BC Biz AirMoney 2페이지 | 1,894 | 35.977ms |
| 근거 없음 | 반려동물 수술비 내용은 안내서에서 확인할 수 없다고 abstain | 없음, `insufficient_evidence=true` | 1,899 | 12.803ms |

첫 근거 있음 응답은 모델이 낸 `S2`를 서버가 실제 document/page/parent/child로 해석했습니다. 첫 답 없음 시도에서는 모델이 `insufficient_evidence=true`와 citation을 동시에 반환해 서버가 fail-closed로 차단했습니다. 프롬프트에 상태별 citation 계약을 명시한 뒤 재실행했고, citation 없는 abstention으로 정상 완료했습니다.

## 재현성 정보

- source corpus SHA-256: `52a171af98c8a3cffb8069afb9744c0f5127c55b17c8a6571ae52d6ad5356657`
- chunk corpus SHA-256: `2d95f92f1cfc5c7e99d688a2e0522b823453496546de3099569ea5fa65b852a1`
- chunk build SHA-256: `8735f43f0f6369dd57af693b1bbfecb349b8427a0ffc34d89010074fd957b891`
- query set SHA-256: `fe4f6d12309980e86473d672590310952fe66a082517b3fc78005e3c792d1598`
- keyword 평가 원본: `data/rag/reports/retrieval_evaluation.json`
- 생성 smoke 원본: `data/rag/reports/generation_smoke.json`

## 남은 작업

- 6개 문서의 gold-context seed 대신 semantic paraphrase, 복합조건, 비교, 답 없음, 보유카드 1/3/5장 hard-negative 질의를 수작업으로 구축합니다.
- 전역 catalog, 보유카드 후보군, catalog fallback을 분리하고 수작업 evidence parent ID로 다시 평가합니다.
- 운영 vector 검색은 pgvector/HNSW 또는 동등한 ANN index로 교체합니다.
- PP-Structure v3 가상 서버를 붙일 경우 canonical artifact에 기록된 205페이지만 검증합니다.
