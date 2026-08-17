# OCR 평가 지표 사전

이 문서는 PickCardU OCR 실험에서 사용하는 지표의 공통 정의다. 새 지표를 도입하면 이 문서에 **의미, 계산 단위, 카드 혜택 예시, 한계**를 함께 추가한다.

## 먼저: 평가 층위

| 층위 | 비교 대상 | 답하는 질문 |
|---|---|---|
| 전체 텍스트 | 수기 TXT와 OCR TXT | 페이지의 글자가 얼마나 비슷한가? |
| 수치 토큰 | 금액·비율·횟수 토큰 | 숫자가 보존됐는가? |
| 구조화 필드 | gold JSON과 예측 JSON | 정해진 필드 값이 맞는가? |
| semantic fact | 대상·조건·수치가 묶인 사실 | 사용자에게 잘못된 혜택을 안내할 위험이 없는가? |

예를 들어 정답이 “편의점에서 전월실적 30만 원 이상이면 월 1만 원 할인”일 때, `1만 원`만 맞아도 충분하지 않다. 대상(편의점)과 조건(전월 30만 원)까지 함께 맞아야 안전하다.

## 전체 텍스트 지표

### CER (Character Error Rate, 낮을수록 좋음)

- **의미:** 정규화한 예측 문자열을 정답 문자열로 바꾸기 위해 필요한 문자 삽입·삭제·교체의 비율이다.
- **예시:** 정답 `전월실적 30만원`을 OCR이 `전윈실적 30만원`으로 읽으면 문자 하나의 교체 오류가 반영된다. `30만원`을 통째로 누락하면 삭제 오류가 커진다.
- **한계:** 읽기 순서, 줄바꿈, 표를 행으로 풀어쓴 방식에도 민감하다. OCR이 정답보다 길면 1보다 커질 수 있으며, 숫자의 의미 관계를 보장하지 않는다.

### 토큰 정밀도 · 재현율 · F1 (높을수록 좋음)

- **의미:** 정규화된 단어 토큰의 겹침을 본다. 정밀도는 OCR이 낸 토큰 중 맞는 비율, 재현율은 정답 토큰을 얼마나 빠뜨리지 않았는지, F1은 둘의 균형이다.
- **예시:** 정답 `해외 가맹점 2% 할인`과 OCR `해외 가맹점 2% 청구 할인`은 대부분 토큰이 겹쳐 F1이 높다.
- **한계:** 토큰 순서와 문장 구조를 약하게 본다. `숙박 2% 할인`도 숫자·할인 토큰이 겹치므로 F1만으로는 위험한 대상 오류를 충분히 잡지 못한다.

### 전체 TXT 완전 일치 (높을수록 좋음)

- **의미:** 페이지의 정규화된 전체 OCR 문자열이 gold TXT와 완전히 같은지 본다.
- **예시:** 한 글자라도 다른 `전원실적`/`전윈실적`, 표의 행 순서 차이, 줄바꿈 차이도 실패다.
- **한계:** 매우 엄격해서 운영 통과 기준으로 단독 사용하기 부적절하다. 전체 텍스트 품질의 상한 확인용이다.

### 수치 F1 (높을수록 좋음)

- **의미:** 금액·비율·횟수·기간처럼 숫자 형태인 토큰만 골라 토큰 F1을 계산한다.
- **예시:** `30만 원`, `2%`, `월 3회`가 모두 보존되면 높아진다. `1만 원`을 `2만 원`으로 읽으면 숫자 토큰 불일치다.
- **한계:** `편의점 1만 원`이 `숙박 1만 원`으로 바뀌어도 숫자는 같으므로 통과할 수 있다. semantic fact 지표와 함께 봐야 한다.

## 구조화 필드와 교차검증 지표

### 값 존재율 (높을수록 좋음)

- **의미:** 요구한 필드 ID에 대해 구조화 단계가 값 또는 `null`을 반환한 비율이다.
- **예시:** 117개 필드 모두가 JSON에 존재하면 100%다.
- **한계:** `null`이나 오답도 존재로 계산될 수 있다. 정확도 지표가 아니다.

### 필드 exact match / 핵심 필드 exact match (높을수록 좋음)

