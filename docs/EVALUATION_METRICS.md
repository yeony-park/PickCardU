# PickCardU 평가·진단 지표 사전

PickCardU의 OCR, 구조화 추출, 검색, reranker, MMR, 실행 자원 평가에 쓰인 용어와 계약을 한곳에 모은 사전이다. 값은 각 보고서·CSV·JSON의 저장 산출물을 우선하며, 이 문서는 지표의 뜻과 해석 범위를 정의한다. 별도 표기가 없으면 모든 비율은 해당 평가 질의·페이지·fact의 평균이며, 단일 실행은 반복 평균이나 표준편차가 아니다.

## 목차

1. [읽는 법과 구분](#읽는-법과-구분)
2. [텍스트·OCR 지표](#텍스트-ocr-지표)
3. [구조화·표·사실 평가](#구조화-표-사실-평가)
4. [검색과 후보군 지표](#검색과-후보군-지표)
5. [평가 view와 비교 통계](#평가-view와-비교-통계)
6. [MMR 중복성 진단](#mmr-중복성-진단)
7. [의사결정 규칙과 사람 검수](#의사결정-규칙과-사람-검수)
8. [품질·비용·시간 함께 해석하기](#품질비용시간-함께-해석하기)
9. [실행 자원·재현성](#실행-자원-재현성)
10. [지표가 아닌 parameter](#지표가-아닌-parameter)
11. [역사적 alias와 N/A 계약](#역사적-alias와-na-계약)

## 읽는 법과 구분

| 구분 | 뜻 | 예 |
|---|---|---|
| **Metric** | 결과 품질이나 자원 사용을 수치화한 값 | MRR@5, CER, VRAM peak |
| **Parameter** | 실험 방법을 정하는 입력값; 높고 낮음 자체가 품질은 아님 | MMR λ, BM25 k1/b, RRF weight |
| **Evaluation view** | 같은 Top5를 어떤 relevance 계약으로 채점할지 | strict raw, answer-bearing |
| **Comparison statistic** | 동일 질의의 두 방식 차이를 요약 | paired delta, W/L/T |
| **Decision rule** | 여러 지표를 묶어 채택 여부를 정하는 조건 | guardrail, gate, auto-pass |
| **Diagnostic** | 원인·구조를 설명하는 보조 관찰 | cosine, containment, changed query |

공통 카드 예시는 “A카드가 편의점에서 전월실적 30만 원 이상이면 월 1만 원 할인”이다. `1만 원`만 맞아도 대상·조건이 바뀌면 사용자 안내에는 위험할 수 있으므로, 텍스트·수치·관계·검색 지표를 함께 본다.

## 텍스트·OCR 지표

| 지표 | 한글 설명·비교/분모 | 높고 낮음의 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| CER | 정규화한 OCR 문자열을 gold 문자열로 바꾸는 문자 삽입·삭제·교체 수를 gold 문자 수로 나눈다. 페이지 또는 전체 텍스트가 분모다. | **낮을수록 좋다.** `전월실적`을 `전윈실적`으로 읽으면 교체 오류다. | 줄바꿈·읽기 순서·표 직렬화에도 민감하고, 숫자 의미 관계를 보장하지 않는다. | OCR benchmark, 08번 OCR 보고서 계열 |
| WER | 정규화 뒤 단어 토큰의 삽입·삭제·교체 수를 gold 단어 수로 나눈다. | **낮을수록 좋다.** `30만 원 이상`에서 `이상` 누락은 단어 삭제다. | 한국어 띄어쓰기·토큰화 규칙에 따라 값이 달라진다. | OCR 텍스트 비교 산출물 |
| word_sequence_similarity | gold·예측의 tokenizer 적용 뒤 단어열을 `difflib.SequenceMatcher.ratio()`로 비교한 보조 점수다. 페이지 단어열이 비교 단위다. | **높을수록 좋다.** 표의 조건→혜택 순서가 보존되면 높다. | SequenceMatcher는 의미가 아닌 순서열 유사도이며, 토큰 존재만 맞아도 표 구조를 완전히 보장하지 않는다. | OCR 비교 산출물에서의 보조 진단 |
| 토큰 Precision / Recall / F1 | gold·예측 정규화 토큰의 교집합을 본다. Precision 분모는 예측 토큰, Recall 분모는 gold 토큰, F1은 조화평균이다. | **높을수록 좋다.** `해외 가맹점 2% 할인` 토큰을 빠뜨리지 않으면 Recall이 오른다. | `숙박 2% 할인`처럼 대상이 달라도 숫자·공통 토큰 때문에 높을 수 있다. | OCR benchmark, 자동 통과 후보 진단 |
| 전체 TXT exact match | 페이지의 정규화 전체 문자열과 gold TXT가 완전히 같은지 본다. 페이지 수가 분모다. | **높을수록 좋다.** 한 글자·행 순서 차이도 실패다. | 지나치게 엄격하여 단독 운영 기준으로 부적절하다. | OCR 텍스트 상한 점검 |
| 수치 F1 | 금액·비율·횟수·기간 토큰만 골라 Precision/Recall/F1을 계산한다. | **높을수록 좋다.** `30만 원`, `2%`, `월 3회`를 보존하면 높다. | `편의점 1만 원`을 `숙박 1만 원`으로 바꿔도 통과할 수 있다. | OCR benchmark, auto-pass 후보 |
| numeric normalized exact | 숫자를 쉼표·공백·표현 차이 등 계약된 규칙으로 정규화한 뒤 gold 수치와 완전 비교한다. 평가 가능 numeric leaf가 분모다. | **높을수록 좋다.** `10,000원`과 `10000원`의 계약상 동치 여부를 일관되게 본다. | 정규화 규칙 밖의 동의 표현이나 단위 오류는 별도 지표가 필요하다. | structured fact / relation 산출물 |
| numeric surface exact | 원문 표면형 숫자 문자열을 gold와 그대로 비교한다. | **높을수록 좋다.** `2%`를 `20%`로 읽으면 실패한다. | 표현만 달라도 실패하므로 normalized exact와 같은 값이 아니다. | OCR·구조화 숫자 감사 |
| unit exact | 수치와 연결된 통화·퍼센트·회·개월 등 단위가 gold와 같은지 본다. 단위가 있는 평가 가능 수치가 분모다. | **높을수록 좋다.** `5,000원`을 `%`로 해석하면 실패다. | gold에 단위가 없으면 N/A이며, 동의어 정규화 범위는 계약에 의존한다. | semantic fact, structured relation |

## 구조화·표·사실 평가

| 지표 | 한글 설명·비교/분모 | 높고 낮음의 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| 값 존재율 | 요구된 필드 ID에 값 또는 `null` 키가 반환됐는지 센다. 요구 필드 수가 분모다. | **높을수록 좋다.** 연회비·할인율 키가 모두 있으면 높다. | `null`·오답도 존재로 셀 수 있어 정확도는 아니다. | 초기 structured extraction |
| field_set_exact_match (과거) | 초기 **page-level** 평가에서 해당 페이지의 예측 field ID 집합이 gold field ID 집합과 정확히 같은지 본다. 값·`null` 내용은 비교하지 않으며 페이지 수가 분모다. | **높을수록 좋다.** 한 페이지의 할인율·한도·조건 ID가 추가·누락 없이 gold와 같아야 통과다. | 이후의 “structured field exact” 값 정확도와 이름만 비슷할 뿐 다른 지표다. | 초기 page-level field-set 계약; historical alias |
| structured field exact | 이후 구조화 평가에서 gold 필드의 **값**과 예측 값을 계약된 정규화 뒤 완전 비교한다. 평가 가능 필드가 분모다. | **높을수록 좋다.** 월 한도 `10,000원`을 `20,000원`으로 내면 실패다. | 배열·객체는 일부만 틀려도 실패할 수 있다. | structured field 평가, OCR 보고서 계열 |
| critical field exact | `critical=true`로 지정한 structured field만의 exact 비율이다. | **높을수록 좋다.** 전월실적 조건·할인 한도 같은 핵심 값이 맞아야 한다. | critical 지정 자체가 평가 범위를 제한한다. | critical fact gate |
| fact exact | 대상·조건·수치·단위를 묶은 한 semantic fact가 모두 맞는 비율이다. fact 수가 분모다. | **높을수록 좋다.** 편의점/30만 원/1만 원이 모두 맞아야 통과한다. | 엄격해서 어떤 leaf가 실패했는지 단독으로 설명하지 못한다. | semantic fact 평가 |
| numeric leaf exact | fact 안 금액·비율·횟수·기간 등 숫자 leaf의 exact 비율이다. | **높을수록 좋다.** 월 한도 `1만 원→2만 원`은 실패다. | 대상 업종이 달라도 숫자만 맞으면 통과한다. | semantic fact·관계 감사 |
| text leaf exact | 업종·조건 문구·날짜 등 문자열 leaf를 계약된 정규화로 비교한다. | **높을수록 좋다.** `해외`를 `국내`로 바꾸면 실패다. | 숫자·단위 오류를 설명하지 않는다. | semantic fact·관계 감사 |
| null prediction rate | gold가 있는 fact에서 전체 `null`을 반환한 비율이다. gold fact 수가 분모다. | **낮을수록 좋다.** 원문에 30만 원 조건이 있는데 `null`이면 실패다. | 그럴듯한 오답은 잡지 못한다. | semantic fact 평가 |
| atomic relation exact | 혜택 ID→대상·조건→값→원본 경로 관계 안의 원자 값이 같은지 본다. 평가 가능 원자 관계가 분모다. | **높을수록 좋다.** `tiers[0]`의 3,000원을 다른 행 7,000원과 바꾸면 실패다. | 필드 ID·경로가 주어진 계약이라 자유 발견 능력과 다르다. | 10번 관계형 Critical Fact v2 |
| relation group exact | 한 혜택 또는 중첩 객체에 속한 평가 가능 원자 값이 모두 맞는지 본다. 그룹 수가 분모다. | **높을수록 좋다.** 대상·조건·할인율 중 하나라도 틀리면 해외 할인 그룹은 실패다. | 큰 그룹일수록 실패 확률이 높다. | 10번 관계형 평가 |
| structured-array table-row relation exact | 구조화 배열의 한 행에서 조건과 결과가 함께 맞는지 본다. 배열 행 수가 분모다. | **높을수록 좋다.** 30만~50만 원→3,000원 행의 값이 다른 tier와 바뀌면 실패다. | PDF 좌표·원본 표 셀의 관계를 직접 검증하지 않는다. | 10번 structured-array row 평가 |
| source-located table-row relation exact | 원본 페이지·표 위치에 연결된 행에서 조건과 값 관계가 맞는지 본다. source-located 행이 분모다. | **높을수록 좋다.** 같은 문서에 두 금액이 있어도 올바른 조건 행에 있지 않으면 실패다. | 구조화 배열 행과 별도 계약이며, 위치 추출 품질에 의존한다. | 12번 source-located table-row 관계 평가 |
| table header exact | 표 열 제목·라벨이 gold와 같은지 본다. 평가 대상 header가 분모다. | **높을수록 좋다.** `전월 이용금액`을 `월 할인한도`로 읽으면 실패다. | 값이 맞아도 header만으로 행 관계를 보장하지 않는다. | OCR 표 구조 감사 |
| table row exact | 표의 한 행을 header·조건·결과 계약으로 완전 비교한다. | **높을수록 좋다.** `30만 원 이상 / 1만 원` 행이 통째로 맞아야 한다. | 줄 병합·읽기 순서가 불안정하면 과도하게 실패할 수 있다. | OCR 표 구조 감사 |
| condition exact | 전월실적, 기간, 제외 조건처럼 자격 조건만 gold와 비교한다. 조건 leaf/관계가 분모다. | **높을수록 좋다.** `30만 원 이상`을 `50만 원 이상`으로 읽으면 실패다. | 혜택 금액·대상 오류는 따로 봐야 한다. | critical fact·table relation |
| unsafe mismatch rate | 누락이 아니라 존재하는 값을 틀리게 단정한 fact/원자 관계 비율이다. 평가 가능 항목이 분모다. | **낮을수록 좋다.** 정답 1만 원을 2만 원으로 반환하면 위험 오답이다. | 누락은 분자에 넣지 않으므로 missing rate와 함께 본다. | Critical Fact v2 |
| missing prediction rate | 요청된 평가 가능 필드·하위 경로가 아예 없는 비율이다. | **낮을수록 좋다.** 연회비 객체의 `partner_krw` 키가 없으면 실패다. | 값이 있으나 틀린 경우는 잡지 못한다. | Critical Fact v2 |
| prediction-supported label coverage | 안전성 라벨 중 현 예측 스키마가 실제로 요청하여 공정하게 채점할 수 있는 비율이다. | **높을수록 좋다.** 할인 대상·조건·한도가 모두 스키마에 있으면 coverage가 높다. | 지원은 정확도를 뜻하지 않는다. | Critical Fact v2 |
| error taxonomy | 실패를 수치·대상·조건·제외·단위·누락·미지원 등으로 분류한 진단 분포다. | 특정 오류 비중이 높으면 수정 우선순위가 된다. 예: `30만→50만`은 조건 오류다. | 한 오류가 여러 의미를 훼손해도 대표 분류 하나만 기록될 수 있다. | OCR·structured audit |

## 검색과 후보군 지표

strict relevance의 기본 계약은 `expected_card` + `expected_level` + 모든 `required_terms`를 만족한 청크다. 초기 `Hit@3`은 카드 라우팅 지표였으므로 이 사전에서는 **Card Hit@3**로 표준화해 부른다.

| 지표 | 한글 설명·비교/분모 | 높고 낮음의 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| Card Hit@3 | expected card와 Top3 청크 card metadata를 비교한다. 해당 query group 전체가 분모다. | **높을수록 좋다.** The Orange 질의의 Top3에 현대카드 청크가 있으면 통과다. | 같은 카드의 무관한 청크도 통과하므로 strict evidence 성공이 아니다. | 13번 초기 retrieval의 `hit_at_3` alias, 13번 이후 ablation |
| Card MRR@5 | expected card가 처음 나타난 Top5 순위의 역수 평균이다. 카드 질의 또는 지정 query group 수가 분모다. | **높을수록 좋다.** 현대카드가 1위면 1, 2위면 0.5다. | 같은 카드의 무관한 청크도 관련으로 보며, 답 문장 순위는 말하지 않는다. | 22번 구조 청킹 보고서 |
| Strict Hit@3 | strict relevant chunk가 Top3에 하나 이상 있는 질의 비율이다. strict relevant가 있는 질의가 분모다. | **높을수록 좋다.** 음식점·60%를 담은 expected benefit 청크가 Top3에 있어야 한다. | level·용어 계약이 엄격해 답을 담은 부모 청크도 실패할 수 있다. | 13번 이후 retrieval/reranker/MMR |
| Answer-bearing Hit@3 | expected card와 모든 required terms를 가진 청크가 Top3에 하나 이상 있는 질의 비율이다. evidence 질의 수가 분모이며 level은 무시한다. | **높을수록 좋다.** 같은 카드의 연회비 `200,000원` 문장이 Top3에 있으면 통과다. | lexical proxy라 완전한 답·조건 충족과 같지 않고, parent 청크도 통과할 수 있다. | 18번 이후 answer-bearing view, 22번 |
| Supported-card Hit@3 | expected card가 Top3에 있고, 동결한 normalized evidence span 하나 이상이 그 카드의 actual final payload body/evidence에 실제로 있는 evidence 질의 비율이다. evidence 질의 수가 분모다. | **높을수록 좋다.** `편의점 10%`와 해당 카드가 final payload body로 확인되면 통과다. | 카드 적중만 보는 Card Hit@3보다 엄격하며, span이 corpus 구조에 공통으로 projection되지 않으면 점수 자체를 산출하면 안 된다. | 26번 cross-package holdout v2; **정의됨** |
| Evidence Span Coverage@3 | Top3 payload가 회수한 고유 normalized frozen evidence span의 질의별 비율을 평균한 값이다. 해당 질의의 frozen span 수가 질의별 분모다. | **높을수록 좋다.** `10% 할인`과 `전월 30만원` 두 span 중 둘 다 payload body에 있으면 1이다. | 제목 보강이나 parent/child 분할 때문에 실제 정보가 있어도 body-only exact span으로 대응하지 않을 수 있다. 두 corpus에 같은 span이 투사되는지 먼저 검증해야 한다. | 26번 cross-package holdout v2; **정의됨** |
| cross-corpus evidence projection validity | 동결 span의 모든 단위가 비교하려는 두 corpus의 actual body/evidence에 모두 exact projection되는지 보는 사전 유효성 gate다. 전체 frozen span unit 수가 분모다. | **30/30처럼 전부 통과해야** 공통 scorecard로 Card Hit·Supported-card·Coverage를 비교할 수 있다. | 이 값은 검색 성능이 아니라 채점표의 공정성 검사다. 한쪽 구조에서 실패하면 winner·성능 지표를 만들지 않고 invalid로 종료한다. | 26번 cross-package holdout v2; **정의됨** |
| Recall@5 | 해당 view의 relevant chunk 중 Top5가 회수한 비율이다. 질의별 relevant unit 수가 분모다. | **높을수록 좋다.** 관련 청크 2개 중 1개를 찾으면 0.5다. | 후보 밖 근거와 5위 이후는 반영하지 않는다. | retrieval, reranker, MMR |
| MRR@5 | Top5 안 첫 relevant unit의 역순위 평균이다. 관련 단위가 Top5 밖이면 0이다. | **높을수록 좋다.** 정답 benefit이 2위면 0.5다. | 첫 정답만 보므로 다수 근거 회수는 Recall/nDCG가 보완한다. | retrieval, reranker, oracle, MMR |
| nDCG@5 | Top5 binary relevance의 할인 gain을 이상 순위로 정규화한다. 질의별 relevant unit이 기준이다. | **높을수록 좋다.** 관련 혜택이 1위일수록 높다. | 부분적으로 유용한 청크와 완전 근거를 같은 binary로 볼 수 있다. | retrieval, reranker, MMR |
| candidate hit | reranker 입력 후보 TopK 안에 strict 또는 answer 관련 단위가 하나라도 있는 질의 비율이다. | **높을수록 좋다.** Top20에 연회비 benefit이 하나 있으면 hit다. | 후보 안 순위는 보지 않으며 reranker는 후보 밖을 복구하지 못한다. | 16·17·18 candidate ceiling |
| candidate recall | 후보 TopK가 해당 view의 관련 단위를 얼마나 담는지 보는 Recall이다. | **높을수록 좋다.** strict gold 4개 중 Top50에 3개면 0.75다. | reranker 성능이 아니라 후보 상한이다. | 16·17·18·19 후보 진단 |
| candidate ceiling | 고정 후보군에 이미 존재하는 hit/recall의 상한 진단이다. | **높을수록 좋다.** ceiling이 낮으면 재정렬만으로 성공할 수 없다. | 실제 reranker가 그 상한을 달성한다는 뜻은 아니다. | RRF weight·GTE·leaf-only 보고서 |
| union coverage | 여러 retriever 또는 weight 후보의 합집합이 relevant unit을 포함하는 정도다. | **높을수록 좋다.** 한 retriever가 놓친 연회비 근거를 다른 retriever가 포함하면 상승한다. | 합집합은 단일 운영 후보·순위를 뜻하지 않으며 비용이 늘 수 있다. | 16번 candidate/RRF 분석 |
| expected-card share@5 | Top5 중 expected card 청크의 비중이다. Top5 청크 수가 분모다. | **높을수록 좋다.** A카드 질의 Top5 중 A카드가 4개면 0.8이다. | card diversity나 strict relevance와 동일하지 않다. | 19번 MMR 및 20번 비교 gate |
| unique card count | Top5에 등장한 서로 다른 card key 수의 질의 평균이다. | 목적에 따라 다르다. 카드 라우팅은 낮은 off-card와 함께 해석한다. | 다양성이 높아도 관련성이 높다는 뜻은 아니다. | 19번 redundancy summary 이후 |
| off-card count | Top5에서 expected card가 아닌 청크 수의 질의 평균이다. | **낮을수록 보통 좋다.** A카드 질의에 B카드가 많으면 증가한다. | 여러 카드를 비교하는 질의에서는 단순 적용이 어렵다. | 19번 redundancy summary 이후 |
| card_diversity_at_5 | Top5의 고유 카드 수를 별도 집계한 진단 이름이다. | 사용 목적에 따라 해석한다. 중복 억제 진단에는 고유 카드 수가 참고가 된다. | 초기 retrieval/16~18 일부 CSV에는 별도 컬럼이 **미저장(N/A)** 이다. 19번 이후에는 `unique_card_count`, `expected_card_count`, `off_card_count`가 저장돼 같은 질문을 범위 내에서 감사할 수 있다. | 초기 미저장 범위와 19번 이후 redundancy 범위를 구분 |

### 다중정답 추천형 검색 지표

아래 지표는 시장 전체 추천이 아니라, 정해진 문서 corpus에서 한 질의에 여러 정답 카드가 있을 수 있는 closed-corpus 평가에 쓴다. 카드 라우팅·단일 답 청크 지표와 분모가 다르므로 직접 섞어 비교하지 않는다.

| 지표 | 한글 설명·비교/분모 | 높고 낮음의 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| Card Precision@3 / @5 | Top3 또는 Top5에 반환한 카드 중 gold positive card의 비율이다. 반환 카드 슬롯 수가 분모다. | **높을수록 좋다.** `배달앱 할인` 질의 Top5 중 3장이 gold 카드면 Precision@5=.6이다. | gold 밖의 실제 좋은 카드나 개인 자격·발급 가능성은 반영하지 않는다. | 24번 다중정답 추천형 개발평가 |
| Card Recall@3 / @5 | gold positive card 중 Top3 또는 Top5에 회수한 비율이다. 질의별 gold positive card 수가 분모다. | **높을수록 좋다.** 정답 카드 3장 중 2장을 Top5에 내면 Recall@5=2/3이다. | 앞쪽 한 장의 정확한 근거는 보지 않으므로 Precision·Evidence Accuracy와 함께 본다. | 24번 다중정답 추천형 개발평가 |
| Evidence-supported Card Recall@5 | gold card를 Top5에 냈고, 대표 청크 하나가 hard predicate까지 증명한 카드의 회수율이다. 질의별 gold positive card 수가 분모다. | **높을수록 좋다.** `배달앱 10%` 카드가 Top5에 있고 같은 청크가 대상·할인 조건을 모두 담으면 기여한다. | 부모·자식 청크에 조건이 나뉘면 사실상 유용해도 단일 대표 청크 계약에서 실패할 수 있다. | 24번 다중정답 추천형 개발평가 |
| Bundle-supported Card Recall@5 | gold card를 Top5에 냈고, 자동으로 만든 같은 카드 evidence bundle 전체가 hard predicate를 증명한 카드의 회수율이다. 질의별 gold positive card 수가 분모다. | **높을수록 좋다.** 배달앱 할인 대상 청크와 할인율 청크가 같은 카드 bundle에 함께 있으면 기여한다. | bundle이 답을 생성·검증한 결과는 아니며, 허용한 관계·token cap에 따라 값이 달라진다. | 24번 Follow-up 1 operational bundle |
| card evidence bundle / token·chunk cap | 카드별로 Top20 seed와 허용된 같은 카드 1-hop 구조 관계에서 evidence를 최대 청크 수·token 수까지 합친 scorer 입력이다. 이 실험은 최대 5청크·4,096 tokens가 cap이며 bundle 수가 아니라 query-card 후보가 관측 단위다. | 여러 직접 본문이 조건을 나눠 담은 경우 대표 근거를 보완한다. 예: `배달앱` 제목과 `10% 할인` 본문을 같은 카드 bundle로 읽는다. | root fanout·재귀 descendant·cross-card·gold 선택은 금지되어 있으며, cap을 넘는 근거는 포함하지 못한다. | 24번 Follow-up 1 operational bundle 계약 |
| single representative 지원 지표 | 카드별 대표 청크 1개가 hard predicate를 증명하는지를 기준으로 한 Evidence-supported Card Recall·Evidence Accuracy다. gold positive 카드 또는 반환 슬롯이 각각 분모다. | **높을수록 좋다.** 한 청크 안에 대상·할인·조건이 모두 있으면 지원 성공이다. | 여러 청크를 합친 bundle-supported와 분모가 비슷해도 입력 단위가 달라 값·W/L/T를 직접 같은 원인으로 해석할 수 없다. | 24번 원래 single-chunk 평가 및 Follow-up 1 비교 |
| Evidence Accuracy@3 / @5 | Top3 또는 Top5의 반환 슬롯 중 카드와 대표 근거가 모두 맞는 비율이다. 빈 슬롯도 실패로 세므로 K개 슬롯이 분모다. | **높을수록 좋다.** Top5 중 카드·근거 모두 맞는 슬롯이 2개면 .4다. | 다중 근거를 합쳐 답하는 생성 단계와 같지 않고, 단일 청크 증명 계약에 민감하다. | 24번 다중정답 추천형 개발평가 |
| Candidate Card Recall | reranker 전 후보 TopK에 gold positive card가 존재하는 비율이다. 질의별 gold positive card 수가 분모다. | **높을수록 좋다.** 정답 3장 중 2장이 Top20 후보에 있으면 2/3이다. | 후보 상한일 뿐 reranker가 그 카드를 Top5로 올린다는 뜻은 아니다. | 24번 candidate ceiling |
| Candidate Evidence Recall | reranker 전 후보 TopK에 hard predicate를 증명하는 evidence가 있는 gold card의 비율이다. 질의별 gold positive card 수가 분모다. | **높을수록 좋다.** 카드 후보는 있어도 할인 조건 청크가 없으면 이 값에는 기여하지 않는다. | corpus 청크 구조와 단일 청크 evidence 계약에 따라 낮아질 수 있다. | 24번 candidate ceiling |
| Bundle-reachable Evidence Recall@20 | Top20 후보와 고정된 같은 카드 1-hop bundle 규칙으로 hard predicate를 증명할 수 있는 gold card의 비율이다. 질의별 gold positive card 수가 분모다. | **높을수록 좋다.** 대상 청크와 할인율 청크가 Top20 안에서 허용된 bundle 관계로 연결되면 기여한다. | 후보 밖 카드·근거는 reranker나 bundle이 복구할 수 없고, relation rule·token cap에 따라 값이 달라진다. | 24번 Follow-up 2 generalization 평가 |
| Exact Pair Accuracy | 질의에서 뽑은 두 predicate pair가 gold pair와 순서와 내용까지 정확히 같은 문장의 비율이다. 전체 평가 문장 수가 분모다. | **높을수록 좋다.** ‘쿠팡 할인과 전기요금 혜택’이 Q02·Q05로 정확히 나뉘면 성공이다. | pair 정답은 미리 정한 taxonomy에 의존하며, 표현이 달라지거나 조건이 셋이면 같은 방식으로 충분하지 않을 수 있다. | 24번 Follow-up 4 parser blind-style 평가 |
| Two-predicate Output Coverage | parser가 고정 실패 코드가 아니라 서로 다른 두 predicate를 실제 출력한 문장의 비율이다. 전체 평가 문장 수가 분모다. historical alias는 `coverage_rate`/`coverage_count`다. | **높을수록 좋다.** 검색에 넘길 수 있는 두 조건이 모두 있어야 한다. | 출력했다는 사실만으로 정답 pair라는 뜻은 아니므로 Exact Pair Accuracy와 함께 본다. | 24번 Follow-up 4 parser blind-style 평가 |
| Wrong-route | parser가 실패로 멈추지 않고 잘못된 두 predicate pair를 출력한 문장 수 또는 비율이다. 전체 평가 문장 수가 분모다. | **낮을수록 좋다.** 0이면 확신하지 못한 문장을 엉뚱한 검색 경로로 보내지 않았다. | 낮다고 coverage가 높은 것은 아니다. 모두 멈추면 Wrong-route는 0이어도 실용성이 없을 수 있다. | 24번 Follow-up 4 parser blind-style 평가 |
| Fail-closed | parser가 애매하거나 조건이 부족한 입력에 pair 대신 고정 실패 코드를 반환해 downstream 검색을 차단한 문장 수 또는 비율이다. 전체 평가 문장 수가 분모다. | 안전 목적에서는 잘못된 pair보다 낫지만, **낮을수록** 더 많은 문장을 실제 처리한다. | 너무 높으면 표현 coverage 부족을 뜻하며, 실패를 곧 정답 처리로 바꾸면 안 된다. | 24번 Follow-up 4 parser blind-style 평가 |
| predicate micro/macro F1 | predicate label의 정밀도·재현율 조화평균이다. micro는 모든 label 판단을 합쳐, macro는 문장별 F1을 평균해 계산한다. | **높을수록 좋다.** 여러 카드 혜택 조건을 전체적으로 맞히는지와 문장별 균형을 함께 본다. | pair가 정확한지·실제 검색에 쓸 수 있는지는 직접 보장하지 않으므로 Exact Pair Accuracy·Coverage와 분리한다. | 24번 Follow-up 4 parser blind-style 평가 |
| zero-hit | 특정 질의에서 TopK에 gold card가 하나도 없는 상태 또는 그 질의 비율이다. 질의 수가 분모다. evidence-supported zero-hit은 근거까지 갖춘 gold card가 없는 경우다. | **낮을수록 좋다.** 카드 3장을 추천해야 하는 질의에서 정답 카드가 전혀 없으면 zero-hit이다. | zero가 아니어도 Precision·Recall·근거 품질이 충분하다는 뜻은 아니다. | 24번 추천형 guardrail |
| catastrophic loss | 기준 대비 한 질의의 핵심 추천·근거 성능이 크게 무너진 사례 수다. 이 계약에서는 supported recall이 1에서 .5로 떨어진 Q03 같은 질의를 센다. 질의 수가 분모이며 보통 count로 보고한다. | **낮을수록 좋고 0이 guardrail**이다. | ‘크게’의 기준은 사전에 고정해야 하며 다른 프로젝트 임계값과 직접 비교할 수 없다. | 24번 depth gate |
| card collapse | 중복 청크·동일 카드가 상위를 차지해 여러 정답 카드가 밀리는 현상 또는 그 진단 수치다. 예: raw duplicate count Top5는 Top5 중 중복 raw 청크 수를 질의 평균으로 본다. | 보통 **낮을수록** 카드 폭과 다양성에 유리하다. | 중복 감소가 추천 정확도·근거 충족을 자동으로 높이지는 않는다. | 24번 duplicate/card 다양성 진단 |

### LLM 답변 품질 지표

아래 지표는 검색 순위가 아니라, 고정된 카드 근거 그룹을 입력으로 받은 LLM 답변을 answer gold와 비교한다. 단일 응답 실행의 자동 scorer 또는 익명 packet 감사 결과이므로 사람의 최종 사실 검증과 동일하지 않다.

| 지표 | 한글 설명·비교/분모 | 높고 낮음의 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| quality composite | 카드 선택, 인용, grounding, required fact, 수치·조건, 완결성·오류 항목을 저장 계약의 가중 방식으로 합친 답변 품질 요약 점수다. 구성별 질문 수가 분모다. | **높을수록 좋다.** 카드명·혜택·조건·인용이 함께 맞는 답이 높아진다. | 가중치·gold projection에 의존하므로 단일 하위 지표나 사람 판단을 대체하지 않는다. | 25번 LLM 답변 품질 비교 |
| Answer Card F1 | 답변이 낸 카드 집합과 gold positive 카드 집합의 정밀도·재현율 조화평균이다. 질문별 카드 집합이 관측 단위다. | **높을수록 좋다.** 정답 카드 2장 중 2장을 내고 오답 카드가 없으면 1이다. | 카드가 맞아도 혜택·수치·조건 설명이 맞는지는 보지 않는다. | 25번 |
| Citation Validity | 카드·주장에 붙인 evidence_id가 제공된 카드 근거 그룹에서 유효하고 소유권이 맞는 비율이다. 인용이 필요한 주장 수가 분모다. | **높을수록 좋다.** A카드 할인 설명에 실제 A카드 evidence_id를 인용하면 기여한다. | 유효한 인용이 곧 해당 주장의 사실 정확성·완결성을 보장하지 않는다. | 25번 |
| Grounded Claim Precision | 답변의 주장 중 제공 evidence가 직접 뒷받침하는 주장 비율이다. 답변 주장 수가 분모다. | **높을수록 좋다.** 근거에 없는 발급 자격·시장 최적성 주장을 하지 않으면 높다. | 필요한 사실을 빠뜨리는 문제는 별도로 Required Fact Recall을 봐야 한다. | 25번 |
| Required Fact Recall | 질문·gold가 요구한 카드·혜택·수치·조건 사실 중 답변이 회수한 비율이다. 질문별 required fact 수가 분모다. | **높을수록 좋다.** `0.2%`와 전월 실적 조건을 모두 언급해야 기여한다. | gold가 질문 밖 주변 사실을 강제하거나 alias를 투영하면 자동 scorer와 실제 유용성이 어긋날 수 있다. | 25번 |
| Numeric Value/Unit/Condition Exact | 수치 주장 중 값·단위·적용 조건이 모두 정확한 비율이다. 수치 사실 평가 단위가 분모다. | **높을수록 좋다.** `0.2% 적립`과 해당 조건을 함께 맞혀야 성공이다. | 값만 맞고 조건을 생략하면 실패하며, 수치가 없는 답변에는 적용 범위가 제한된다. | 25번 |
| Complete Answer | 필요한 카드·필수 사실·인용·불충분 근거 처리를 계약대로 갖춘 완결 답변의 비율이다. 전체 질문 수가 분모다. | **높을수록 좋다.** 여러 조건 질의에서 필요한 카드와 근거를 빠뜨리지 않은 답이 성공이다. | strict schema 완료나 transport 완료와 같은 뜻이 아니다. | 25번 |
| Critical Error Rate | 저장된 severity 계약에서 critical로 분류된 답변 오류 비율이다. 전체 질문 또는 답변 수가 분모다. | **낮을수록 좋다.** 근거 없이 중요한 혜택을 단정하는 답을 줄여야 한다. | 오류 severity와 분모 계약에 따라 major/minor와 직접 합산할 수 없다. | 25번 |
| Blind packet win/loss/tie | 구성 키를 숨긴 두 답변 packet을 reviewer가 비교해 더 나음/나쁨/동점으로 센 결과다. packet pair 수가 분모다. | 승이 많고 심각 오류가 적으면 보조 신호다. | single reviewer·packet-only 판단은 human audit이나 독립 반복 평가가 아니다. | 25번 custom Codex reviewer follow-up |
| Supported-card Recall@3 (LLM) | positive 문항에서 답변이 낸 gold 카드 중, 해당 카드와 답변의 필수 claim이 sealed evidence component로 함께 지원된 비율이다. positive gold 카드가 분모다. | **높을수록 좋다.** A카드와 ‘편의점 10%, 전월 30만 원’이 둘 다 근거에 연결돼야 기여한다. | 카드명이 맞아도 claim lexical/numeric match 또는 citation projection이 실패하면 낮아질 수 있다. | 28번 integrated holdout; **정의됨** |
| Required Claim Coverage (LLM) | positive 문항의 gold required claim 중 답변이 sealed component에 근거를 두고 회수한 비율이다. required claim 수가 분모다. | **높을수록 좋다.** 할인율뿐 아니라 전월 실적 조건도 답해야 1에 가까워진다. | deterministic lexical/numeric scorer는 의미가 같은 바꿔말하기를 놓칠 수 있다. | 28번 integrated holdout; **정의됨** |
| E2E exact (integrated holdout) | 카드·필수 claim·근거 지원·오류·negative 처리의 sealed 계약을 한 문항에서 모두 만족한 비율 또는 성공 수다. 전체 holdout 문항 수가 분모다. | **높을수록 좋다.** A카드와 할인·조건을 근거 있게 답하고, 정답 없음 질문에는 추천하지 않아야 성공이다. | 하나라도 놓치면 0이라 원인별 품질은 Card/Claim/negative 지표를 함께 봐야 한다. | 28번 integrated holdout; **정의됨** |
| negative correctness (integrated holdout) | 정답 카드가 없는 문항에서 답변이 카드를 추천하지 않고 계약된 insufficient-evidence 처리를 한 비율 또는 성공 수다. negative 문항 수가 분모다. | **높을수록 좋다.** 근거가 없을 때 임의 카드 3장을 추천하지 않으면 성공이다. | corpus 밖에 실제 적합 카드가 있는지 또는 사용자 개인 자격은 평가하지 않는다. | 28번 integrated holdout; **정의됨** |
| transport semantic validation pass | 응답 형식, 전송된 card identity, citation ownership, answer-state만 validator가 통과시킨 비율이다. 생성 응답 수가 분모다. | **높을수록 전송·형식 계약에 맞는다.** A카드 claim에 전달된 A카드 evidence ID만 쓰면 기여한다. | 사실 정답이나 claim 완결성은 보장하지 않으며, offline factual scoring과 혼동하면 안 된다. | 28번 LLM transport audit; **정의됨** |

## 평가 view와 비교 통계

| view / 통계 | 한글 설명·비교/분모 | 높고 낮음의 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| strict raw | card+level+required terms를 모두 만족한 원본 청크를 relevant로 본 view다. | **높을수록 좋다.** expected benefit과 `60%`를 모두 담은 benefit 청크가 앞에 와야 한다. | 계층 중복 때문에 답을 담은 page/section도 strict 실패가 될 수 있다. | 13번 이후 기본 retrieval 평가 |
| answer-bearing raw | expected card와 모든 required terms를 만족하면 level을 무시해 관련으로 보는 view다. | **높을수록 좋다.** 동일 카드 page가 답 용어를 담으면 관련으로 본다. | 사실적으로 완전한 답과 동일하지 않은 lexical proxy다. | 18번 Follow-up 이후 |
| exact-group / exact dedup | 같은 card와 정규화 document가 정확히 같은 청크를 한 그룹으로 묶어 평가한다. | **높을수록 좋다.** 같은 문서가 page·section에 중복돼도 한 그룹으로 센다. | 표현이 약간 다른 중복은 묶지 못한다. | 18번 answer/strict exact view |
| gold-family oracle | answer-bearing 정답들을 gold family로 묶는 평가용 상한 view다. | **높을수록 좋다.** 같은 답 가족 청크가 앞에 오면 통과다. | 운영 dedup이나 실제 답변 평가는 아니다. | 18번 이후 oracle view |
| leaf-only | card/page를 제외하고 section/benefit 후보만 두는 진단 view/pool이다. | parent 제거 뒤 benefit 근거가 앞서는지를 본다. | fixed20과 available은 후보 수가 달라 절대 성능 비교가 불가하다. | 18번 Follow-up 2 |
| paired delta | 같은 query의 두 방식 지표 차이를 평균한 비교 통계다. | 양수면 기준보다 평균적으로 좋다. 예: MRR +0.02. | 질의 평균 차이여서 분포·반복 변동을 대신하지 않는다. | retrieval/reranker/MMR ablation |
| W/L/T | 같은 query에서 새 방식이 기준보다 win/loss/tie인 횟수다. 해당 질의 수가 분모다. | win이 많고 loss가 적을수록 유리하다. | tie tolerance와 선택 지표에 따라 달라지고 크기는 말하지 않는다. | paired CSV, oracle/MMR 비교 |
| changed query | 두 방식 Top5·지표·관련 상태가 실제로 달라진 질의 목록이다. | 개선·하락 사례를 원인 감사에 연결한다. | 목록 자체는 전체 평균이나 인과를 증명하지 않는다. | 18번 leaf-only, reranker 감사 |
| ranking freeze | relevance/gold를 불러오기 전에 각 구성의 TopK ranked IDs와 점수를 저장하는 순서 보장 기록이다. query × configuration 행이 분모다. | gold-before-freeze가 false이고 필요한 행이 모두 있으면 평가 순서 오염 위험을 낮춘다. | 순위가 공정하게 계산됐다는 뜻은 아니며, relevance 계약 자체의 타당성은 별도 검토해야 한다. | 22번 구조 청킹 |
| heading-only contribution | relevant 청크에서 required term 중 body에는 없고 heading path에만 있는 term의 존재 여부·건수다. 관련 청크가 분모다. | 제목 경로가 실제 검색문에 더한 문맥을 감사한다. 예: `운영경비`가 제목에만 있어 하나카드 가맹점 목록을 구분한다. | heading만으로 답을 정당화할 수 없고 body가 self-contained answer인지 별도 확인해야 한다. | 22번 구조 청킹 |

## MMR 중복성 진단

| 진단 | 한글 설명·비교/분모 | 높고 낮음의 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| positive pairwise cosine mean | Top5 청크 쌍 중 양의 cosine 유사도의 평균이다. 청크 쌍이 분모다. | **낮을수록 중복이 적을 가능성.** 같은 할인 문장을 반복한 page/section 쌍이 많으면 높다. | cosine은 의미 중복의 대리값이지 정답·사실 중복의 증명은 아니다. | 19번 MMR redundancy, 20번 numeric MMR 진단 |
| exact duplicate excess | 같은 카드 안에서 정규화 body가 정확히 같은 group마다 첫 청크를 뺀 초과 청크 수다. 보고서 계약에 따라 전체 chunk set 또는 Top5에 집계할 수 있으므로 분모·범위를 함께 적는다. | **낮을수록 좋다.** page와 benefit에 같은 연회비 문장이 반복되면 증가한다. | exact 동일 문서만 세므로 유사 표현 중복은 놓친다. | 19번 이후, 22번 전체 청크 감사 |
| cross-level containment | 같은 카드의 다른 level 문서 중 정규화 문자열이 서로 포함되는 쌍 수 평균이다. | **낮을수록 계층 중복이 적다.** page가 benefit 문장을 그대로 품으면 증가한다. | 문자열 포함만 보며 의미적으로 중복된 다른 표현은 놓친다. | 19번 이후 |
| same-card 80% token containment pair | 같은 카드 body 쌍에서 짧은 body의 고유 토큰 중 80% 이상이 긴 body에 포함된 쌍 수다. exact duplicate는 제외하고, 짧은 body는 고유 토큰 5개 이상이어야 한다. | **낮을수록 좋다.** page가 benefit 문장의 대부분을 포함하면 한 쌍으로 센다. | 80%와 최소 토큰 수는 계약값이며 의미는 같지만 어휘가 다른 중복을 놓친다. | 22번 구조 청킹 중복 감사 |
| same-card cross-level pair count | Top5 안 같은 카드의 서로 다른 level 쌍 수 평균이다. | 낮으면 계층 반복 노출이 줄었을 수 있다. | 포함 관계나 relevance를 뜻하지 않는다. | 19번 redundancy summary |
| redundancy W/L/T | 질의별 중복 진단이 baseline보다 개선/악화/동일한 횟수다. | 개선이 악화보다 많아야 중복성 조건을 지지한다. | 어떤 중복 지표를 선택했는지에 따라 뜻이 달라진다. | 19번 MMR gate |

## 의사결정 규칙과 사람 검수

| 규칙·지표 | 한글 설명·비교/분모 | 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| auto-pass / STP | 사람 검수 없이 통과시키는 straight-through processing 후보 규칙이다. 엔진 일치·텍스트/수치 지표 등 계약 조건을 모두 만족한 페이지가 분모다. | 조건을 모두 만족하면 auto-pass 후보가 된다. 예: 두 엔진이 30만 원·1만 원을 일치 반환. | 자동 통과는 gold 정답이 아니며, 안전성 요구에 따라 표본 검수가 필요하다. | OCR 자동 통과 실험 |
| auto-pass accuracy | auto-pass로 분류된 항목 중 gold/human 기준으로 실제 맞은 비율이다. | **높을수록 좋다.** 통과 페이지의 혜택 조건이 실제로 맞아야 한다. | auto-pass로 선택된 부분집합만의 정확도이며 전체 OCR 정확도가 아니다. | OCR gate 사후 검증 |
| false pass | auto-pass로 통과했지만 gold/human 검수에서 틀린 항목의 수 또는 비율이다. | **낮을수록 좋다.** `1만 원`을 잘못 읽고도 통과하면 false pass다. | 사람 검수 표본·gold 범위 밖 오류는 보지 못한다. | OCR gate 안전성 감사 |
| human review | 자동 규칙이 아닌 사람이 원문과 결과를 대조하는 절차·검토 큐다. | 애매한 표 행·단위·조건을 확정하는 데 쓴다. | 사람 간 일치도·검수 지침이 없으면 재현성이 제한된다. | OCR 표·critical fact gate |
| guardrail | primary 개선 후보가 동시에 회귀하지 않아야 하는 보조 지표 조건이다. | Recall·nDCG·Card Hit 또는 answer view가 떨어지면 MRR 상승만으로 통과하지 않는다. | 어떤 guardrail을 채택할지는 사전 계약이어야 하며 post-hoc 추가는 탐색 편향 위험이 있다. | 15~20 ablation decision |
| gate | metric·view·중복성·card 조건을 묶은 채택 가능 여부 규칙이다. | 모든 hard 조건과 필요한 개선을 만족하면 후보가 eligible이다. | 개발셋 통과는 운영 일반화를 뜻하지 않는다. | RRF, GTE, MMR, oracle decision JSON |
| metric tradeoff | 한 지표 개선과 다른 지표 하락이 함께 난 상태를 나타내는 진단 라벨이다. | MRR은 오르나 Recall/nDCG가 내려가면 tradeoff다. | 가치 판단은 사용자 위험·비용 기준 없이는 자동 확정되지 않는다. | 20번 combined oracle |
| technical gate vs independent disposition | technical gate는 사전에 정한 기계 규칙의 통과 여부이고, independent disposition은 수치·계약·약점까지 검토한 최종 개발 판단이다. 둘 다 해당 실험 1건이 분모다. | 둘이 다르면 기계 통과만으로 채택하지 않는다. 예: Hit win 1개 규칙은 통과했어도 Hit loss와 MRR 하락이 있으면 기존 기준선을 유지할 수 있다. | 독립 검토도 개발셋 한계와 검토 기준에 의존하며 운영 일반화를 보장하지 않는다. | 22번 구조 청킹 selection/independent review |

## 품질·비용·시간 함께 해석하기

결과 보고에서는 품질과 비용을 한 숫자로 합치지 말고 아래 항목을 함께 적는다. 비용 절감은 같은 품질 또는 사전에 정한 guardrail을 지킬 때에만 효율 우위의 근거가 될 수 있다.

- **일회성 build와 질의당 비용을 분리한다.** embedding·index build는 corpus를 만들 때 한 번 드는 비용이고, search·rerank·LLM은 질의마다 반복될 수 있다.
- **API 비용은 tokens, requests, 단가, cache를 함께 쓴다.** 13번은 구조 청킹으로 comparable embedding tokens·requests가 줄었지만 query cache 재사용 여부에 따라 실제 전송량은 별도로 기록했다.
- **시간·자원은 latency, throughput, GPU/VRAM을 함께 본다.** 12번 reranker는 pair 수·batch·장치가 시간과 메모리를 좌우하므로, chunk 수만으로 rerank 비용 감소율을 추정하지 않는다.
- **규모를 명시한다.** corpus/chunk 수, reranker candidate TopK, 최종 LLM context token 수는 서로 다른 비용 단위다. Top20/50 후보 수가 같으면 chunk 수가 줄어도 reranker pair 수가 자동으로 줄지 않는다.
- **품질과 함께 판정한다.** 동일하거나 근접한 품질에서 tokens·requests·latency·VRAM이 낮으면 효율 우위를 기록한다. Hit/Recall/MRR 같은 guardrail이 하락하면 비용만으로 채택하지 않는다.
- **미측정과 추정을 구분한다.** search latency, index build 시간, storage bytes, LLM context/비용을 측정하지 않았다면 방향상 기대나 추정으로만 쓰고 실측처럼 보고하지 않는다.

## 실행 자원·재현성

| 지표·기록 | 한글 설명·비교/분모 | 해석·카드 예시 | 핵심 한계 | 사용 범위 |
|---|---|---|---|---|
| latency | 실행 시작부터 결과까지 걸린 시간이다. query·pair·run 단위로 기록한다. | **낮을수록 빠르다.** 카드 질의 20개 rerank에 걸린 초를 비교한다. | cold/warm cache, 장치, batch에 따라 달라 단일 값 일반화가 어렵다. | GTE reranker resource 산출물 |
| throughput | 초당 처리 query·pair·page 수다. 처리 건수/실행 시간이 분모다. | **높을수록 효율적이다.** reranker pairs/s가 높으면 동일 카드 질의를 더 빨리 처리한다. | 품질·VRAM·batch를 통제하지 않으면 공정 비교가 아니다. | GTE resource 산출물 |
| VRAM allocated / reserved | GPU가 실제 할당한 메모리와 allocator가 확보해 둔 메모리를 MiB/GiB로 기록한다. | 낮을수록 여유가 크다. allocated와 reserved는 같은 값이 아니다. | GPU·프레임워크 allocator에 종속하며 CPU-only 실행에는 N/A다. | GTE resource 산출물 |
| token count | 모델 입력으로 토큰화된 길이 또는 처리 토큰 수다. | 과도하면 지연·절단 위험이 증가한다. 카드 표·긴 약관 청크에서 중요하다. | tokenizer·모델 revision에 따라 달라진다. | reranker/LLM 실행 기록 |
| truncation | 입력이 max length를 넘어 잘린 건수·비율이다. | **낮을수록 좋다.** benefit 말미의 월 한도가 잘리면 위험하다. | 0이라도 모델이 조건 관계를 이해했다는 보장은 없다. | GTE reranker resource 산출물 |
| API/network calls | 외부 API·네트워크 호출 수다. | 0이면 local-only/offline 계약을 뒷받침한다. | 호출 0은 캐시·입력 자체의 품질을 보장하지 않는다. | 13·17~20 execution contract |
| cache hit / cache usage | 저장 embedding·OCR·모델 결과를 재사용한 건수 또는 여부다. | 재사용은 비용·변동성을 줄인다. | cache freshness와 source hash 불변을 별도 확인해야 한다. | retrieval/reranker integrity·manifest |
| completeness | 기대된 페이지·질의·필드·행이 결과에 존재하는 비율 또는 완료 상태다. | **높을수록 좋다.** 10개 카드의 요구 페이지가 모두 있으면 완전하다. | 존재는 정확도와 다르며 null/빈 결과가 포함될 수 있다. | OCR run manifest, evaluation contract |
| single run / repeated run | single run은 1회 결정론 실행의 평균, repeated run은 독립 반복의 평균과 분산/표준편차를 보고하는 방식이다. | repeated run의 표준편차가 작을수록 변동이 작다. | 현재 다수 retrieval·reranker·MMR 보고서는 **single deterministic run**으로 표준편차가 N/A다. | 13~20 개발셋 산출물; 반복 실험이 있을 때만 variance 보고 |
| macro / micro / pooled | macro는 group별 점수의 단순 평균, micro는 전체 원자 단위를 합쳐 계산, pooled는 계약에 따라 모든 관측치를 한 pool로 합친 요약이다. | 소수 group이 큰 group에 묻히는지 확인한다. 예: card 10/evidence 20을 같은 비중으로 볼지 구분한다. | 분모·가중 방식이 다르므로 서로를 같은 수치처럼 비교하면 안 된다. | OCR fact·query group summary에서 계약별 사용 |

## 지표가 아닌 parameter

| parameter | 역할 | 해석상 주의 | 사용 범위 |
|---|---|---|---|
| BM25 `k1`, `b` | term frequency 포화와 문서 길이 정규화를 정한다. | 값의 높고 낮음은 품질 지표가 아니며 같은 후보·질의에서 ablation해야 한다. | normalized BM25 retrieval |
| RRF `k` | reciprocal rank fusion의 rank 완화 상수다. | 결과 지표가 아니라 fusion 곡선을 정하는 설정이다. | hybrid/RRF retrieval |
| RRF depth | 각 retriever에서 fusion에 넣는 TopK 범위다. 답변이 K개 반환된다는 뜻이 아니다. | candidate ceiling·비용을 바꾸므로 Top20/Top50 결과를 직접 혼동하지 않는다. | 16~20 RRF/reranker |
| RRF weight | vector와 normalized BM25 rank의 상대 기여를 정한다. | 0.4:0.6 같은 비율은 후보 순서 설정이지 metric이 아니다. | 16, 17, 19, 20 |
| MMR λ | relevance와 중복 벌점의 비중을 정한다. | λ가 작을수록 중복 억제가 강하지만 품질 향상을 보장하지 않는다. | 19, 20 MMR |
| TopK / reranker candidate count | reranker에 전달하거나 평가하는 후보 수다. | 후보 수가 다르면 recall ceiling·비용이 달라진다. | 16~20 |
| batch size / precision | 한 번 처리하는 pair 수와 FP16/FP32 등 수치 형식이다. | latency·VRAM·미세 순위에 영향을 줄 수 있으나 품질 metric이 아니다. | GTE 실행 계약 |

## 역사적 alias와 N/A 계약

- 초기 13번 `hit_at_3`은 expected card만 보는 **Card Hit@3**이다. strict evidence Hit@3와 같은 지표가 아니다.
- `field_set_exact_match`는 초기 **page-level 예측 field ID 집합 exact**다. 이후 `structured field exact`의 **필드 값 exact**와 혼용하지 않는다.
- 10번 `structured-array table-row relation exact`와 12번 `source-located table-row relation exact`는 서로 다른 평가 계약이다. 전자는 배열 관계, 후자는 원본 위치까지 연결한 행 관계다.
- `card_diversity_at_5`는 초기 일부 CSV에 별도 저장되지 않아 그 범위에서는 N/A다. 19번 이후 `unique_card_count`, `expected_card_count`, `off_card_count`가 저장된 범위에서만 중복·카드 구성을 수치 감사한다.
- combined MMR+GTE의 전체 cosine은 GTE cosine이 저장되지 않아 N/A이며, numeric MMR 10질의의 구조 진단과 같은 값으로 대체하지 않는다.
- N/A는 0이나 실패가 아니라, 해당 계약에서 값이 저장·정의되지 않았음을 뜻한다.

## Inventory 보완: OCR·운영 검수

| 항목 | 정의·비교/분모 | 해석·카드 예시 | 한계 | 사용 범위·상태 |
|---|---|---|---|---|
| critical fact substring match | gold critical fact의 정규화 문자열이 OCR 텍스트에 부분문자열로 존재하는 비율이다. critical fact 수가 분모다. | **높을수록 좋다.** `전월실적 30만 원` 문구가 페이지 OCR에 그대로 있으면 통과다. | 단어 경계·동의어·행 분할에 약하고, 값 관계가 맞는지 보장하지 않는다. | OCR critical fact 진단; **정의됨** |
| critical fact token coverage | critical fact의 필수 토큰 중 OCR 텍스트에 나타난 토큰 비율이다. 해당 fact의 gold 토큰 수가 분모다. | **높을수록 좋다.** 편의점·30만·1만 원 토큰을 모두 찾으면 1이다. | 토큰 순서·부정·조건 결합을 보지 않는다. | OCR critical fact 진단; **정의됨** |
| field value recall in OCR text | gold structured field의 값 문자열/토큰이 OCR 텍스트에 회수된 비율이다. 평가 가능한 gold field value가 분모다. | **높을수록 좋다.** 연회비 2만 원 값이 OCR 본문에 있으면 회수다. | 추출 JSON 정확도가 아니라 원문 OCR coverage다. | OCR↔structured 교차 진단; **정의됨** |
| page-level field-set exact | 한 페이지에서 사전에 정한 **평가 대상 field ID 집합 자체**가 gold field ID 집합과 정확히 같은지 보는 역사적 지표다. 페이지 수가 분모다. | **높을수록 좋다.** 할인율·한도·조건 ID가 모두 있고 추가/누락 ID가 없으면 통과다. | 값이 맞는지는 보지 않는다. 이후 structured field exact(값 exact)와 절대 혼용하지 않는다. | 초기 page-level field-set 평가; **historical alias/정의됨** |
| numeric token exact | gold와 예측의 수치 토큰을 표면형 또는 계약된 정규화로 일대일 exact 비교한 비율이다. numeric token 수가 분모다. | **높을수록 좋다.** `30만`, `2%`, `월 3회`가 각각 맞아야 한다. | 숫자가 맞아도 대상·조건 연결은 틀릴 수 있다. | OCR 숫자 감사; **정의됨** |
| normalized / raw edit distance | raw는 원문 문자열, normalized는 계약된 정규화 문자열 사이의 편집 거리다. 비교 문자열 길이가 기준이다. | **낮을수록 좋다.** 공백·쉼표 제거 전후 오류 민감도 차이를 분리한다. | 거리만으로 사용자 위험을 표현하지 못하며 길이가 다르면 직접 비교가 어렵다. | OCR text 비교; **정의됨** |
| reference / prediction char·token count | gold와 예측의 문자 수·토큰 수를 각각 기록하는 길이 진단이다. 페이지/문서가 관측 단위다. | 차이가 크면 누락·과잉 OCR 가능성을 의심한다. 예: 표 한 행이 빠지면 prediction token count가 줄 수 있다. | 길이가 비슷해도 내용은 틀릴 수 있다. | OCR text 비교; **diagnostic** |
| length difference / ratio | 예측 길이−gold 길이 및 예측/gold 길이 비율이다. gold 길이가 기준이다. | 0 또는 1에 가까울수록 길이상 유사하다. | gold 길이 0의 비율은 N/A이며, 길이 유사는 정확도가 아니다. | OCR text 비교; **diagnostic** |
| character_similarity | 문자열 유사도 계열의 과거 표기다. 실제 계산식은 저장 산출물 계약을 따른다. | 높을수록 문자열이 유사하다는 보조 신호다. | CER의 역수·동일 지표라고 가정하면 안 된다. | 초기 OCR 산출물; **historical alias** |
| official / preview coverage eligibility | official gold/검증 대상에 포함되는 항목과 preview·참고 항목을 구분해 평가 eligibility를 계산한다. eligible 항목 수가 분모다. | official 대상이 모두 평가 가능한지 확인한다. 예: preview 페이지는 정확도 분모에서 제외될 수 있다. | 제외는 품질 통과가 아니며 범위 제한을 명시해야 한다. | OCR goldset coverage; **정의됨** |
| engine agreement rate | 두 엔진의 텍스트·필드·fact 결과가 계약된 비교 단위에서 일치한 비율이다. 공통 평가 항목이 분모다. | **높을수록 합의가 많다.** 두 엔진이 월 한도 1만 원에 동의하면 일치다. | 같은 오류에 함께 동의할 수 있어 gold 정확도가 아니다. | dual OCR 검증; **정의됨** |
| human review queue count / rate | 자동 통과가 아니거나 불일치·위험 조건으로 사람 검수에 들어간 항목 수/비율이다. 전체 eligible 항목이 분모다. | **낮을수록 자동화 부담은 작다.** 표 행 불일치 페이지가 queue에 들어간다. | 낮은 queue가 안전성을 보장하지 않으며 검수 정책에 좌우된다. | 운영 검수 진단; **정의됨** |
| high-confidence wrong count | 높은 confidence 또는 auto-pass 조건을 만족했지만 gold/human에서 틀린 항목 수다. | **낮을수록 좋다.** 확신 높게 1만 원을 2만 원으로 내면 위험 사례다. | confidence 정의·gold/human 범위에 따라 달라진다. | OCR 안전성 감사; **정의됨** |
| disagreement category distribution | 엔진·gold 불일치를 수치·대상·조건·표 구조 등 범주별 수/비율로 나눈 분포다. 불일치 건이 분모다. | 특정 범주가 많으면 개선 우선순위가 된다. 예: 조건 불일치가 집중될 수 있다. | 범주 체계·복수 원인 처리 방식에 의존한다. | dual OCR/human review 감사; **diagnostic** |

`auto-pass/STP`의 분모는 **전체 eligible 항목**이고, `auto-pass accuracy`의 분모는 **그중 실제 auto-passed 항목**이다. `false pass`와 `high-confidence wrong`은 서로 겹칠 수 있으나 동일 지표라고 가정하지 않는다.

## Inventory 보완: 구조화 coverage와 진단

| 항목 | 정의·비교/분모 | 해석·카드 예시 | 한계 | 사용 범위·상태 |
|---|---|---|---|---|
| source-supported audit coverage | 원본 페이지·좌표·문서 경로 등 source 근거까지 연결돼 감사 가능한 라벨 비율이다. 전체 audit label이 분모다. | **높을수록 추적 가능성.** 할인 한도 값이 어느 PDF 페이지에서 왔는지 확인 가능하다. | source가 있다고 값이 정확한 것은 아니다. | source-located structured audit; **정의됨** |
| relation-scoring-supported coverage | 관계 점수 계약이 실제로 채점할 수 있는 원자/관계 라벨 비율이다. 전체 관계 라벨이 분모다. | **높을수록 관계 평가 범위가 넓다.** 대상·조건·혜택 연결을 점수화할 수 있다. | 지원은 정확도나 source support와 다르다. | Critical Fact v2; **정의됨** |
| explicit table locator coverage | table/page/row locator가 명시돼 원본 표 행에 연결 가능한 평가 항목 비율이다. locator 요구 항목이 분모다. | **높을수록 표 검증 가능.** 30만 원 조건 행을 원본 표 위치로 찾을 수 있다. | locator는 OCR 셀 분할 정확도를 보장하지 않는다. | source-located table audit; **정의됨** |
| diagnostic heuristic projection / fallback match | 엄격한 locator·관계가 없을 때 휴리스틱 투영 또는 fallback이 gold와 맞았는지 보는 **진단**이다. fallback 적용 항목이 분모다. | 높으면 fallback이 해당 샘플에서 근사적으로 맞았다. | 성능·채택 지표가 아니며 명시 locator exact를 대체할 수 없다. | structured 감사 보조; **diagnostic** |
| unsafe numeric mismatch | 존재하는 numeric 값이 gold와 다르게 단정된 비율이다. 평가 가능 numeric leaf가 분모다. | **낮을수록 좋다.** 10,000원을 20,000원으로 반환하면 실패다. | null·누락은 포함하지 않아 missing rate를 함께 봐야 한다. | Critical Fact v2; **정의됨** |

## Inventory 보완: 후보군·view·비교 통계

| 항목 | 정의·비교/분모 | 해석·카드 예시 | 한계 | 사용 범위·상태 |
|---|---|---|---|---|
| expected-card candidate hit | reranker 후보 TopK에 expected card 청크가 하나 이상 있는 질의 비율이다. query group이 분모다. | **높을수록 좋다.** Top20에 A카드 청크가 있으면 통과다. | strict/answer relevance를 보지 않아 candidate hit·recall과 다르다. | candidate ceiling; **정의됨** |
| Top50 union unique count | 여러 retriever/weight의 Top50 합집합에서 중복 제거 뒤 남은 고유 chunk 수다. 질의별 또는 집계 단위로 기록한다. | 크면 서로 다른 후보가 보완될 수 있다. | 많아도 관련 후보가 늘었다는 뜻은 아니다. | RRF candidate union; **diagnostic** |
| missing relevant ID count | 해당 view의 gold relevant chunk ID 중 후보 TopK에 없는 개수다. gold relevant ID 수가 기준이다. | **낮을수록 좋다.** strict benefit ID가 후보에 없으면 reranker가 복구 못한다. | gold relevance 계약·view에 따라 값이 달라진다. | candidate coverage 감사; **diagnostic** |
| leaf candidate count | card/page 제거 뒤 section/benefit 후보로 남은 수(K)다. 질의 또는 variant가 관측 단위다. | K가 크면 leaf rerank 입력이 넓다. | fixed20과 available은 K 정의가 달라 절대 비교 불가다. | 18번 leaf-only; **diagnostic** |
| leaf candidate coverage | leaf pool 안 strict/answer 관련 unit의 hit/recall이다. 해당 view relevant unit이 분모다. | **높을수록 좋다.** leaf TopK에 혜택 근거가 남아야 한다. | parent 제거로 answer recall이 줄 수 있고 reranker 품질 자체가 아니다. | 18번 leaf-only; **정의됨** |
| candidate hit/recall view N/A | candidate hit·recall은 strict, answer-bearing, exact, family 등 **각 view의 relevance unit이 존재하고 후보 pool이 정의된 경우에만** 계산한다. | 해당 view가 정의되면 높을수록 좋다. | relevant unit 0, 저장된 후보 없음, 적용하지 않은 view는 0이 아니라 **N/A**다. | 16~20 candidate contract; **N/A 계약** |
| answer-bearing exact-doc view | answer-bearing relevance에 exact document grouping을 적용한 별도 view다. | **높을수록 좋다.** 동일 정규화 문서의 여러 level 중 한 그룹이 앞서면 평가한다. | strict exact-group과 relevance 정의가 달라 한 행으로 합치면 안 된다. | 18번 이후; **정의됨** |
| guardrail-loss count | 사전 정의한 guardrail metric·view 조건 중 baseline보다 회귀한 조건 수다. gate check 수가 분모/기준이다. | **낮을수록 좋다.** MRR이 올라도 Recall·nDCG 손실이 있으면 증가한다. | 조건 수는 gate 설계에 의존하며 손실 크기를 말하지 않는다. | 15~20 decision audit; **diagnostic** |
| run count | 독립 실행 횟수다. | 반복 실행이 많을수록 변동성을 추정할 수 있다. | 동일 seed 재실행이 완전 독립 표본인지 계약을 확인해야 한다. | 모든 실험 execution contract; **정의됨** |
| mean / std / min / max | 반복 실행 또는 질의 값의 평균·표준편차·최솟값·최댓값 요약이다. std는 계약된 `ddof=0`이면 population standard deviation이다. | 평균은 중심, std/min/max는 변동·경계를 본다. | **single run의 run-level std는 N/A**이며 질의별 분포와 반복 run 변동을 혼동하지 않는다. | 반복 측정 시; single deterministic 보고서는 N/A 계약 |

## Inventory 보완: MMR 세부 구조 진단

| 항목 | 정의·비교/분모 | 해석·카드 예시 | 한계 | 사용 범위·상태 |
|---|---|---|---|---|
| pairwise cosine max | Top5 청크 쌍의 양의 cosine 중 최댓값이다. 질의별 쌍이 기준이다. | **낮을수록 매우 유사한 중복 쌍이 줄 수 있다.** page·benefit이 거의 같으면 높다. | 한 쌍의 극값이라 전체 중복을 대표하지 않는다. | 19번 redundancy; **diagnostic** |
| nearest-neighbor positive cosine mean | 각 Top5 청크의 가장 유사한 양의 cosine 이웃을 평균한 값이다. Top5 청크가 기준이다. | **낮을수록 이웃 중복이 적을 가능성.** 같은 할인 문장 반복 시 높다. | cosine은 의미 중복의 대리값이다. | 19번 redundancy; **diagnostic** |
| unique exact group count | Top5를 exact document group으로 묶은 뒤 고유 그룹 수 평균이다. | **높을수록 exact 중복이 적다.** 같은 문서 반복 대신 다른 혜택 근거가 보이면 증가한다. | 서로 다른 group도 의미상 중복일 수 있다. | 19번 redundancy; **diagnostic** |
| cross-level cosine ≥0.90 count | 같은 카드의 서로 다른 level 쌍 중 cosine이 0.90 이상인 쌍 수다. | **낮을수록 강한 계층 중복이 적다.** page와 benefit이 거의 동일하면 증가한다. | 0.90은 진단 threshold이며 품질 metric·인과 증명은 아니다. | 19번 redundancy; **diagnostic** |
| expected-card count | Top5 중 expected card 청크 수 평균이다. | **높을수록 보통 카드 라우팅 집중이 좋다.** A카드 질의에 A카드 근거가 많다. | strict relevance·다양성과 동일하지 않다. | 19번 redundancy 이후; **diagnostic** |
| original RRF rank mean / max | MMR 선택 Top5가 원래 fused RRF에서 갖던 순위의 평균과 최댓값이다. | 낮으면 원래 상위 후보를 많이 유지한다. 예: MMR이 20위 청크를 넣으면 max가 커진다. | 낮은 원래 순위가 곧 관련성은 아니다. | 19번 MMR step/redundancy; **diagnostic** |
| normalized RRF relevance mean / sum | MMR에 투입된 RRF relevance를 계약된 min-max 등으로 정규화한 값의 평균/합이다. | 높으면 MMR 선택이 원래 RRF 관련성을 더 보존한 신호다. | 정규화 anchor와 depth에 의존하며 다른 run과 직접 비교하면 안 된다. | 19번 MMR; **diagnostic** |
| redundancy improved / worsened / tied count | baseline 대비 질의별 redundancy 진단이 개선/악화/동일인 횟수다. query 수가 기준이다. | 개선이 많고 악화가 적을수록 중복 제어 목적을 지지한다. | 어떤 redundancy composite를 사용했는지 계약을 확인해야 한다. | 19번 MMR; **diagnostic** |

## Inventory 보완: runtime·resource·완결성

| 항목 | 정의·비교/분모 | 해석·카드 예시 | 한계 | 사용 범위·상태 |
|---|---|---|---|---|
| per-page elapsed | 한 페이지 OCR/평가를 처리한 실측 경과 시간이다. 페이지가 관측 단위다. | **낮을수록 빠르다.** 표가 긴 카드 안내 페이지의 초 단위 시간을 본다. | cold/warm·API 대기·하드웨어에 따라 변한다. | OCR run timing; **measured** |
| model load / warm-up / scoring seconds | 모델 로드, 첫 실행 준비, 실제 pair/page scoring에 쓴 시간을 분리 기록한다. | scoring만 빠른지 로드까지 빠른지 구분한다. | 경계 시점·cache 상태가 계약에 따라 달라진다. | reranker resource; **measured** |
| measured pairs/s | 실제 처리 pair 수를 실제 scoring 시간으로 나눈 throughput이다. | **높을수록 빠르다.** 카드 질의-청크 pair를 초당 더 처리한다. | batch·GPU·precision이 달라지면 공정 비교가 아니다. | GTE resource; **measured** |
| estimated per-config latency | 측정 throughput·pair 수로 계산한 설정별 예상 지연이다. | 비용 계획에 쓴다. 예: Top50은 Top20보다 예상 시간이 길 수 있다. | **실측 latency가 아니다.** 모델 load·queue·I/O를 포함하지 않을 수 있다. | candidate/reranker 비용 추정; **estimated** |
| peak allocated / reserved | 실행 중 GPU allocator의 최대 실제 할당/예약 메모리다. 실행 run이 관측 단위다. | 낮을수록 여유가 크다. allocated와 reserved를 분리한다. | CPU-only는 N/A이며 allocator 구현에 의존한다. | GTE resource; **measured** |
| input / output / total token usage | 요청 입력, 생성 출력, 합계 토큰 수다. run·page·request가 관측 단위다. | 낮으면 비용·절단 위험이 줄 수 있다. | tokenizer·provider 과금 정의에 의존한다. | LLM/OCR API usage; **measured when stored** |
| API request count | 외부 API 요청 횟수다. | 낮으면 호출 비용·외부 의존이 작다. | 요청 수는 토큰·성공률·품질을 뜻하지 않는다. | API-backed OCR/LLM execution; **measured** |
| cache hit count / rate | 캐시 재사용 건수와 재사용/캐시 조회 기회 비율이다. | 높으면 비용·변동을 줄일 수 있다. | stale cache·hash 일치를 별도 검증해야 한다. | OCR/retrieval cache manifest; **measured when stored** |
| truncation count / rate | max length로 잘린 입력의 건수와 처리 입력 대비 비율이다. | **낮을수록 좋다.** 조건·월 한도가 끝에서 잘리면 위험하다. | 0이어도 semantic 이해를 보장하지 않는다. | reranker resource; **measured** |
| input token p50 / p95 / max | 입력 token 길이의 중앙값, 95백분위, 최댓값이다. 입력 건이 분모다. | p95/max가 크면 긴 카드 약관의 tail 위험을 본다. | 작은 표본의 percentile은 불안정할 수 있다. | reranker/LLM input diagnostics; **measured when stored** |
| success / failure / incomplete count | 정상 완료, 실패, 기대 산출물 일부 누락 건수다. 요청·page·document 단위별로 기록한다. | success가 높고 failure/incomplete가 낮을수록 좋다. | 완료는 정확도를 뜻하지 않는다. | OCR run manifest; **measured** |
| expected / actual page·document count | 계약상 처리해야 할 페이지·문서 수와 실제 산출된 수를 비교한다. | actual이 expected와 같으면 coverage 완결성 신호다. | 수가 같아도 중복·빈 결과가 있을 수 있다. | OCR/retrieval input-output completeness; **measured** |

## 집계 혼용 금지 예시

검색 `query-macro`는 20개 Evidence 질의의 query별 MRR을 먼저 평균한다. 반면 structured `pooled`/`micro`는 모든 numeric leaf 또는 relation을 합쳐 큰 분모로 계산할 수 있다. 예를 들어 1개 카드 질의의 MRR 개선과 수백 개 leaf의 micro exact를 같은 “평균 성능”처럼 직접 비교하면 안 된다. `macro`는 group을 같은 비중으로, `micro/pooled`는 관측 수가 많은 group에 더 큰 비중을 준다.

토큰 Precision/Recall/F1도 tokenizer와 집계 계약을 확인한다. **set** 비교는 같은 토큰의 반복을 한 번만 보며, **multiset** 비교는 반복 횟수까지 센다. 카드 표에서 `월 1회`가 여러 행에 반복될 때 두 계약은 다른 값을 낼 수 있다.

## Inventory 추적 체크리스트

- [x] OCR/text 누락 항목: **정의됨** 또는 historical alias(`character_similarity`, `field_set_exact_match`)로 분리했다.
- [x] 운영 검수·auto-pass·human queue·false/high-confidence pass 항목: **정의됨**으로 분리했다.
- [x] structured coverage·fallback: coverage는 **정의됨**, heuristic projection/fallback은 **diagnostic**으로 표시했다.
- [x] retrieval candidate·view·N/A: **정의됨**이며 candidate view의 0과 N/A를 분리했다.
- [x] paired·gate·run summary: 비교 통계/decision rule 또는 **N/A 계약**으로 표시했다.
- [x] MMR 구조 항목: 모두 **diagnostic**으로 표시했다.
- [x] runtime/resource: 저장된 경우 **measured**, throughput 기반 추정은 **estimated**, 저장되지 않은 경우 N/A로 표시했다.
- [x] parameter는 metric이 아니라 별도 표에 유지했다.

## 사용 원칙

1. 지표 하나로 OCR 안전성이나 검색 채택을 단정하지 않는다.
2. 비교는 같은 query set, candidate depth, relevance view, 집계 방식인지 먼저 확인한다.
3. decision rule은 가능하면 사전에 고정한다. adaptive/post-hoc oracle 결과는 원인 진단 상한으로만 기록한다.
4. 저장 산출물에 없는 값은 추정하거나 역산해 새 지표처럼 보고하지 않는다.
