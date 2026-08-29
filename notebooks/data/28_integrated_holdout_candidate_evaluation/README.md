# 28 통합 holdout 후보 검색·LLM 평가 완료

OLD와 STRUCT 후보의 검색·BGE·evidence package 및 LLM 60건 평가가 완료됐다. 첫 LLM 실행은 API key 확인 단계에서 요청 0건으로 끝났고, 두 번째 실행에서 60건이 모두 terminal record로 저장됐다. 이후 projection schema를 교정한 CPU scoring recovery는 API 0건으로 완료됐으며 최종 판정은 `holdout_failed`다. 실제 usage는 input 312,974, output 19,833, reasoning 2,585, total 332,807 tokens이고 응답 latency 합은 217.864초다. 현재 공식 단가를 offline에서 확인하지 않아 provider 비용은 미확정이다.

공통 fused 작업목록은 Top50이며, OLD는 section/benefit 순서 필터 후 첫 20개, STRUCT는 Top50의 첫 20개를 사용한다. Query embedding은 승인된 1회/30개/1,726 tokens만 실행했다.

## 검색 결과

- OLD: Card Recall@3 0.942308, Supported-card Recall@3 0.846154, Required Claim Coverage 0.875000, negative 4/4
- STRUCT: Card Recall@3 0.948718, Supported-card Recall@3 0.852564, Required Claim Coverage 0.862179, negative 4/4
- STRUCT 대 OLD paired: win 2 / loss 3 / tie 25, critical retrieval regression O07
- 검색만으로 winner를 정하지 않으며 sealed scoring contract의 LLM answer gate를 적용해야 한다.

## 승인된 LLM 실행 계약

승인 payload core는 `ef74af3eefd2195d6e4b9d5bd33b0f60f0f6629253a3c336bd9929d15ddd0c57`이다. 범위는 `gpt-5.6-terra`, reasoning medium, 60 requests, cl100k input estimate 466,486 tokens, output cap 72,000 tokens, 요청당 1,200 tokens, retries 0, tools 0, store false다. 현재 공식 단가는 offline에서 확인하지 않아 비용은 미확정이다.

응답 실행 guard `8ff81172ca130a5ae388ec180d14eb82f6cfd6ae4b53fdef9511168e5d3233a0`로 60개 요청이 terminal 완료됐다. Scoring guard `dd40c96b0bcc0b7c4be561238436a2fe5037338909568e1992cd74c5bf0a09d8`는 원 응답 guard·ledger·60개 record hash와 수정된 CPU scorer source를 함께 결속한다. 각 record의 payload ID, request SHA, outcome, validation association을 전량 대조한 뒤에만 점수를 계산한다. 외부 runner에는 `skip-llm-execution`을 추가해 API0 scoring 재개에서 실행되지 않게 했다.

Transport validation은 형식·전송 card identity·citation ownership만 검사하며 사실 정답 판정이 아니다. 60개 terminal record가 저장된 뒤 CPU factual scoring이 sealed gold와 projection을 사용해 OLD/STRUCT paired decision을 계산한다.

## Main 실행 명령

```bash
cd /home/sms/openclaw_file/PickCardU
RUN_APPROVED_28_LLM=0 RUN_APPROVED_28_LLM_SCORING=1 APPROVED_28_LLM_EXECUTION_GUARD_SHA256=dd40c96b0bcc0b7c4be561238436a2fe5037338909568e1992cd74c5bf0a09d8 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false conda run -n skn25 python -c "import nbformat; from nbclient import NotebookClient; p='notebooks/28_integrated_holdout_candidate_evaluation.ipynb'; nb=nbformat.read(p,4); NotebookClient(nb,timeout=7200,kernel_name='python3',resources={'metadata':{'path':'.'}},skip_cells_with_tag='skip-llm-execution').execute(); nbformat.write(nb,p)"
```

이 명령은 기존 embedding/retrieval/BGE/post-freeze와 외부 runner를 모두 건너뛰고 validator→guard→CPU scorer만 같은 fresh kernel에서 실행한다. 현재 scoring run의 API/network/GPU/model 호출은 0이다.

## LLM scoring 결과

- OLD: Card Precision@3 0.961538, Card Recall@3 0.923077, Supported-card Recall@3 0.641026, Required Claim Coverage 0.698718, E2E exact 11/30, negative 3/4
- STRUCT: Card Precision@3 1.000000, Card Recall@3 0.929487, Supported-card Recall@3 0.538462, Required Claim Coverage 0.615385, E2E exact 13/30, negative 4/4
- STRUCT 대 OLD E2E paired: win 4 / loss 2 / tie 24
- 판정: `holdout_failed` — 두 후보 모두 사전 absolute/hard gate를 통과하지 못했다. 운영 승격 근거가 아니다.
- 실제 usage: input 312,974, output 19,833, reasoning 2,585, total 332,807 tokens. 응답 wall 합 217.864초. 현재 공식 단가를 offline에서 확인하지 않아 실제 provider 비용은 미확정이다.