- **의미:** gold의 한 필드 값과 예측 필드 값이 정규화 뒤 완전히 같은 비율이다. 핵심 필드 exact는 `critical=true` 필드만 대상으로 한다.
- **예시:** `{"할인율": 0.02, "월한도": 10000}`에서 OCR이 월한도를 `20000`으로 내면 그 필드는 실패한다.
- **한계:** 배열·복합 객체·표는 한 부분만 달라도 전체 필드가 실패한다. 어느 부분이 틀렸는지는 leaf 단위 지표가 더 잘 설명한다.

### 엔진 일치율 / 자동 통과 후보

- **의미:** 두 OCR 엔진의 텍스트 또는 구조화 값이 서로 같은 비율이다. 기존 실험에서는 토큰 F1 0.98 이상이고 수치 F1 1.00인 페이지를 자동 통과 **후보**로 표시했다.
- **예시:** Upstage와 OpenAI가 모두 `전월실적 30만 원`을 반환하면 서로 일치한다.
- **한계:** 두 엔진이 같은 오류를 낼 수 있다. 엔진 일치는 정답이 아니며 gold 기반 평가나 사람 검수가 필요하다.

## Semantic fact 지표

semantic fact는 필드 하나를 더 안전한 단위로 묶은 것이다. 예시는 다음과 같다.

```json
{
  "대상": "편의점",
  "조건": {"전월실적_원": 300000},
  "혜택": {"할인_원": 10000, "월_한도": 10000}
}
```

### Fact exact match (높을수록 좋음)

- **의미:** 한 fact 안의 대상·조건·수치·정답에 기록된 단위가 모두 맞는 비율이다.
- **예시:** OCR이 `숙박`, `30만 원`, `1만 원`을 반환하면 숫자는 맞아도 대상이 달라 해당 fact는 실패다. `편의점`, `20만 원`, `1만 원`도 조건이 달라 실패다.
- **의의:** 사용자에게 잘못된 카드 혜택을 안내할 위험을 가장 직접적으로 표현한다.
- **한계:** 아주 엄격하므로 낮은 점수만 보고 어느 요소가 문제인지 알기 어렵다. 아래 leaf 지표를 함께 본다.

### 핵심 수치 정확도 (높을수록 좋음)

- **의미:** 모든 fact 내부의 숫자 leaf가 정확한 비율이다. 금액, 할인율, 횟수, 전월실적, 기간, 한도가 대상이다.
- **예시:** `할인_원: 10000`을 `20000`으로 읽으면 실패한다. `할인율: 0.02`를 `0.2`로 읽어도 실패한다.
- **의의:** 금전적 손실·잘못된 자격 조건으로 이어질 수 있는 치명적인 숫자 오류를 빠르게 감지한다.
- **한계:** `편의점 1만 원`과 `숙박 1만 원`은 숫자만 보면 둘 다 통과한다.

### 문자 leaf 정확도 (높을수록 좋음)

- **의미:** 대상 업종, 조건 문구, 날짜, 정답 JSON에 기록된 단위처럼 문자열 leaf의 정확도다. 현재 구현은 공백을 제거한 뒤 비교한다.
- **예시:** `해외 가맹점`을 `국내 가맹점`으로 읽으면 실패한다. `전원실적`을 `전윈실적`으로 읽어도 실패한다.
- **의의:** 숫자는 맞지만 혜택 대상·조건이 바뀌는 오류를 분리해서 본다.
- **한계:** 숫자 오류는 잡지 못하며, `won`과 `KRW`처럼 의미가 같은 단위 enum 표기는 현재 실패할 수 있다.

### 단위 정확도 (높을수록 좋음)

- **의미:** gold에 단위가 기록된 수치 fact에서 단위가 같은 비율이다.
- **예시:** `5,000원`의 단위를 `won`으로 맞히면 통과한다. `5,000달러`처럼 통화가 달라지면 실패한다.
- **한계:** 단위가 gold에 없는 경우는 평가에서 제외한다. `won`/`KRW` 같은 동의어 정규화는 아직 개선 과제다.

### Null prediction rate (낮을수록 좋음)

- **의미:** 정답이 있는 fact에 대해 구조화 단계가 근거 부족으로 전체 `null`을 반환한 비율이다.
- **예시:** 원문에 `전월실적 30만 원`이 있는데도 해당 fact를 `null`로 내면 실패로 집계된다.
- **한계:** `null`이 아닌 그럴듯한 오답은 이 지표에 나타나지 않는다. fact exact와 함께 본다.

