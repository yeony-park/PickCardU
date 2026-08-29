# 23 구조 청킹 GTE/BGE reranker 비교

개발 질의 30개에서 새 구조 청킹의 고정 RRF Top20/Top50 후보를 GTE와 BGE로 다시 정렬한 단일 실행이다. proper/numeric은 RRF를 유지하고 semantic만 reranker를 쓰는 selective 경로도 함께 비교했다.

두 모델 입력은 issuer, card_name, heading_path, body만으로 만든 임시 제목 보강 문자열이다. gold/relevance 필드는 입력에 쓰지 않았다. raw logit만 정렬에 사용했고 RRF 점수와 섞지 않았다.

Hit@3는 상위 3개 안에 관련 결과가 하나라도 있는지, Recall@5는 전체 관련 결과 중 상위 5개가 회수한 비율, MRR@5는 첫 관련 결과가 얼마나 앞에 있는지, nDCG@5는 관련 결과가 앞쪽에 모였는지를 뜻한다. Card Hit/MRR은 같은 계산을 기대 카드 기준으로 한다.

선택 결과: all_bge. 이는 개발셋 비교일 뿐 운영 확정이나 holdout 통과가 아니다. 이전 청킹과의 Recall/nDCG 비교는 corpus와 관련 문서 분모가 달라 진단용이다. GPU0에서 ollama가 함께 실행된 상태였으므로 load/scoring 시간, 처리량, VRAM은 shared_gpu_measurement_non_confirmatory이다. network/API/download/new embedding/Chroma query 비용은 0이다.

## Top10 offline follow-up

원 RRF Top50의 앞 10개만 후보로 제한한 뒤, 저장된 raw logit으로 그 10개 안에서 다시 정렬했다. Top50 rerank 결과를 잘라 쓰지 않았다. GPU·모델·외부 호출은 없었고 모델별 후보 구조 수는 30×10=300개다. Top10 전용 latency/VRAM은 측정하지 않았으므로 시간·메모리 절감률을 주장하지 않는다. 판정은 개발셋 후속 진단이며 운영 승격 근거가 아니다.