## 관계형 Critical Fact v2 지표

v2는 큰 복합 필드를 원자 값으로 나누되 `혜택 ID → 대상·조건 → 값 → 원본 페이지/JSON 경로` 관계를 유지한다. 아래 지표는 10번 노트북에서 구현됐다.

### 원자 관계 정확도 (Atomic relation exact rate, 높을수록 좋음)

- **의미:** 미리 정의된 혜택·조건 경로에 놓인 한 원자 값이 정답과 정확히 같은 비율이다. 단위가 별도 평가 가능한 수치 fact는 값과 단위가 모두 맞아야 한다.
- **예시:** `월납요금 할인 → tiers[0] → monthly_limit_krw`의 정답이 `3,000원`인데 예측이 같은 경로에 `7,000원`을 넣으면 실패한다. 다른 곳에 `3,000원`이 있어도 통과하지 않는다.
- **한계:** 필드 ID와 JSON 경로를 추출기에 미리 제공한 평가다. 모델이 자유 형식 문서에서 관계 자체를 처음부터 발견하는 능력과 동일하지 않다.

### 관계 그룹 정확도 (Relation group exact rate, 높을수록 좋음)

- **의미:** 한 혜택 또는 중첩 객체에 속한 평가 가능 원자 값이 전부 맞아야 그룹을 통과시킨다.
- **예시:** `해외 가맹점`, `2%`, `전월실적 없음`, `월 한도 없음` 중 하나라도 틀리면 해당 해외 할인 관계 그룹은 실패한다.
- **한계:** 큰 그룹일수록 실패 가능성이 커진다. 원인을 보려면 원자 상세와 오류 유형을 함께 봐야 한다.

### 표 행 관계 정확도 (Table-row relation exact rate, 높을수록 좋음)

- **의미:** 배열로 표현된 표의 한 행에서 조건과 결과 값이 모두 맞는 비율이다.
- **예시:** `전월 30만~50만원 → 월 3,000원`, `50만~100만원 → 월 7,000원`일 때 두 한도 숫자가 서로 바뀌면 값 자체가 문서에 모두 존재해도 두 행 모두 실패한다.
- **한계:** 현재 `tiers[n]` 같은 구조화 배열을 표 행으로 본다. OCR 좌표와 실제 PDF 셀 경계가 맞는지는 별도 좌표 평가가 필요하다.

### 위험 오답률 (Unsafe mismatch rate, 낮을수록 좋음)

- **의미:** 평가 가능한 fact 중 값을 누락한 것이 아니라, 존재하는 값을 틀리게 단정한 비율이다. `unsafe numeric mismatch`는 그중 숫자만 따로 집계한다.
- **예시:** 할인 한도를 `null`로 반환하면 누락이지만, 정답 `10,000원`을 `20,000원`으로 반환하면 위험 수치 오답이다. 대상이 `편의점`인데 `숙박`으로 반환한 경우도 위험 오답이다.
- **한계:** 누락 역시 검색·추천 품질에는 문제지만 이 지표의 분자에는 포함되지 않는다. 누락률과 함께 해석해야 한다.

### 예측 누락률 (Missing prediction rate, 낮을수록 좋음)

- **의미:** 기존 추출 스키마가 요청한 평가 가능 원자 fact에서 필드 또는 하위 경로가 아예 없는 비율이다.
- **예시:** 연회비 객체는 반환했지만 `partner_krw` 키를 반환하지 않으면 해당 원자 fact가 누락이다.
- **한계:** 값은 있지만 오답인 경우를 잡지 않는다. 위험 오답률 및 원자 관계 정확도와 함께 본다.

### 라벨 평가 지원율 (Prediction-supported label coverage, 높을수록 좋음)

- **의미:** v2 전체 안전성 라벨 중 현재 예측 스키마가 실제로 요청해 공정하게 점수화할 수 있는 비율이다.
- **예시:** 현재 v2는 465개 원자 라벨이며 기존 09 스키마가 요청했던 443개는 평가할 수 있다. 나머지 22개 안전성 감사 라벨은 다음 추출 실험에 추가해야 한다. 이 중 관계 점수의 주 분모는 semantic 관계와 별도 관계 규칙 366개이며, 중복 삭제하지 않은 numeric probe는 수치 정확도의 분모로 따로 사용한다.
- **한계:** 지원된다는 것은 정확하다는 뜻이 아니다. 단지 평가 분모에 넣을 수 있다는 의미다.

### 오류 유형 분포 (Error taxonomy)

- **의미:** 실패를 수치, 대상, 조건, 제외 항목, 설명 값, 단위, 누락, 미지원으로 나눠 개수와 비율을 집계한다.
- **예시:** `30만원 이상`을 `50만원 이상`으로 읽으면 조건 오류, `1만원`을 `2만원`으로 읽으면 수치 오류, `해외`를 `국내`로 읽으면 대상 오류다.
- **한계:** 분류는 gold의 metric/path 이름에 의존한다. 하나의 오류가 여러 의미를 동시에 훼손해도 대표 유형 하나로 집계될 수 있다.

## Retrieval Evaluation

13번 계층형 청킹 retrieval 실험은 10개 카드의 canonical fixture에서 고정 질의 30개를 대상으로 한다. 이 평가는 OCR 품질 점수가 아니라, 생성된 청크에서 카드 혜택 근거를 검색하는 품질을 측정한다. BC는 selected excerpt, IBK는 incomplete/ambiguous, 나머지 8개는 visual audit 전 full-page candidate이므로 원본 PDF 전체 범위의 검색 성능으로 일반화하지 않는다.

### 방법 구분

| method | 검색 방법 | 해석상 주의점 |
|---|---|---|
| `keyword` | 결정론적 토크나이저와 BM25 계열 점수로 질의·청크의 어휘 일치를 순위화한다. | 한국어 형태소 분석이 아니므로 조사·복합어·표기 변형에 민감하다. |
| `vector` | cache된 질의 embedding과 로컬 Chroma의 청크 embedding 유사도로 순위화한다. | semantic 유사성을 반영할 수 있지만 embedding 모델·청크 경계의 영향을 받는다. |
| `hybrid` | keyword 상위 50개와 vector 상위 50개를 RRF(reciprocal rank fusion, 상수 60)로 결합한다. | 결합이 모든 지표에서 단일 방법보다 높다는 보장은 없으며, fusion 설정에 따라 결과가 달라진다. |

### Relevance 계약

`relevant chunk`는 다음 세 조건을 **모두** 만족하는 청크다.

1. 청크 metadata의 카드가 `expected_card`와 같다.
2. 청크 metadata의 계층이 `expected_level`과 같다 (`card`, `page`, `section`, `benefit`).
3. 청크 본문에 `required_terms`의 모든 항목이 정규화 후 포함된다.

예를 들어 “LOCA LIKIT Eat 음식점 결제일 할인율은?”의 `expected_card`가 LOCA LIKIT Eat, `expected_level`이 `benefit`, `required_terms`가 `음식점`, `60%`라면, 같은 카드의 benefit 청크이면서 두 항목을 모두 포함해야 relevant다. 같은 카드의 다른 혜택 청크나 `60%`만 있는 청크는 relevant가 아니다.

### 검색 지표

### Hit@3 (높을수록 좋음)

- **비교 대상:** 질의의 `expected_card`와 각 방법의 상위 3개 청크의 카드 metadata다.
- **계산 / 통과 기준:** 상위 3개 중 expected card 청크가 하나 이상이면 질의 점수 1, 없으면 0이다. 요약은 30개 질의의 평균이다. 이 지표의 pass는 카드 라우팅 성공을 뜻한다.
- **카드 혜택 예시:** “the Orange 카드의 발급사는?”에서 상위 3개 중 Hyundai The Orange 청크가 하나라도 있으면 pass다.
- **한계:** 구현상 `expected_level`과 `required_terms`를 요구하지 않는 카드 라우팅 지표다. 따라서 엄격한 `relevant chunk` 정의와 다르며, 같은 카드의 무관한 청크도 Hit@3를 통과시킬 수 있다.

### Recall@5 (높을수록 좋음)

- **비교 대상:** 위 relevance 계약으로 만든 해당 질의의 모든 relevant chunk와 상위 5개 청크다.
- **계산 / 통과 기준:** `top 5 안의 relevant chunk 수 / relevant_chunk_count`로 계산한다. 1.0이면 모든 relevant chunk가 상위 5개에 회수된 것이다.
- **카드 혜택 예시:** “음식점 60% 할인”에 대해 해당 card·benefit·용어 계약을 만족하는 청크가 2개이고 상위 5개가 그중 1개를 찾으면 0.5다.
- **한계:** relevant 청크가 여러 개일수록 완전 회수가 어려우며, 상위 5개 밖의 유용한 근거는 반영하지 않는다. required term 기반 gold는 동의어·우회 표현의 의미 관련성을 놓칠 수 있다.

### MRR@5 (높을수록 좋음)

- **비교 대상:** 상위 5개 내에서 처음 등장한 relevant chunk의 순위다.
- **계산 / 통과 기준:** 첫 relevant chunk의 reciprocal rank(`1 / 순위`)를 사용하며, 상위 5개에 없으면 0이다. 1위면 1.0, 2위면 0.5다.
- **카드 혜택 예시:** “해외 가맹점 2% 할인”의 relevant benefit 청크가 2위에 처음 나오면 MRR@5는 0.5다.
- **한계:** 첫 정답 하나의 위치만 본다. 1위 이후에 다른 relevant chunk를 얼마나 회수했는지는 Recall@5나 nDCG@5가 더 잘 설명한다.

### nDCG@5 (높을수록 좋음)

- **비교 대상:** 상위 5개 청크의 binary relevance(위 세 조건 충족 여부)와, 같은 relevant 개수에서 가능한 이상적 순위다.
- **계산 / 통과 기준:** 각 relevant hit에 `1 / log2(순위 + 1)`의 할인 gain을 주고 ideal DCG로 나눈다. 1.0이면 가능한 relevant 결과가 가장 앞 순위에 배치된 것이다.
- **카드 혜택 예시:** “전월 실적 없이 해외 결제로 마일리지를 더 받는 혜택”의 related section 청크가 1위일 때보다 5위일 때 더 낮다.
- **한계:** 현재 relevance는 binary라 부분적으로 유용한 청크와 완전한 근거 청크를 구분하지 않는다. 5위 이후 순위와 답변 생성 품질도 평가하지 않는다.

### 13번 결과 요약

아래 값은 `retrieval_summary.csv`의 30개 고정 질의 평균이다. 단일 canonical fixture 실험 결과이며, 통계적 우열이나 서비스 운영 성능을 단정하지 않는다.

| method | Hit@3 | Recall@5 | MRR@5 | nDCG@5 |
|---|---:|---:|---:|---:|
| `keyword` | 0.9333 | 0.6167 | 0.3817 | 0.4294 |
| `vector` | 0.9667 | 0.7250 | 0.5111 | 0.5370 |
| `hybrid` | 0.9000 | 0.7250 | 0.4539 | 0.5037 |

### 결과 파일 컬럼

#### `retrieval_per_query.csv`

| 컬럼 | 설명 |
|---|---|
| `query_id` | 고정 평가 질의의 고유 ID다. |
| `category` | 질의 유형(`proper_noun`, `numeric_condition`, `semantic`)이다. |
| `query` | 검색에 입력한 자연어 질의다. |
| `method` | `keyword`, `vector`, `hybrid` 중 해당 순위를 만든 방법이다. |
| `expected_card` | 관련 청크가 속해야 하는 카드 key다. |
| `expected_level` | 관련 청크가 가져야 하는 계층(`card`, `page`, `section`, `benefit`)이다. |
| `required_terms` | relevant 판정 때 본문에 모두 존재해야 하는 정규화 전 용어 배열(JSON 문자열)이다. |
| `relevant_chunk_count` | card·level·모든 required term 조건을 동시에 충족한 gold relevant 청크 수다. |
| `hit_at_3` | 상위 3개에 expected card 청크가 하나 이상이면 1, 아니면 0이다. |
| `recall_at_5` | relevant 청크 중 상위 5개가 회수한 비율이다. |
| `mrr_at_5` | 상위 5개 내 첫 relevant 청크의 reciprocal rank이며, 없으면 0이다. |
| `ndcg_at_5` | 상위 5개의 binary relevance 순위를 ideal DCG로 정규화한 값이다. |
| `top5_chunk_ids` | 순위 상위 5개 청크 ID 배열(JSON 문자열)이다. |
| `top5_cards` | 상위 5개 청크에 대응하는 카드 key 배열(JSON 문자열)이다. |

#### `retrieval_summary.csv`

| 컬럼 | 설명 |
|---|---|
| `method` | 집계 대상 검색 방법이다. |
| `hit_at_3` | 해당 방법의 질의별 Hit@3 평균이다. |
| `recall_at_5` | 해당 방법의 질의별 Recall@5 평균이다. |
| `mrr_at_5` | 해당 방법의 질의별 MRR@5 평균이다. |
| `ndcg_at_5` | 해당 방법의 질의별 nDCG@5 평균이다. |

### Ablation 평가 보완 계약

`retrieval_ablation_per_query.csv`와 `retrieval_ablation_summary.csv`는 같은 30개 고정 질의와 동일 relevance 계약을 사용하되, 카드 라우팅과 엄격한 근거 검색을 분리해 기록한다. 따라서 기존 `Hit@3`은 이 파일에서 `card_hit_at_3`로 명시하고, evidence 지표는 `strict_evidence_*` 및 strict relevance 분모로 구분한다.

#### card_hit_at_3 (높을수록 좋음)

- **비교 대상 / 분모:** 각 평가 질의의 `expected_card`와 최상위 3개 청크의 카드 metadata를 비교한다. 모든 해당 query group 질의가 분모다(예: `all`은 30).
- **계산 / 통과 기준:** 상위 3개 중 expected card 청크가 하나 이상이면 1, 아니면 0이며 요약은 그 평균이다.
- **카드 혜택 예시:** “the Orange 카드의 발급사는?”에서 상위 3개 중 The Orange 카드의 청크가 있으면 통과한다.
- **한계:** card level이나 required term이 맞는지는 보지 않는다. 같은 카드의 무관한 청크로도 통과할 수 있어 evidence retrieval 성공을 뜻하지 않는다.

#### strict_evidence_hit_at_3 (높을수록 좋음)

- **비교 대상 / 분모:** `expected_card` + `expected_level` + 모든 `required_terms`를 충족하는 strict relevant chunk와 상위 3개를 비교한다. strict relevant chunk가 있는 질의만 분모에 넣는다.
- **계산 / 통과 기준:** 상위 3개 안에 strict relevant chunk가 하나 이상이면 1, 없으면 0이다.
- **카드 혜택 예시:** “LOCA LIKIT Eat 음식점 결제일 할인율” 질의에서 LOCA 카드의 `benefit` 청크가 `음식점`과 `60%`를 모두 포함한 채 상위 3개에 있으면 통과한다.
- **N/A 조건과 한계:** 구성에서 gold strict evidence level 청크가 전혀 없으면 `N/A_no_relevant_chunk_in_configuration`이며 strict evidence 지표의 분모에서 제외한다. relevance는 여전히 용어 포함 기반이라 동의어·부분적으로 유용한 근거를 놓칠 수 있다.

#### Evidence Recall@5 · MRR@5 · nDCG@5

이 세 지표의 계산은 앞선 Retrieval Evaluation의 Recall@5·MRR@5·nDCG@5와 같지만, ablation에서는 strict relevant chunk가 존재하는 질의만 각각의 `*_denominator`에 포함한다. strict relevant chunk가 없으면 모두 `N/A`이며 0점으로 처리하지 않는다.

- **Recall@5:** strict relevant chunk 중 top 5가 회수한 비율이다. 예를 들어 ‘해외 가맹점 2%’ benefit 근거가 2개이고 1개만 top 5에 있으면 0.5다. 모든 적합 근거를 요구하므로 relevant 청크 수가 많을수록 낮아질 수 있다.
- **MRR@5:** 첫 strict relevant chunk의 reciprocal rank다. 예를 들어 관련 section이 2위면 0.5, top 5 밖이면 0이다. 첫 근거 뒤의 추가 근거 회수는 반영하지 않는다.
- **nDCG@5:** top 5의 strict binary relevance를 순위 할인해 ideal DCG로 정규화한다. 예를 들어 같은 혜택 근거가 1위보다 5위에 있으면 더 낮다. 부분 relevance와 5위 이후 결과는 반영하지 않는다.

#### card_diversity_at_5

현재 ablation CSV 헤더에는 `card_diversity_at_5` 컬럼이 없다. duplicate control은 설정(`card_max_1`, `card_max_2` 등)과 `top5_cards`로 결과 구성을 감사하지만, 고유 카드 수를 별도 지표·분모·집계값으로 저장하지 않는다. 따라서 현 파일로는 card diversity를 수치로 비교하거나 통과 기준을 정의할 수 없다.

#### Ablation 결과와 과적합 주의

현재 `retrieval_ablation_summary.csv`의 `final / rrf_0.7_0.3_alias_two_stage_n3_card2 / all` 행은 card_hit_at_3 0.9000 (분모 30), strict_evidence_hit_at_3 0.7000 (30), Recall@5 0.6250 (30), MRR@5 0.6500 (30), nDCG@5 0.6116 (30)이다. 이는 현재 파일과 대조한 기록값일 뿐이다. 모든 configuration 탐색과 final 선택이 동일 30질의에서 이뤄졌으므로 **동일 30질의 tuning 과적합 위험**이 있으며, 독립 holdout 검증 전에는 일반화 성능이나 최종 우열로 해석하지 않는다.

#### Ablation 결과 파일 컬럼

`retrieval_ablation_per_query.csv`는 한 configuration·query group·질의의 상세 행이며, `retrieval_ablation_summary.csv`는 그 행의 평균과 각 지표 분모를 기록한다.

| 파일 | 컬럼 | 설명 |
|---|---|---|
| per-query / summary | `family` | 실험군 유형(예: `weighted_rrf`, `hierarchy`, `two_stage_evidence`, `final`)이다. |
| per-query / summary | `configuration` | 해당 실험군의 구체 설정 이름이다. |
| per-query / summary | `query_group` | 집계·평가한 질의 하위집합(`all`, `evidence`, `proper_noun_or_card`)이다. |
| per-query | `query_id`, `query`, `category` | 고정 질의 ID·원문·유형이다. |
| per-query | `expected_card`, `expected_level` | strict relevance에 사용하는 정답 카드·청크 계층이다. |
| per-query / summary | `card_hit_at_3` | 질의별 0/1 또는 그 평균이다. |
| per-query / summary | `strict_evidence_hit_at_3`, `recall_at_5`, `mrr_at_5`, `ndcg_at_5` | strict relevance 기반 질의별 값 또는 available 행 평균이다. per-query에서는 strict relevant가 없을 때 비어 있다. |
| summary | `card_hit_at_3_denominator`, `strict_evidence_hit_at_3_denominator`, `recall_at_5_denominator`, `mrr_at_5_denominator`, `ndcg_at_5_denominator` | summary 전용 컬럼이다. 해당 지표 평균에 실제 포함된 질의 수이며, card hit은 group의 전체 질의 수, strict evidence 지표는 N/A를 제외한 수다. |
| per-query | `strict_metric_status` | strict 지표의 `available` 또는 `N/A_no_relevant_chunk_in_configuration` 상태다. |
| per-query | `strict_relevant_chunks` | 현재 구성·strict 계약에서 평가 가능한 relevant 청크 수다. |
| per-query | `top5_chunk_ids`, `top5_cards`, `top5_levels` | 상위 5개 청크의 ID·카드 key·계층 배열(JSON 문자열)로, 순위와 다양성의 감사 근거다. |

### True two-stage 평가 계약

기존 `existing_rrf_filtered`는 전체 RRF 순위의 필터링 결과이고, true two-stage는 별도 계약이다. stage 1은 10개 `card` 청크에서 cached vector로 Top-3 후보 카드를 고르고, stage 2는 그 후보 카드의 `section`+`benefit` subset에서 vector 거리, BM25, RRF를 새로 계산한다.

- **`stage1_card_hit_at_1` / `stage1_card_hit_at_3`:** 카드 전용 stage 1의 top 1/3에 expected card가 있으면 1이다. true-stage 구성에만 적용하며, full baseline·기존 필터 구성은 N/A다. 예: ‘the Orange 발급사’에서 10개 card 중 현대 카드가 top 3에 있으면 Hit@3 pass다. stage 1이 틀리면 stage 2는 정답 카드 근거를 복구할 수 없다.
- **`stage1_oracle_ceiling`:** 10개 stage1 card corpus에 expected card의 card chunk가 존재하면 1이다. 현재 카드당 card chunk가 하나라 구조적으로 1.0이며, 모델의 검색 품질 상한을 뜻하지 않는다.
- **`all` vs `evidence_question`:** `all`은 card 질문 10개는 stage1 card ranking, evidence 질문 20개는 stage2 ranking을 섞어 평균낸다. 따라서 evidence retrieval 성능 판단에는 `evidence_question`만 사용한다. `card_question`은 card 질문 10개만, `evidence_question`은 numeric·semantic 근거 질문 20개만의 평균이다.

`retrieval_true_two_stage_per_query.csv` 새 컬럼은 `configuration`, `query_id`, `question_group`, `category`, `query`, `expected_card`, `expected_level`, `stage1_used`, `stage1_card_hit_at_1`, `stage1_card_hit_at_3`, `stage1_oracle_ceiling`, `card_hit_at_3`, `strict_evidence_hit_at_3`, `recall_at_5`, `mrr_at_5`, `ndcg_at_5`, `top5_chunk_ids`, `top5_cards`, `top5_levels`이다. `retrieval_true_two_stage_summary.csv`는 여기에 각 지표와 대응하는 `*_denominator`를 집계한다. N/A는 stage1을 쓰지 않는 구성의 stage1 지표에 적용되며 summary에서는 분모 0/null이다.

## 반복 실행 통계

### 평균, 표준편차, 최솟값, 최댓값

- **의미:** 같은 모델·프롬프트·페이지·옵션을 여러 번 OCR했을 때 각 지표의 중심과 흔들림을 본다.
- **예시:** Luna의 핵심 수치 정확도가 98.0%, 99.0%라면 평균은 98.5%다. 두 값의 표준편차가 작을수록 결과가 반복 실행에서 안정적이다.
- **해석:** 평균이 높아도 표준편차가 크면 운영 결과가 흔들릴 수 있다. 반복 수가 2회면 표본이 작으므로 확정적 우열이 아니라 재검증 후보로 해석한다.

## 수치 오류 발생 단계 귀속

귀속은 성능 점수가 아니라 실패 원인을 찾는 감사 방법이다. `gold → OCR 문맥 → 구조화 JSON`을 순서대로 비교한다.

### OCR 발생 오류

- **의미:** OCR 문맥의 수치가 gold와 다르고 JSON이 그 오독 또는 누락을 그대로 따른 경우다.
- **예시:** 정답 `월 25만원` → OCR `월 30만원` → JSON `300000`이면 OCR 단계 오류다.
- **한계:** 같은 숫자가 문서의 다른 위치에 있을 수 있으므로 전체 텍스트 숫자 검색만으로 판정하지 않고 해당 혜택 문맥을 확인해야 한다.

### JSON 구조화 발생 오류

- **의미:** OCR 문맥에는 정답 수치와 관계가 있지만 JSON 필드 누락, 행·열 연결 또는 단위 정규화가 틀린 경우다.
- **예시:** OCR 표가 `40만원 이상 → 5천원`, `70만원 이상 → 1만원`인데 JSON 한도가 `400000`, `700000`이면 실적 기준을 한도에 연결한 구조화 오류다.
- **한계:** OCR의 표 구조가 심하게 무너졌다면 OCR과 구조화 양쪽 책임이 섞일 수 있어 사람이 근거 문맥을 확인해야 한다.

### 표 빈칸 의미 규칙

- **의미:** OCR은 빈칸 또는 `-`를 보존했지만 gold가 해당 셀을 0원으로 해석한 경우다. OCR 오독이 아니라 문서별 스키마 해석 규칙 문제다.
- **예시:** 가족카드 기본연회비가 `-`이고 gold가 `0`이면 JSON의 `null`을 0으로 바꿀 명시 규칙이 필요하다.
- **한계:** 모든 빈칸이 0은 아니다. 표 헤더와 열 의미가 0원을 뜻한다고 확정된 필드에만 적용한다.

## 아직 미포함인 지표

- PDF 좌표 기반 표 헤더·행·셀 경계 정확도
- 필드별 부분 일치율
- 읽기 순서 정확도(LCS 등)
- semantic fact의 근거 문장·좌표 일치도
- 자유 형식 관계 추출에서의 대상·조건·값 tuple 정확도

이 지표가 새로 구현되면 위 형식으로 이 문서와 해당 HTML 보고서의 지표 해설에 함께 추가한다.
