# 노트북 실사용 평가지표 안내서

이 문서는 `notebooks/01`부터 `notebooks/40`까지의 코드 셀, 저장된 결과를 읽는 셀, 그리고 계산을 맡는 Python helper에서 **실제로 계산하거나 검사하는 항목**을 정리한다. 과거 운영 OCR 검증기의 버전별 계약은 포함하지 않는다. 수치의 우열이나 운영 채택 여부를 판정하는 문서도 아니다.

표의 예시는 계산을 이해하기 위한 설명용이며 실험 결과값이 아니다. `gold`는 사람이 정한 정답, `relevant`는 관련 있다고 표시한 정답 항목, `candidate`는 최종 순위를 정하기 전 후보, `payload`는 답변 모델에 전달한 근거를 뜻한다. `macro`는 질문·카드별 점수를 평균 내고, `micro`는 전체 분자와 분모를 먼저 합쳐 계산한다.

## 먼저 읽을 점

- 같은 `Recall@k`라도 분모가 다르면 같은 지표가 아니다. 이 문서에서는 `gold OCR 토큰`, `정답 청크`, `정답 카드`, `근거 묶음`을 반드시 나누어 적는다.
- `@k`는 앞에서부터 k개만 본다는 뜻이다. 각 노트북이 정한 k를 그대로 쓴다. 공통 통과선은 없다.
- 평균은 대체로 질문 또는 페이지별 값의 산술평균이다. 분모가 바뀐 청킹 비교에서는 Recall과 nDCG가 진단용일 수 있으므로 Hit과 MRR도 함께 본다.
- `미측정`은 0이 아니다. 실행하지 않은 단계, 없는 사용량, 비어 있는 분모에는 값을 만들지 않는다.
- 39번의 frozen scorer는 원문 인용에 대한 어휘 기반 대용 검사다. 새 18개 질문의 `calculations`는 모두 빈 목록이며, 등록된 합산식의 `percent_supported` 회귀가 최소 퍼센트 요구 전체를 해결했다는 뜻은 아니다. 40번은 108개 중 28개를 완료했으며 80개는 메모리 부족으로 미실행이다.

## 품질 지표

### OCR 원문과 구조화 추출

|지표|의미와 계산 대상·분모|방향·카드 혜택 예시|핵심 한계와 실제 출처|
|---|---|---|---|
|CER|문자 편집 거리 ÷ 정규화한 gold 문자 수. 페이지별 OCR 텍스트를 사람 gold 전사와 비교한다.|낮을수록 좋다. gold의 `전월 30만원`을 `전월 50만원`으로 읽으면 거리와 CER가 커진다.|글자 순서와 공백 정규화 방식에 민감하며 혜택의 대상·조건 연결은 보지 않는다. [02 셀 16](../notebooks/02_pdf_text_layer_check.ipynb), [07 셀 8](../notebooks/07_goldset_vision_upstage_comparison.ipynb), [08 셀 4](../notebooks/08_ocr_model_runtime_comparison.ipynb), [12 evaluator](../notebooks/12_clean_end_to_end_evaluator.py)|
|WER|단어 편집 거리 ÷ gold 단어 수. 02번의 사람이 전사한 페이지에서 OCR 단어열을 비교한다.|낮을수록 좋다. `편의점 10% 할인`의 한 단어가 빠지면 오류가 늘어난다.|표 읽기 순서가 달라도 커질 수 있으며 숫자의 의미 관계는 보장하지 않는다. [02 셀 16](../notebooks/02_pdf_text_layer_check.ipynb)|
|Readable ratio — 읽을 수 있는 문자 비율|공백을 없앤 페이지 문자열에서 허용 문자 수 ÷ 전체 문자 수. 높을수록 문자 깨짐이 적다. `avg_readable_ratio`는 페이지별 비율 평균이다.|공백 제외 100자 중 허용 문자가 90자면 0.9다.|허용 문자는 영숫자·한글·지정 기호이며 올바른 혜택인지는 검사하지 않는다. [02 `readable_ratio`](../notebooks/02_pdf_text_layer_check.ipynb)|
|Usable page ratio — 사용 가능한 페이지 비율|문자 수와 readable ratio 기준을 통과한 페이지 수 ÷ 전체 페이지 수. 높을수록 텍스트 레이어를 사용할 페이지가 많다.|10페이지 중 8페이지가 통과하면 0.8이다.|02의 사용 가능 조건은 공백 제외 20자 이상이고 readable ratio 0.70 이상이다. 정확한 OCR 보장은 아니다. [02 `is_usable_page`](../notebooks/02_pdf_text_layer_check.ipynb)|
|문자 수·이상 문자 진단|페이지 최소·최대 문자 수와 깨진 문자·제어 문자 목록을 기록한다. 이상 문자는 적을수록 좋으며 문자 수 자체에는 공통 최적값이 없다.|혜택 페이지에서 깨진 문자 `�`가 발견되면 이상 문자 목록에 들어간다.|짧은 정상 페이지도 있어 길이만으로 오류를 판정하지 않는다. [02](../notebooks/02_pdf_text_layer_check.ipynb)|
|Word sequence similarity — 단어 순서 유사도|정답 단어열과 추출 단어열의 `SequenceMatcher.ratio()` 값. 매칭 원소 수를 M, 두 단어열 길이를 A·B라 하면 `2M/(A+B)`다. 높을수록 순서가 비슷하다.|같은 혜택 단어를 모두 읽어도 표의 열 순서가 바뀌면 점수가 낮아질 수 있다.|최소 편집 거리인 CER·WER와 다르다. [02 진단 셀](../notebooks/02_pdf_text_layer_check.ipynb)|
|토큰 Precision·Recall·F1|정규화 토큰의 다중집합 교집합을 쓴다. Precision 분모는 예측 토큰 수, Recall 분모는 gold 토큰 수, F1은 둘의 조화평균이다. 07·08·12번은 페이지 단위 OCR과 gold 원문을 비교한다.|모두 높을수록 좋다. 원문에 있는 `월 1만원 한도`를 읽지 못하면 Recall이, 원문에 없는 `월 2만원`을 넣으면 Precision이 낮아진다.|순서와 혜택 연결을 보지 않는다. 양쪽에 같은 오류가 있어도 교차 비교 F1은 높을 수 있다. [07 셀 2·8](../notebooks/07_goldset_vision_upstage_comparison.ipynb), [08 셀 4](../notebooks/08_ocr_model_runtime_comparison.ipynb), [12 evaluator](../notebooks/12_clean_end_to_end_evaluator.py)|
|숫자 Precision·Recall·F1 — 다중집합|숫자 토큰 Counter의 교집합 수를 사용한다. Precision은 예측 숫자 수로, Recall은 정답 숫자 수로 나누고 F1은 둘의 조화평균이다. 높을수록 좋다.|정답 숫자 4개 중 3개를 맞추고 예측 숫자가 5개면 Precision 0.6, Recall 0.75, F1 약 0.667이다.|정규화·숫자 패턴이 노트북마다 다르며 단위나 대상 관계는 보장하지 않는다. [07](../notebooks/07_goldset_vision_upstage_comparison.ipynb), [08](../notebooks/08_ocr_model_runtime_comparison.ipynb), [12 `multiset_scores`](../notebooks/12_clean_end_to_end_evaluator.py)|
|Number recall·Numeric token overlap — 숫자 집합 비교|02의 `number_recall`은 PDF 숫자 집합과 Vision 숫자 집합 교집합 크기 ÷ PDF 숫자 집합 크기다. `numeric_token_overlap`은 두 숫자 집합의 Jaccard다. 높을수록 두 출력이 비슷하다.|PDF 숫자 집합이 {10, 30}, Vision이 {10, 50}이면 Recall 1/2, Jaccard 1/3이다.|중복 숫자는 한 번만 세고 PDF 텍스트도 오독할 수 있다. 정답 gold 기반 Counter 지표와 다르다. [02 `validate_page`·진단 셀](../notebooks/02_pdf_text_layer_check.ipynb)|
|Exact text match|정규화한 gold 페이지 문자열과 OCR 페이지 문자열이 완전히 같은지의 0 또는 1이다. 평균을 내면 정확히 같은 페이지의 비율이다.|높을수록 좋다. `전월 30만원 이상`까지 모든 정규화 문자열이 같아야 1이다.|사소한 표현·순서 차이도 0이며, 1이어도 표의 시각 배치 평가는 아니다. [07 셀 8](../notebooks/07_goldset_vision_upstage_comparison.ipynb), [08 셀 4](../notebooks/08_ocr_model_runtime_comparison.ipynb)|
|토큰 Jaccard|두 OCR 또는 PDF 텍스트 레이어의 토큰 집합 교집합 ÷ 합집합이다. gold 분모가 아닌 두 출력의 합집합이 분모다.|높을수록 두 출력이 비슷하다. 두 출력 모두 `편의점`, `10%`를 포함하면 높아진다.|정확도 지표가 아니다. 둘이 같은 오독을 해도 높고, 표 표현 차이로 낮을 수 있다. [02 셀 6·12](../notebooks/02_pdf_text_layer_check.ipynb)|
|03번 manual OCR 비교 결과|마지막 셀의 저장 출력은 Vision·Paddle·Upstage·Claude 행에 `cer`, `wer`, `content_recall`, `content_precision`, `sequence_similarity`, `field_value_recall`, `field_set_exact_match`을 표시한다. `claude_manual_metrics.json`에는 같은 summary 키가 있다.|직접 검수 gold에서 `10%`가 누락된 엔진은 content recall이 낮아질 수 있다.|과거 `manual_full_and_field_metrics.json`은 현재 복사본에 없어 셀 6의 원 계산 함수·정확한 분모는 확실하지 않음이다. 셀 5의 더 넓은 목록은 계획 명세다. [03 셀 5·6](../notebooks/03_ocr_engine_comparison.ipynb), [Claude summary](../notebooks/data/03_ocr_engine_comparison/claude_manual_metrics.json)|
|04번 방향별 token recall·숫자 차이|`token_recall(reference, candidate)`은 Counter 교집합 수 ÷ reference token 수다. `vision_to_upstage_token_recall`은 Upstage를 reference로, `upstage_to_vision_token_recall`은 Vision을 reference로 쓴다. `vision_only_numeric`, `upstage_only_numeric`은 숫자 set 차집합 목록이다.|Upstage의 `30만원`을 Vision도 읽으면 첫 Recall 분자에 들어간다.|방향을 바꾸면 분모도 바뀌므로 두 값을 같은 재현율로 합치지 않는다. [04 셀 3](../notebooks/04_vision_upstage_validation.ipynb)|
|06번 page validation routing|페이지별 active engine 누락, 최소 비교 engine 수 부족, fact set 불일치가 하나라도 있으면 `needs_review`, 모두 없으면 `auto_candidate`다. auto candidate·review queue의 건수를 실제 출력한다.|한 engine이 빠지거나 `전월 실적` fact set이 다르면 needs review다.|gold 정확도 지표가 아니라 교차검증 라우팅 상태다. [06 `validate_page`](../notebooks/06_ocr_validation_pipeline.ipynb)|
|OCR 간 agreement F1·숫자 agreement F1|OpenAI OCR과 Upstage OCR의 토큰 또는 숫자 토큰 다중집합 F1이다. gold가 아닌 두 엔진 출력끼리 비교한다.|높을수록 두 엔진이 일치한다. 두 엔진이 `전월 30만원`을 읽으면 숫자 agreement가 높다.|자동 통과 후보를 찾는 보조 검사일 뿐 정답 보장은 아니다. [07 셀 9](../notebooks/07_goldset_vision_upstage_comparison.ipynb)|
|필드 exact match·필드 present|gold 구조화 필드마다 정규화 JSON 값이 같은지, 예측값이 null이 아닌지를 센다. critical 필드는 별도 집계한다.|정확 일치와 present 비율은 높을수록 좋다. `할인율=10%`와 `월 한도=5000원`이 gold와 같아야 exact다.|필드 단위라 대상과 조건의 관계 전체를 뜻하지 않으며, gold 범위 밖 정보는 평가하지 않는다. [07 셀 11·12](../notebooks/07_goldset_vision_upstage_comparison.ipynb), [08 셀 5](../notebooks/08_ocr_model_runtime_comparison.ipynb)|
|fact exact match·null prediction rate|09번은 카드 fact 하나의 예상 구조 전체가 같은지와 예측이 null인 비율을 계산한다. 분모는 gold fact 수다.|exact는 높을수록, null 비율은 목적에 따라 해석한다. `커피 2%` fact 전체가 맞으면 exact 1이다.|null은 보수적 거절일 수 있어 단독으로 오류율이 아니다. [09 helper `evaluate`](../notebooks/09_semantic_repeatability_runner.py)|
|Numeric/String Leaf·Unit Accuracy — 숫자·문자열·단위 정확도|숫자·문자열은 fact 내부의 해당 정답 값 수가 분모다. Unit은 정답에 non-null 단위가 있는 fact 수가 분모다. 같은 위치의 값 또는 단위를 맞춘 수를 각각 나눈다.|높을수록 좋다. `300000`, `할인`, `%`를 각각 맞추면 해당 항목이 오른다.|부분 일치는 관계 전체 일치가 아니다. 이 코드는 분모가 없으면 `max(1, 분모)`로 0을 낸다. [09 `evaluate`](../notebooks/09_semantic_repeatability_runner.py)|
|critical fact 판정|10·11번은 사람이 정한 critical fact의 대상·조건·수치·단위 등을 원문 및 추출 결과와 대조해 match, mismatch, unresolved 같은 판정을 남긴다.|match가 늘고 mismatch·unresolved가 줄면 바람직하다. `주유 5%`에 `전월 30만원`이 붙는지처럼 관계를 본다.|일부 노트북의 저장 결과는 진단 표이며 통계적 정확도나 사람 오류율을 뜻하지 않는다. [10](../notebooks/10_relational_critical_fact_evaluation.ipynb), [11](../notebooks/11_numeric_error_attribution.ipynb), [12 evaluator](../notebooks/12_clean_end_to_end_evaluator.py)|
|10번 atomic relation·numeric 지표|`atomic_relation_exact_rate`는 supported relation-primary fact, `numeric_value_accuracy`는 supported numeric fact, `unit_accuracy`는 unit-evaluable fact가 각각 분모다. `relation_group_exact_rate`와 `table_row_relation_exact_rate`는 각각 relation group과 table-row group 분모에서 모든 원자 fact 일치를 센다.|표의 `주유 5% / 월 1만원`에서 수치만 맞아도 numeric value는 1일 수 있지만, 같은 행의 단위나 관계가 틀리면 group exact는 0이다.|`unsafe_mismatch_rate`·`missing_prediction_rate`의 분모는 relation-primary fact, `unsafe_numeric_mismatch_rate`는 numeric fact다. 각 반복 결과는 mean, population stddev, min, max로 집계하며 단일 run의 stddev는 null이다. [10 run summary](../notebooks/10_relational_critical_fact_evaluation.ipynb)|
|12번 OCR macro·micro|`macro_cer`는 카드별 CER 평균, `micro_cer`는 모든 편집 거리 합 ÷ 모든 gold 문자 수다. token도 카드 평균 macro F1과 전체 토큰을 합친 micro Precision·Recall·F1을 분리한다.|짧은 카드와 긴 카드의 영향이 macro와 micro에서 달라질 수 있다.|한 값만 보고 문서 길이 편향을 숨기면 안 된다. [12 evaluator](../notebooks/12_clean_end_to_end_evaluator.py)|
|12번 Present·Null — 출력 키 존재·빈 값 비율|Present는 gold label ID가 예측 JSON에 있는지, Null은 키가 있으면서 값이 null인지다. 각각 해당 label 수가 분모다. Present는 높을수록 출력 범위가 넓다.|`할인율` 키가 존재하되 null이면 Present=1, Null=1이다. 키 자체가 없으면 둘 다 0이다.|Null은 보수적 출력일 수 있어 단순 오류율이 아니다. 07·08의 non-null present와 정의를 구분한다. [12 `structured_rows`](../notebooks/12_clean_end_to_end_evaluator.py)|
|12번 Field Value Exact — 필드 값 일치율|정규화 후 gold와 같은 필드 수 ÷ 평가 가능한 field label 수. 높을수록 좋다.|gold가 `커피 할인`인데 `주유 할인`이면 0이다.|값의 null·누락도 일치 실패다. [12 `structured_rows`](../notebooks/12_clean_end_to_end_evaluator.py)|
|12번 Numeric Normalized/Surface/Unit Exact — 숫자 정규값·표면·단위 일치율|numeric label에서 정규 수치, 표면 문자열, 단위를 각각 gold와 비교한다. 각 평가 가능 label 수가 분모이며 높을수록 좋다.|gold `30만원`, 정규값 300000, 단위 원에 대해 정규값만 맞고 표면이 `300000원`이면 두 점수가 다를 수 있다.|수치가 맞아도 단위나 혜택의 관계가 맞는지는 별도다. [12](../notebooks/12_clean_end_to_end_evaluator.py)|
|12번 Table Header/Rows Exact — 표 머리글·전체 행 일치율|머리글 배열과 전체 행 배열이 정규화 후 gold와 같은지를 따로 센다. 분모는 평가 가능한 표 label 수이며 높을수록 좋다.|표 머리글은 맞아도 한 행의 월 한도가 다르면 header=1, rows=0이다.|개별 셀 정확도를 평균내는 지표가 아니다. `table_row_relation_accuracy`는 행 위치 근거가 없어 미측정이며 추정 표 연결은 통과 지표가 아니다. [12](../notebooks/12_clean_end_to_end_evaluator.py)|
|12번 Scorable Atomic Relation/Numeric/Unit Accuracy — 평가 가능한 관계·수치·단위 정확도|각각 관계 평가 가능한 fact, 그중 숫자값 fact, 단위 비교 가능한 fact에서 올바른 항목 수 ÷ 해당 항목 수다. 높을수록 좋다.|10%가 맞아도 다른 혜택에 연결되면 관계 정확도는 떨어질 수 있다.|분모가 서로 다르며 평가 불가능 fact는 제외한다. 단위 검사 대상의 단위 누락은 오류로 센다. [12 `summarize_critical`](../notebooks/12_clean_end_to_end_evaluator.py)|
|12번 Relation Group Exact — 관계 묶음 전체 일치율|평가 가능한 관계 묶음 중 모든 원자 관계가 맞는 묶음 수 ÷ 해당 묶음 수. 높을수록 좋다.|커피 할인율·실적·한도의 세 관계 중 한도가 틀리면 묶음 점수는 0이다.|일부 fact의 값 정확도와 다르다. [12 `summarize_critical`](../notebooks/12_clean_end_to_end_evaluator.py)|
|12번 Missing·Unsafe Mismatch — 누락·위험한 불일치 수|원자 fact의 missing 및 unsafe mismatch 판정 건수를 센다. 낮을수록 좋다.|할인율이 사라지면 missing, 원문과 다른 할인율을 출력하면 unsafe mismatch로 진단할 수 있다.|건수이며 비율이 아니다. 평가 가능 범위·엔진 수와 함께 본다. [12](../notebooks/12_clean_end_to_end_evaluator.py)|
|12번 Card Macro — 카드별 정확도 평균|`card_macro_[metric]`는 각 카드의 평가 가능한 label 정확도를 구한 뒤 카드별로 평균낸다. 해당 exact 지표는 높을수록 좋다.|label이 2개인 카드와 20개인 카드가 평균에서 같은 카드 1개씩의 비중을 갖는다.|모든 label을 한꺼번에 나눈 정확도와 다르다. [12 `summarize_structured`](../notebooks/12_clean_end_to_end_evaluator.py)|

### 검색과 청킹: 정답 청크, 카드, 근거 묶음은 별도 분모

|지표|의미와 계산 대상·분모|방향·카드 혜택 예시|핵심 한계와 실제 출처|
|---|---|---|---|
|카드 Hit@k|앞 k개 반환 청크 또는 카드 중 `expected_card` 또는 `gold_cards`가 하나라도 있으면 1이다. 질문별 0·1의 평균 분모는 정답 카드가 있는 질문 수다.|높을수록 좋다. 커피 혜택 질문에 정답 카드가 3위 안이면 1이다.|그 카드의 올바른 혜택 청크를 찾았다는 뜻은 아니다. `01`은 카드 id, 13~24는 청크 메타데이터의 카드 키, 32~40은 반환 카드 키를 쓴다. [01 셀 3](../notebooks/01_rag_evaluation.ipynb), [13](../notebooks/13_hierarchical_chunking_retrieval.ipynb), [32 `evaluate_retrieval`](../notebooks/32_search_experiment.py)|
|Strict evidence Hit@k|앞 k개 청크에 expected card, expected level, required terms 계약을 모두 만족하는 정답 청크가 하나라도 있는지다. 분모는 해당 evidence 질문 수다.|높을수록 좋다. 정답 카드라도 `연회비` 청크만 나오고 `편의점 10%` 청크가 없으면 0이다.|13번 초기 `hit_at_3`은 expected-card top 3의 카드 Hit이며 strict가 아니다. 후속의 명시적 `strict_evidence_hit_at_3`과 15·16의 같은 계약만 이 정의로 읽는다. [13](../notebooks/13_hierarchical_chunking_retrieval.ipynb), [15](../notebooks/15_korean_morphology_retrieval_ablation.ipynb), [16](../notebooks/16_normalized_rrf_weight_ablation.ipynb)|
|청크 Recall@k|앞 k개 반환 청크와 gold relevant chunk 집합의 교집합 수 ÷ gold relevant chunk 수다. 13~23의 evidence 또는 answer-bearing view에서 쓴다.|높을수록 좋다. 관련 청크 4개 중 3개를 top 5에서 찾으면 0.75다.|청킹, corpus, relevance unit이 달라지면 분모가 바뀐다. 그런 비교의 Recall은 진단용이며 절대 품질 비교가 아니다. [13](../notebooks/13_hierarchical_chunking_retrieval.ipynb), [18](../notebooks/18_answer_bearing_grouped_retrieval_reevaluation.ipynb), [23](../notebooks/23_structural_chunking_reranker_comparison.ipynb)|
|카드 Recall@3 — `anchor_recall_at_3`|반환 카드 집합과 `gold_cards` 집합 교집합 수 ÷ 정답 카드 수. 32의 계산 함수를 33·34·36 등도 재사용한다. 높을수록 좋다.|조건에 맞는 카드 2장 중 1장을 top 3에 내면 0.5다.|40의 `retrieval_metrics`에는 이 항목이 없다. 카드가 맞아도 혜택 근거가 충분한지는 별도다. [32 `evaluate_retrieval`](../notebooks/32_search_experiment.py), [36](../notebooks/36_comparison.py)|
|MRR@k — Mean Reciprocal Rank, 첫 정답 순위의 역수 평균|질문별 첫 정답 순위를 r이라 할 때 `RR=1/r`, k개 안에 없으면 0이다. MRR은 질문별 RR의 평균이다. 높을수록 좋다. 01은 카드, 13~23은 청크 또는 별도 카드 지표, 32~40은 정답 카드 순위를 쓴다.|세 질문의 첫 정답이 1위·2위·없음이면 MRR은 `(1+0.5+0)/3=0.5`다.|저장된 질문별 `mrr` 값은 실제로 RR이다. 첫 정답 뒤의 다른 정답은 반영하지 않으며 청크와 카드 순위를 섞어 비교하면 안 된다. [01](../notebooks/01_rag_evaluation.ipynb), [13](../notebooks/13_hierarchical_chunking_retrieval.ipynb), [23](../notebooks/23_structural_chunking_reranker_comparison.ipynb), [32](../notebooks/32_search_experiment.py)|
|nDCG@k — Normalized Discounted Cumulative Gain, 순위 할인 이득의 정규화값|`DCG=Σ gain(r)/log2(r+1)`, `nDCG=DCG/IDCG`. IDCG는 정답을 이상적인 순서에 놓은 DCG다. 01은 `gain=2^등급−1`, 13~23의 이진 평가에서는 관련 청크 gain=1, 나머지=0이다. 높을수록 좋다.|관련 청크 2개가 1·3위면 nDCG@3은 `(1+1/log2(4))/(1+1/log2(3))`, 약 0.92다. 둘 다 1·2위면 1이다.|정답 등급·청크 경계·평가 단위가 달라지면 분모도 달라진다. [01 `ndcg_at_k`](../notebooks/01_rag_evaluation.ipynb), [13](../notebooks/13_hierarchical_chunking_retrieval.ipynb), [16](../notebooks/16_normalized_rrf_weight_ablation.ipynb), [23](../notebooks/23_structural_chunking_reranker_comparison.ipynb)|
|Witness Recall@3 — 32번|분모는 `n=len(gold_cards)`다. `witness_groups[card]`는 각 AND 요구에 대한 대체 fact id 묶음이다. 길이가 같은 plan group을 찾고, 각 요구마다 대체 id 중 하나가 `fact_witnessed`를 만족해야 그 카드에 1을 더한다.|높을수록 좋다. `커피`와 `10%`라는 두 AND 요구가 각각 같은 카드 evidence에서 필요한 fact로 확인되면 카드 A에 1을 센다.|전체 witness group 수는 분모가 아니며, 카드별 모든 AND 요구를 만족해야 한다. card-level Hit 또는 card Recall과 다르다. [32 `evaluate_retrieval`](../notebooks/32_search_experiment.py), [34 report](../notebooks/34_render_bundle_report.py)|
|Witness Recall@3 — 40번|분모는 `witness_groups` 딕셔너리의 카드 항목 수 `count`다. 각 카드의 중첩 group 중 하나에서 target·action·value 핵심 조각이 evidence에 모두 있으면 1을 더한다.|높을수록 좋다. 동일 카드 evidence에 `커피`·`할인`·`10%`가 있으면 해당 카드 항목 1개를 센다.|gold 카드 수나 중첩 group 수가 분모가 아니며, 원문 의미 판정이 아닌 문자열 기반 진단이다. [40 `retrieval_metrics`](../notebooks/40_new_query_comparison.py)|
|strict prefilter anchor retention|구조화 조건으로 사전 필터링한 eligible 카드 집합 안에 남은 gold 카드 수 ÷ gold 카드 수다. 조건 질문에서만 계산한다.|높을수록 좋다. `전월 30만원 이하` 조건으로 거른 뒤에도 정답 카드가 남아야 한다.|필터 뒤의 순위 품질이나 답변 품질은 보지 않는다. [32 `evaluate_retrieval`](../notebooks/32_search_experiment.py)|
|paired wins·losses·ties|같은 질문에서 후보와 기준의 MRR 또는 nDCG가 더 높음·낮음·같음을 세는 비교 표다. 비율 자체가 아니라 질문별 변화 건수다.|wins가 많고 losses가 적을수록 후보가 해당 측정치에서 유리하다. 한 질문의 정답 혜택 청크가 2위에서 1위가 되면 MRR win이다.|평균 개선과 다른 결론이 날 수 있고, 개발셋 반복 탐색 결과는 확증 결과가 아니다. [16](../notebooks/16_normalized_rrf_weight_ablation.ipynb), [21](../notebooks/21_current_chunking_prebranch_reranker_evaluation.ipynb), [23](../notebooks/23_structural_chunking_reranker_comparison.ipynb)|

### 18·19번의 relevance 단위와 중복 진단

|지표|의미·계산(분모·방향 포함)|예시|사용처·한계|
|---|---|---|---|
|18번 view별 Hit@3·Recall@5·MRR@5·nDCG@5|공식은 위 청크 지표와 같지만 relevance unit이 다르다. `strict_raw`는 strict 청크, `answer_bearing_raw`는 level을 완화한 answer-bearing 청크, `*_exact_doc_dedup`은 카드와 정규화 문서가 같은 청크를 하나로 묶은 문서 group, `gold_family_oracle`은 질의의 모든 answer-bearing 청크를 정답 family 1개로 둔다.|gold family view에서 top 5에 answer-bearing 청크가 하나라도 있으면 Recall@5는 1, 없으면 0이다.|각 view의 `relevant_unit_count`가 분모다. raw Recall·nDCG는 parent 중복에 민감하고 gold family는 운영 dedup이 아닌 평가 상한이다. [18 `view_metrics`](../notebooks/18_answer_bearing_grouped_retrieval_reevaluation.ipynb)|
|18번 candidate coverage|후보 pool과 strict 또는 answer-bearing relevant 청크의 교집합을 각각 relevant 청크 수로 나눈 `*_candidate_recall`과, 하나라도 있으면 1인 `*_candidate_hit`이다. gold family candidate recall은 answer-bearing 청크가 하나라도 pool에 있으면 1이다.|후보 20개에 answer-bearing gold 4개 중 3개가 있으면 0.75다.|reranker 뒤의 순위가 아니라 후보 ceiling 진단이다. [18 `candidate_coverage`](../notebooks/18_answer_bearing_grouped_retrieval_reevaluation.ipynb)|
|19번 Positive Pairwise Cosine Mean/Max — 청크 쌍 유사도|Top5의 10쌍에서 `max(0, cosine)`의 평균·최대를 낸다. 중복 진단에서는 낮을수록 유리하다.|같은 커피 문장을 반복한 두 청크는 높은 유사도를 보일 수 있다.|cosine은 임베딩 방향 유사도이며 사실의 동일성을 보장하지 않는다. [19 `redundancy_diagnostics`](../notebooks/19_mmr_redundancy_ablation.ipynb)|
|19번 Nearest-Neighbor Positive Cosine Mean — 가장 비슷한 청크와의 평균 유사도|각 청크마다 나머지 네 청크 중 최대 양의 cosine을 구하고 5청크 평균을 낸다. 중복 진단에서는 낮을수록 유리하다.|같은 혜택 설명이 여러 번 나오면 각 청크의 최근접 유사도가 높아진다.|다른 카드라도 표현이 비슷하면 높을 수 있다. [19](../notebooks/19_mmr_redundancy_ablation.ipynb)|
|19번 Exact Duplicate Excess·Unique Group Count — 동일 문서 중복|동일 카드·정규화 문서로 묶은 고유 group 수와 `5−고유 group 수`를 센다. excess는 낮을수록 중복이 적다.|5청크가 실제 3문서 묶음이면 unique=3, excess=2다.|문자열이 다른 의미 중복은 잡지 못한다. [19](../notebooks/19_mmr_redundancy_ablation.ipynb)|
|19번 Cross-Level Pair/Containment/High-Cosine Count — 계층 간 중복 쌍 수|같은 카드의 서로 다른 계층 쌍 수, 그중 한 텍스트가 다른 텍스트에 포함되는 쌍 수, cosine≥0.90 쌍 수를 각각 센다. 중복 수는 낮을수록 유리하나 계층 쌍 수 자체에는 공통 통과선이 없다.|parent 청크가 child의 커피 문장을 그대로 포함하면 containment 쌍 1개다.|계층 문맥 보존에도 필요할 수 있다. `level_pair_mean_cosine`은 계층 조합별 원래 cosine 평균이며 음수도 유지한다. [19](../notebooks/19_mmr_redundancy_ablation.ipynb)|
|19번 반환 구성 진단|`unique/expected/off_card_count`, `expected_card_share_at_5`, 원래 순위의 평균·최대, relevance 정규화 점수 평균·합계를 기록한다.|top 5 중 정답 카드 3개면 expected-card share는 3/5다.|후보 수와 순위의 구성 설명용이며 Recall·정확도와 같은 값이 아니다. [19](../notebooks/19_mmr_redundancy_ablation.ipynb)|

19번의 `unique_card_count`는 서로 다른 카드 수, `expected_card_count`는 정답 카드에 속한 청크 수, `off_card_count`는 나머지 청크 수다. `original_rank_mean/max`는 선택한 청크의 변경 전 순위 평균·최대이고 `rel_norm_mean/sum`은 후보 풀에서 정규화한 검색 관련도 점수의 평균·합이다. 이 값들은 결과 구성을 설명하며, 카드 수나 검색 점수만으로 정답 품질을 결정하지 않는다.

### 답변 대용 점수

|지표|의미와 계산 대상·분모|방향·카드 혜택 예시|핵심 한계와 실제 출처|
|---|---|---|---|
|Constraint Coverage|한 추천 카드가 한 AND 요구 묶음의 각 구성 요소를 얼마나 설명했는지의 평균이며, OR 묶음은 가장 높은 후보를 쓴다. 구성 요소는 대상, 행동, 최소 할인율, 실적, 한도, 제외 조건 등이다.|높을수록 좋다. `커피`, `할인`, `전월 30만원 이하`, `월 5천원 이상` 네 조건 중 세 개를 같은 카드 주장과 인용으로 보이면 0.75가 될 수 있다.|32번은 보수적 문자열 대용 검사다. 같은 뜻의 바꿔 말하기와 복잡한 인과 관계를 놓치거나 큰 인용문에서 잘못 통과할 수 있다. [32 `component_support`·`score_row`](../notebooks/32_score_answers.py), [35 report](../notebooks/35_render_answer_report.py)|
|Known Positive Complete — 32~36, 정답 카드의 조건 설명 완료|성공한 답변에서 추천 정답 카드 하나의 AND 요구가 모두 충족되고, OR 대안 중 하나가 완성되며, 인용 소유권=1이고 지원되지 않은 퍼센트 주장=0이면 1이다. 조건이 없는 질문은 카드 일치로 조건 부분을 통과한다. 높을수록 좋다.|커피 할인과 실적·한도를 함께 요구한 질문에서 정답 카드라도 한도 설명이 빠지면 0이다.|실패 행은 0, 정답 카드가 없는 질문은 해당 없음이다. 32는 답변의 `answered` 상태를 별도로 검사하지 않는다. [32 `score_row`](../notebooks/32_score_answers.py)|
|Known Positive Complete — 39~40, 동결된 완료 검사|32와 별도 계약이다. 정답 카드 하나가 AND 요구·OR 대안, 추가 답변 정규식 검사, 인용 소유권, 카드 범위, 퍼센트 근거를 통과하고 답변 상태도 `answered`여야 1이다. 상세 질문은 추천 없이 주장에 나온 정답 카드로 검사한다.|특정 카드 설명에서 추천 목록이 비어 있어도 필요한 혜택·조건이 주장과 근거에 있으면 통과할 수 있다.|어휘 기반 대용 점수이며 사람 평가 정확도가 아니다. 양성 질문만 평균에 넣는다. [39 `score`](../notebooks/39_answer_rubric.py), [40](../notebooks/40_new_query_comparison.py)|
|Citation Ownership|모든 claim과 recommendation의 citation이 존재하고 해당 claim의 카드 키와 같은 카드 evidence를 가리키는지의 0·1이다.|1이 좋다. A카드의 10% 주장이 B카드 인용을 쓰면 0이다.|인용이 같은 카드라는 것만 본다. 인용이 주장을 의미적으로 뒷받침하는지는 완전히 판정하지 않는다. [39 `owned_text`·`score`](../notebooks/39_answer_rubric.py)|
|Unsupported percent claims|답변의 퍼센트 주장 중 자기 카드 인용에 같은 퍼센트가 없거나 허용된 계산 규칙으로 만들 수 없는 주장 수다.|낮을수록 좋다. 인용에는 1%뿐인데 `2% 적립`이라고 쓰면 1건이다.|39번의 등록 합산식은 `percent_supported`에서만 허용한다. 새 18개 질문은 calculations가 빈 목록이며, 최소 퍼센트 조건은 합산값이 인용문에 그대로 없으면 여전히 complete를 통과하지 못할 수 있다. [39 `percent_supported`·`score`](../notebooks/39_answer_rubric.py), [39 limit diagnosis](../notebooks/39_evaluation_limit_diagnosis.py), [40 reports](../notebooks/40_render_reports.py)|
|Explicit Scope Preserved|질문 plan에 카드 범위가 있을 때 답변의 모든 카드가 그 범위 안인지의 0·1이다.|1이 좋다. 특정 카드 둘만 비교하라는 질문에 제3 카드를 추천하면 0이다.|범위가 비어 있으면 통과하며, 카드의 혜택 정확도를 측정하지 않는다. [39 `score`](../notebooks/39_answer_rubric.py)|

### 24~28번의 multi-card·holdout·답변 평가

|지표|의미·계산(분모·방향 포함)|예시|사용처·한계|
|---|---|---|---|
|24번 Card Precision@3/@5, Card Recall@3/@5|top k의 카드 중 positive card 수 ÷ 반환 슬롯 k가 Precision, positive card를 top k에서 찾은 수 ÷ positive card 수가 Recall이다.|정답 카드 2장 중 1장을 top 5에 내고 5개를 반환하면 Recall 0.5, Precision 0.2다.|카드 하나에 근거가 있는지는 별도 지표다. [24 multi-card scorer](../notebooks/24_multicard_recommendation_retrieval_evaluation.ipynb)|
|24번 Evidence-supported Card Recall@5·Evidence Accuracy@3/@5|정답 카드 중 top k에 있고 대표 evidence가 지원되는 카드 수 ÷ positive card 수가 supported recall이다. Evidence Accuracy는 top k 슬롯 중 지원 evidence가 있는 슬롯 수 ÷ k다.|정답 카드가 top 5에 있어도 대표 청크가 `편의점 10%`를 뒷받침하지 못하면 supported recall에는 포함하지 않는다.|single representative와 bundle-supported는 같은 지표 이름처럼 보여도 support 정의가 다르다. [24](../notebooks/24_multicard_recommendation_retrieval_evaluation.ipynb)|
|24번 candidate Card Recall@20·bundle-reachable Evidence Recall@20|candidate card recall은 후보 20에 든 positive card 수 ÷ positive card 수다. bundle-reachable는 positive card별로 허용 bundle이 support evidence에 닿는 수 ÷ positive card 수다.|정답 카드 2장 중 후보에 2장이지만 한 카드의 bundle이 근거에 못 닿으면 전자는 1, 후자는 0.5다.|rerank 결과가 아니라 후보와 bundle의 ceiling이다. [24](../notebooks/24_multicard_recommendation_retrieval_evaluation.ipynb)|
|24번 zero hit·missing slots·후보 구성|`card_zero_hit_at_5`, `supported_zero_hit_at_5`는 각각 카드 또는 지원 근거 hit이 0인 질문의 0·1이다. `missing_slots_at_5`는 반환 카드 수가 5보다 적은 수이며 raw duplicate·unique card 수도 함께 기록한다.|정답 카드가 전혀 없으면 card zero hit 1, 카드 3장만 반환하면 missing slots 2다.|낮을수록 좋은 failure count이지만 이유를 말해 주지는 않는다. [24](../notebooks/24_multicard_recommendation_retrieval_evaluation.ipynb)|
|24번 parser predicate Precision·Recall·F1|조건별 TP(정답 조건 출력), FP(불필요 조건 출력), FN(정답 조건 누락)을 센다. `P=TP/(TP+FP)`, `R=TP/(TP+FN)`, `F1=2PR/(P+R)`. 조건별 F1 평균이 macro, TP·FP·FN 합산 후 F1이 micro다. 높을수록 좋다.|커피 조건을 요구한 3질문 중 2개를 맞추고 다른 질문에 1번 잘못 출력하면 P=R=2/3이다.|조건 추출 정확도이며 검색 성능이 아니다. 빈 분모는 코드에서 0으로 처리한다. [24 parser follow-up](../notebooks/24_multicard_recommendation_retrieval_evaluation.ipynb)|
|24번 two-predicate output coverage·exact accuracy|coverage는 두 조건의 쌍 형식으로 출력한 질문 수 ÷ 10질문이다. 잘못된 쌍도 coverage에는 포함한다. exact accuracy는 정답 조건 쌍과 순서가 정확히 같은 질문 수 ÷ 10이다. 높을수록 좋다.|10질문에 모두 쌍을 출력했지만 4쌍이 틀렸으면 coverage=1, exact=0.6이다.|과거 `coverage_rate`라는 이름은 정답 조건 Recall을 뜻하지 않는다. 잘못된 쌍·출력 실패 수와 함께 본다. [24 follow-up4](../notebooks/24_multicard_recommendation_retrieval_evaluation.ipynb)|
|25번 Card Precision·Recall·F1 — 답변 카드 정확도|식별된 반환 카드 중 정답 수 ÷ 실제 식별된 반환 카드 수가 Precision, 정답 수 ÷ 기대 카드 수가 Recall이다. 고정 k로 나누지 않는다. F1은 둘의 조화평균이며 높을수록 좋다.|정답 2장 중 1장만 정확히 답하면 Precision=1, Recall=0.5, F1 약 0.667이다.|부족함을 답해야 하는 질문은 기대 집합을 비운다. 두 집합이 모두 비면 P=R=1, 양성 질문에서 빈 출력이면 P=R=0이다. [25 `S`](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 Citation Validity — 인용 ID 유효 비율|답변 카드 요약·주장의 인용 중 실제 전달한 evidence ID에 있는 인용 수 ÷ 전체 인용 수. 높을수록 좋다.|인용 4개 중 하나의 ID가 존재하지 않으면 0.75다.|같은 카드 소유권·의미 지원을 보장하지 않는다. 인용이 없으면 기대 거절 질문은 1, 그 외는 0이다. [25 `S`](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 Grounded Claim Precision — 근거가 있는 주장 비율|인용 소유권·숫자 포함·최소 어휘 겹침을 모두 통과한 주장 수 ÷ 검사 주장 수. 높을수록 좋다.|주장 4개 중 `커피 20% 할인` 하나가 인용의 `10%`와 다르면 최대 3/4다.|숫자는 인용 숫자의 부분집합, 토큰 겹침은 주장 토큰 대비 0.35 이상이어야 한다. 의미 판단을 완전히 대신하지 않는다. [25 `S`](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 Required Fact Recall — 답변 사실 재현율|gold의 중복 제거한 `allowed_facts` 중 답변 텍스트에서 매칭된 수 ÷ 해당 사실 수. 높을수록 좋다.|사실 4개 중 커피 할인·실적·한도를 설명하고 제외 조건이 빠지면 0.75다.|이름과 달리 계산 대상은 allowed facts 목록이다. 부분문자열 또는 숫자 포함 및 토큰 겹침 0.60 기준으로 바꿔 말하기를 놓칠 수 있다. 빈 사실 목록은 1이다. [25 `hit`·`S`](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 Numeric Value/Unit/Condition Exact — 수치·단위·조건 충족|요구 숫자가 답변에 모두 있고 적용되는 조건 문구가 모두 매칭되면 1, 아니면 0이다. 높을수록 좋다.|할인율 10%와 전월 실적 30만원 중 실적 설명이 빠지면 0이다.|문자열·숫자 비교이므로 각 숫자가 올바른 혜택에 연결됐는지는 별도 한계다. [25 `S`](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 Complete Answer — 완전 답변 대용 점수|응답 형식·완료 상태·기대 거절 여부·정답 카드 집합이 맞고 사실·인용·주장 지원·수치조건 점수가 모두 1이며 부적절 카드가 없으면 1이다. 높을수록 좋다.|커피 추천 카드는 맞아도 필요한 제외 조건이 빠지면 0이다.|모든 조건을 함께 검사하는 0·1 점수이며 사람의 만족도는 아니다. [25 `S`](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 Quality Score — 여섯 점수의 평균|Card F1, Citation Validity, Grounded Claim Precision, Required Fact Recall, Numeric Exact, Complete Answer의 합 ÷ 6. 높을수록 좋다.|앞의 다섯 점수가 1이고 Complete가 0이면 5/6이다.|서로 다른 검사에 같은 가중치를 준 실험용 합성 점수다. 평균이 좋아도 개별 오류를 함께 확인한다. [25 scoring contract](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 False Abstention·False Answer — 잘못된 거절·답변|답할 수 있는데 거절하면 false abstention=1, 거절해야 하는데 답하면 false answer=1이다. 각각 질문별 0·1 평균이며 낮을수록 좋다.|근거가 있는 커피 혜택에 `근거 부족`으로 답하면 false abstention이다.|원래 정답 계약의 답변 가능 여부 기준이다. [25](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 Unsupported/Duplicate Count — 부적절 카드·주장·중복 수|정답 범위 밖 또는 식별 불가능 카드 수, 근거 검사를 통과하지 못한 주장 수, 반환 카드의 중복 초과 수를 각각 센다. 낮을수록 좋다.|같은 카드를 2번 추천하면 duplicate card count=1이다.|잘못된 카드와 잘못된 혜택 주장은 다른 오류다. [25 `S`](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 Critical/Major/Minor Error — 오류 심각도|형식·완료 실패, 잘못된 답변·카드는 critical이다. 그 외 잘못된 거절·불완전 카드 회수·사실·인용·숫자조건은 major, 나머지 검증 실패는 minor다. 각 질문에서 해당 등급의 0·1을 평균내며 낮을수록 좋다.|정답 없는 질문에 카드를 추천하면 critical, 정답 카드의 한도를 빠뜨리면 major일 수 있다.|코드에서 앞 등급이 있으면 뒤 등급은 0이다. 일반적인 오류 심각도 표준과 같은 정의로 취급하지 않는다. [25 `S`](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|25번 blind 평가 상태|빈 `answer_quality_blind_audit.csv`의 사람 preference 칸은 템플릿이라 측정값이 아니다. 실제 후속 결과는 80 anonymous pair를 single custom Codex reviewer가 본 W/L/T와 severity다.|A/B 카드 선택, 필수 사실·숫자·조건, 인용 지원, 누락을 검토한다.|사람 평가가 아니며 자동 결정도 대체하지 않는다. [25 blind packet·review](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|
|26번 Card/Supported Hit·MRR@3 — 카드·지원 근거 순위|Card Hit은 정답 카드가 Top3에 있으면 1, MRR은 첫 정답 카드 순위의 역수다. Supported Hit·MRR은 정답 카드의 실제 전달 근거에 정답 span 하나 이상도 있어야 한다. 높을수록 좋다.|정답 카드가 2위지만 근거가 연회비 설명뿐이면 Card MRR=0.5, Supported MRR=0이다.|지원 지표는 evidence 질문만 평균낸다. 단순 카드 이름 매칭이나 이전 strict 청크 계약과 다르다. [26 label evaluation](../notebooks/26_cross_package_holdout_evaluation.ipynb)|
|26번 Evidence/Complete Span Coverage@3 — 원문 구간 회수율·전체 충족|Top3 카드에 전달한 근거에서 찾은 정답 span 수 ÷ 해당 질문의 정답 span 수가 Coverage다. 모두 찾으면 Complete Coverage=1이다. 높을수록 좋다.|필요 span 3개 중 2개를 전달하면 Coverage=2/3, Complete=0이다.|Coverage는 모든 반환 카드 근거의 합집합에서 계산하므로 그 자체로 같은 카드 소유권을 보장하지 않는다. [26](../notebooks/26_cross_package_holdout_evaluation.ipynb)|
|27~28번 Card Precision@3·Recall@3 — 실제 반환 수 기준|중복 제거한 최대 3장 중 정답 카드 수 ÷ 실제 반환 카드 수가 Precision이다. Recall은 같은 분자를 정답 카드 수로 나눈다. 양성 빈 출력은 둘 다 0이다. 높을수록 좋다.|정답 2장 중 1장만 반환하면 Precision=1, Recall=0.5다. 고정 분모 3을 쓰는 값과 다르다.|양성 26질문의 질문별 점수를 평균낸다. 27은 계산 계약·시험 예시, 28은 실제 후보 평가다. [27 `score_response`](../notebooks/27_integrated_holdout_dataset.ipynb), [28](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb)|
|27~28번 Supported Card Recall@3 — 근거까지 충족한 카드 재현율|반환한 정답 카드 중 그 카드의 필수 사실·조건 지원 묶음을 충족한 카드 수 ÷ 정답 카드 수. 높을수록 좋다.|정답 2장을 모두 추천해도 한 카드의 실적 근거가 부족하면 0.5다.|카드마다 AND·OR로 정한 지원 계약을 검사한다. 단순 카드 Recall과 다르다. 양성 26질문 평균이다. [27~28 계약](../notebooks/27_integrated_holdout_dataset.ipynb)|
|27~28번 Required Claim Coverage — 필수 주장 충족률|AND 묶음은 각 필수 원자 주장을 한 단위로, OR 묶음은 하나 이상 충족했는지를 한 단위로 세어 충족 단위 수 ÷ 전체 필수 단위 수를 계산한다. 높을수록 좋다.|할인율·실적·한도 세 AND 단위 중 한도를 놓치면 2/3이다.|27 최신 `score_response`의 지원 구조 기준이다. 초기 단순 시험 예시의 claim 집합 Recall과 혼동하지 않는다. 28 검색은 근거 전달, 답변은 실제 주장 지원을 검사한다. [27](../notebooks/27_integrated_holdout_dataset.ipynb), [28](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb)|
|27~28번 Citation Ownership — 인용 소유권 비율|주장이 말한 카드 소유의 인용 수 ÷ 전체 인용 수. 양성 무인용은 0, 올바른 음성 거절은 해당 없음이다. 높을수록 좋다.|인용 4개 중 하나가 다른 카드 문서면 0.75다.|32·39의 모든 인용을 함께 검사하는 0·1 ownership과 다른 집계다. 양성 질문별 평균과 전체 인용 집계의 통과 조건을 구분한다. [27 `score_response`](../notebooks/27_integrated_holdout_dataset.ipynb)|
|27번 Negative Correctness — 정답 없음 처리 계약|정답 카드가 없는 질문에서 카드 추천·언급 신호가 없고 근거 부족을 표시하며 실패 이유가 없으면 1이다. 음성 4질문 기준이며 높을수록 좋다.|범위 안에 30% 커피 할인 카드가 없는데 임의 카드를 추천하면 0이다.|27의 계약·시험 예시다. 해당 문서 범위에서의 거절이며 시장 전체에 카드가 없다는 뜻은 아니다. [27 `score_response`](../notebooks/27_integrated_holdout_dataset.ipynb)|
|28번 Answer Negative Correctness — 답변 거절 성공|실제 28 답변에서 식별된 반환 카드가 없고 `insufficient_evidence`가 참이면 1이다. 음성 4질문 기준이며 높을수록 좋다.|근거 부족을 표시했어도 식별된 카드를 반환하면 0이다.|이 점수만으로 형식 오류를 배제하지 않는다. 음성 E2E 성공은 형식 오류=0도 요구한다. [28 LLM scoring 셀](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb)|
|27~28번 End-to-End Exact Success — 전체 답변 조건 완료|정답 카드 집합, 필수 주장·지원 카드·인용, 답변 가능 여부가 맞고 오류가 없으면 1이다. 음성 질문은 Negative Correctness를 사용한다. 전체 30질문의 완료 수·비율을 집계하며 높을수록 좋다.|카드와 혜택은 맞아도 필요한 실적 조건이 틀리면 0이다.|28의 `retrieval_end_to_end_exact_success`는 검색 근거만의 검사다. LLM 답변 완료와 구분한다. [27 scorer](../notebooks/27_integrated_holdout_dataset.ipynb), [28](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb)|
|28번 retrieval·answer 결과|retrieval은 `candidate_card_recall_d20`, Card Precision/Recall@3, Supported Card Recall@3, Required Claim Coverage, negative correctness, retrieval E2E exact를 쓴다. LLM answer scorer는 여기에 citation ownership, end-to-end exact, format·semantic validation·critical·wrong-card·negative false·unsupported·duplicate error count와 paired W/L/T를 더한다.|정답 카드가 후보 20에는 있어도 top 3에 없으면 candidate recall은 높고 card recall은 낮을 수 있다.|retrieval-only 결과는 winner를 정하지 않으며, answer score는 외부 LLM judge가 아닌 sealed component와 lexical/numeric matcher다. [28 retrieval scorer·LLM scorer](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb)|
|28번 Candidate Card Recall D20 — 후보 카드 재현율|20청크 후보에 들어 있는 정답 카드 수 ÷ 정답 카드 수. 양성 26질문 평균이며 높을수록 좋다.|정답 2장 모두 후보에 있지만 최종 Top3에는 1장만 남으면 candidate recall=1, card recall=0.5다.|후보 단계의 상한이며 최종 추천 성공률이 아니다. [28 retrieval scoring](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb)|
|28번 Retrieval Negative Correctness — 검색 음성 검사|음성 질문의 반환 카드 중 동결된 조건 판정에서 적합하다고 표시된 카드가 없으면 1이다. 높을수록 좋다.|범위 내 정답 카드가 없는 질문에서 이 검색 검사와 LLM의 거절 검사는 따로 집계한다.|검색 결과가 반드시 빈 목록이어야 하거나 답변이 근거 부족이어야 하는 검사는 아니다. 위의 답변 Negative Correctness와 구분한다. [28 retrieval scoring](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb)|
|27번 Derived Error Counts — 계약의 원인별 오류 수|형식 오류·인용 오류·지원 없는 주장·중요 필수 주장 실패·부적절 카드·음성 추천을 각각 센다. `candidate_failure_count`는 중복 제거한 실패 이유 수다. 낮을수록 좋다.|필수 할인율이 누락되면 중요 주장 실패가 기록된다.|실제 28의 오류 계산과 같은 계약이라고 단정하지 않는다. [27 `score_response`](../notebooks/27_integrated_holdout_dataset.ipynb)|
|28번 Derived Error Counts — 실제 답변의 오류 수|`format_error_count`·`semantic_validation_error_count`는 저장 응답의 형식·의미 검증 실패와 추가 형식 오류다. `unsupported_claim_count`는 지원 없는 주장, `wrong_card_error_count`는 정답 밖 카드, `negative_false_recommendation_count`는 음성 추천 여부, `duplicate_card_count`는 중복 카드 초과 수다. 낮을수록 좋다.|같은 카드를 두 번 반환하면 duplicate count=1이다.|`critical_error_count`는 wrong card+negative false+unsupported claim의 합이다. 음성 추천은 wrong card와 중복될 수 있으므로 합계를 독립 사고 건수로 읽지 않는다. [28 LLM scoring 셀](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb)|

## 동작·안전 검사

|지표|의미·계산(분모·방향 포함)|예시|사용처·한계|
|---|---|---|---|
|상태 수와 실패 수|실행 record의 `ok`, `failed`, `started`, `incomplete` 등을 센다. 분모는 해당 실험이 만들기로 한 요청 또는 질문 record다.|답변 호출이 실패하면 품질 점수 0과 별도로 failed 상태를 남긴다.|실패가 없다고 답변 또는 검색 품질이 높다는 뜻은 아니다. [32~36 notebooks](../notebooks), [37](../notebooks/37_operational_guard_audit.ipynb)|
|해시·freeze·보존 검사|입력 데이터, 코드 셀, payload, 이전 산출물의 SHA-256, row 수, cache identity를 비교한다. 일치해야 통과한다.|gold를 열기 전 label-free ranking을 seal했는지 확인한다.|내용이 올바른지보다 재현 중 변경이 없었는지를 확인한다. [24](../notebooks/24_multicard_recommendation_retrieval_evaluation.ipynb), [26](../notebooks/26_cross_package_holdout_evaluation.ipynb), [28](../notebooks/28_integrated_holdout_candidate_evaluation.ipynb), [30~40 helpers](../notebooks)|
|30번 Structured Field Support Rates — 조회 가능한 구조화 필드 비율|정규화 규칙에서 known 또는 지원 가능한 행동으로 판정한 fact 수 ÷ 전체 fact 수. 높을수록 해당 조건의 조회 범위가 넓다.|할인율을 확정할 수 있는 fact가 100개 중 70개면 해당 support rate=0.7이다.|추출 정확도가 아니다. 범위·혼합 수치는 unknown으로 남길 수 있다. DB의 integrity·foreign-key 검사도 별도로 기록한다. [30 build summary](../notebooks/30_build_structured_db.py)|
|parser exact match·candidate set match|33번 이후의 질의 plan에서 조건 group, 경로, 카드 후보가 frozen gold와 같은지 0·1로 검사하고 field errors를 기록한다. 1이 좋다.|`전월 30만원` 조건과 후보 카드 집합이 모두 같아야 exact 1이다.|질의 해석 계약 일치 검사다. 검색 순위나 답변의 사실성은 별도다. [30 verify](../notebooks/30_verify_experiment_results.py), [30 report](../notebooks/30_render_experiment_reports.py)|
|33번 parser 일치 검사|`route_match`는 검색 경로, `condition_exact_match`는 정규화 조건 묶음, `card_scope_match`는 지정 카드 집합, `candidate_set_match`는 조건 실행 결과 카드 집합의 정확 일치다. 각각 질문별 0·1이며 평균 분모는 검사 질문 수다. 1이 좋다.|금액 조건은 맞아도 카드 이름을 잘못 인식하면 condition 일치=1, card scope 일치=0일 수 있다.|조건이 다르지만 후보가 우연히 같을 수 있으므로 두 일치율을 따로 본다. [33](../notebooks/33_query_routing.py)|
|33번 boolean group·unresolved·field errors|`boolean_group_match`는 각 조건 묶음 길이들을 정렬한 목록이 같은지의 0·1이다. `unresolved`는 미해결 항목이 있으면 1이며 낮을수록 좋다. `field_errors`는 잘못 추출한 필드 목록으로 적을수록 좋다.|AND 조건 2개인 묶음을 OR의 1개씩 두 묶음으로 바꾸면 `[2]`와 `[1,1]`이 달라 group match=0이다.|group match는 조건 내용 전체를 검사하지 않는다. `unresolved`의 1은 일치 성공이 아니다. [33](../notebooks/33_query_routing.py)|
|negative control|gold card가 없는 질문은 Hit, MRR, complete의 양성 분모에서 제외하고, 부적절한 카드를 추천하거나 반환하는지를 별도 확인한다.|정답 카드가 없는 일반 조언 질문은 양성 Recall에 넣지 않는다.|양성 지표의 분모 오염을 막지만 전체 거절 품질을 단일 숫자로 만들지는 않는다. [32 `evaluate_retrieval`](../notebooks/32_search_experiment.py)|
|32~36번 Answer Anchor Hit — 답변 정답 카드 포함|추천 카드 집합에 정답 카드가 하나라도 있으면 1이다. 양성 질문만 집계하며 높을수록 좋다.|검색 결과에는 정답 카드가 있었어도 최종 추천에서 빠지면 0이다.|검색 Hit과 답변 Hit을 분리해야 한다. [32 답변 metrics](../notebooks/32_search_experiment.py)|
|32~36번 Negative Abstention·Insufficient — 근거 부족 응답|`negative_abstention`은 음성 대조 질문에서 근거 부족으로 답했는지의 0·1이며 높을수록 좋다. `insufficient`는 모든 답변의 근거 부족 상태를 세는 0·1 진단이다.|정답이 없는 질문에 부족함을 답하면 둘 다 1이다. 정답이 있는 질문에 같은 답을 하면 성공으로 보지 않는다.|모든 질문에서 insufficient가 낮아야 한다는 공통 기준은 없다. [32 답변 metrics](../notebooks/32_search_experiment.py)|
|05번 STP Rate·Auto-Pass Accuracy·Human Queue Rate — 자동 통과·정확도·검토 대기율|자동 통과율은 `n_auto/n_total`, 대기율은 `n_human/n_total`, 자동 통과 정확도는 자동 통과 행 중 gold와 같은 수 ÷ 자동 통과 행 수다. 저장한 `_pct` 값은 각각 100을 곱한다. 정확도 유지 아래 자동화 범위를 높이는 것이 유리하다.|10필드 중 6개 자동 통과, 그중 5개가 정확하면 통과율 60%, 정확도 약 83.3%, 대기율 40%다.|통과 정확도의 분모는 전체 field가 아니다. 자동 통과가 없으면 미측정이며 전체 운영 오류율로 일반화하지 않는다. confidence는 이전 비교 정책에만 쓰인다. [05 셀 7·8](../notebooks/05_hybrid_verification_pipeline.ipynb)|
|37번 fixed-output guard confusion|고정 provider 출력 사례에서 `true_accept`, `false_accept`, `true_reject`, `false_reject`를 센다. 분모는 주입한 출력 사례 수이며 false 값은 낮을수록 좋다.|틀린 `99%` claim이 수락되면 false accept 1이다.|실제 LLM 오류 빈도가 아니라 방어 장치의 민감도 검사다. [37](../notebooks/37_operational_guard_audit.py)|
|38번 parser fault scope 손실|`source_or_selector_gold_loss=gold−oracle eligible`, `additional_parser_gold_loss=(gold∩oracle eligible)−faulty eligible`, `effective_scope_gold_loss=gold−effective keys`의 카드 목록과 건수를 남긴다. 모두 적을수록 좋다. fallback은 eligible 카드 없음, unresolved 존재, unstructured route 중 하나다.|`월`을 `일`로 잘못 파싱해 후보가 0이지만 fallback이 전체 scope를 쓰면 parser loss와 effective loss는 다를 수 있다. `keys=None` 전체 scope면 effective loss는 빈 목록이다.|semantic loss, selector loss, fallback recovery를 같은 실패 수로 합치지 않는다. 주입 fault 진단이며 실제 parser 오류율이 아니다. [38](../notebooks/38_query_condition_audit.py)|

## 자원 지표

|지표|의미·계산(단위·방향 포함)|예시|사용처·한계|
|---|---|---|---|
|API 요청 수·성공 요청 수·캐시 hit|새 API 요청·성공한 요청·저장 응답 재사용을 각각 센다. 단위는 요청 또는 batch 수다. 같은 작업·품질이면 새 요청은 적고 재사용은 많을수록 자원 효율에 유리하다.|같은 query embedding을 cache에서 읽으면 새 요청은 0이다.|요청 수가 같아도 입력 길이와 비용은 다르다. 실패로 요청이 줄어든 경우는 개선이 아니다. [13](../notebooks/13_hierarchical_chunking_retrieval.ipynb), [30 common](../notebooks/30_structured_search_common.py), [34~36 helpers](../notebooks)|
|사용 토큰|API 응답의 입력·출력·전체·캐시 읽기·캐시 쓰기·추론 토큰을 기록한다. 단위는 token이다. 동일 품질이면 불필요한 소비가 적을수록 유리하다.|최대 output token이 4800이어도 실제 output이 1200이면 사용량은 1200이다.|최대값은 소비량이 아니다. 추론 토큰은 출력에 포함되므로 다시 더하지 않는다. 누락 usage는 미측정이다. [35 report](../notebooks/35_render_answer_report.py), [36 cost](../notebooks/36_cost_accounting.py)|
|사용량 기준 비용 환산액|저장된 사용량에 해당 실험에서 확인·동결한 단가를 곱한 `cost_usd`다. 단위는 USD, 동일 품질이면 낮을수록 유리하다. 중복 기록은 중복 과금으로 세지 않는다.|로컬 저장 응답을 재사용한 논리 비교 행은 새 API 비용이 없다.|실제 청구액이 아니다. 실패 호출의 실제 사용량도 포함하며 세금·할인·환율·로컬 전력은 제외한다. [36 `cost_from_usage`](../notebooks/36_cost_accounting.py)|
|시간|`wall_seconds`는 실제 경과 시간, 단계별 elapsed·score seconds는 해당 단계의 시간이다. 단위는 초, 동일 작업·품질·장비 조건이면 낮을수록 유리하다.|reranker 단계가 12초면 해당 단계 경과 시간을 12초로 기록한다.|전체 실행 시간과 개별 요청 지연은 다르다. 실패·캐시·배치 조건을 맞춰 비교한다. [26](../notebooks/26_cross_package_holdout_evaluation.ipynb), [30 common](../notebooks/30_structured_search_common.py)|
|프로세스·GPU 메모리|프로세스 `peak_rss_kib`는 최대 상주 메모리, GPU allocated/reserved는 사용·예약 메모리다. 단위는 KiB·MiB·byte 등 기록값 기준이며 동일 품질이면 낮을수록 유리하다.|BGE를 불러온 프로세스의 RSS 증가를 기록한다.|예약량·상주량·GPU 시점 관측은 서로 다르다. 23의 Top10 전용 VRAM은 미측정이다. [26](../notebooks/26_cross_package_holdout_evaluation.ipynb), [40 memory](../notebooks/40_memory_recovery.py)|
|Host Available·Required Headroom — 시스템 가용 메모리·필요 여유|`minimum_host_available_kib`는 관측한 최소 시스템 가용 메모리다. required headroom은 모델 로딩·batch 전에 요구하는 여유 기준이며 실측 peak 사용량이 아니다. 가용 여유가 클수록 중단 위험이 작다.|현재 가용량이 요구 여유보다 작으면 해당 조건을 미실행으로 남긴다.|메모리 기준은 OOM 방지 휴리스틱이며 실행 성공을 보장하지 않는다. 다른 프로세스와 시점의 영향을 받는다. [40 memory](../notebooks/40_memory_recovery.py), [40 report](../notebooks/40_render_reports.py)|
|저장공간·index 크기|index·cache 파일 byte, vector shape, chunk·파일 수를 기록한다. 같은 보존 범위·품질이면 작은 저장량이 유리하다.|새 index를 만들면 기존과 새 index의 파일 크기를 비교한다.|청크·파일 수는 진단값으로 공통 최적 방향이 없다. 파일 크기는 지연을 직접 뜻하지 않는다. [31](../notebooks/31_operational_chunking.py), [30 report](../notebooks/30_render_experiment_reports.py)|
|후보·pair 수·근거 길이·truncation|검색·rerank 후보 수, 카드 수, 점수 계산 쌍 수, 근거 token/byte 및 실제 입력 잘림을 기록한다. 같은 품질이면 불필요 pair·token이 적을수록 유리하고 잘림은 0이 바람직하다.|재순위 후보가 20개에서 50개가 되면 계산 pair가 늘 수 있다. 문서 제한 초과가 오류로 끝난 경우는 잘림과 별도 실패다.|5청크 bundle로 선택한 것 자체는 token truncation이 아니다. 후보 수·길이에는 품질을 고려하지 않은 공통 최적값이 없다. [24](../notebooks/24_multicard_recommendation_retrieval_evaluation.ipynb), [34](../notebooks/34_bundle_comparison.ipynb)|

## 평균·반복·비교값의 읽는 법

|지표|의미·계산|예시|사용처·한계|
|---|---|---|---|
|Mean·Min·Max — 반복 평균·최소·최대|동일 설정의 n회 결과에서 평균은 합계/n, 최소·최대는 관측 범위다. 방향은 원 지표를 따른다.|정확도가 0.8·0.9이면 평균 0.85, 최소 0.8, 최대 0.9다.|09·10의 반복 집계다. 질문별 평균과 반복별 평균을 구분한다. [09](../notebooks/09_semantic_repeatability_runner.py), [10](../notebooks/10_relational_critical_fact_evaluation.ipynb)|
|Population Standard Deviation — 반복 모집단 표준편차|반복 점수 x에 대해 `sqrt(Σ(x−평균)^2/n)`. 낮을수록 관측 결과의 흔들림이 작다.|정확도가 0.8·0.9이면 표준편차는 0.05다.|09는 한 값이면 0을 계산하고 10은 반복이 1회이면 null로 남긴다. 안정적인 오답도 편차는 작다. [09](../notebooks/09_semantic_repeatability_runner.py), [10](../notebooks/10_relational_critical_fact_evaluation.ipynb)|
|Paired Delta·W/L/T — 같은 질문의 변화·승패|변화는 후보−기준 점수다. 높을수록 좋은 지표에서 양수는 win, 음수는 loss, 허용 오차 안은 tie다. 오류·중복처럼 낮을수록 좋은 지표는 방향을 반대로 해석해야 한다.|카드 MRR이 0.5에서 1이면 delta=+0.5, win이다.|질문별 결과를 비교하며 질문군·분모를 함께 적는다. 일부 코드의 raw outcome은 단순 증감이므로 오류 지표 증가를 품질 승리로 읽으면 안 된다. [19](../notebooks/19_mmr_redundancy_ablation.ipynb), [25](../notebooks/25_llm_answer_quality_pipeline_comparison.ipynb)|

## 알고리즘과 파라미터: 평가 지표가 아님

|이름|의미·역할|예시|출처|
|---|---|---|---|
|MMR — Maximal Marginal Relevance, 관련성과 다양성을 함께 고려하는 선택|다음 청크를 고를 때 질문 관련도와 이미 선택한 청크와의 중복 벌점을 함께 계산하는 알고리즘이다. 19의 식은 `λ×정규화 관련도−(1−λ)×최대 양의 유사도`다. MRR과 다르다.|커피 할인 설명 5개가 반복될 때 관련성을 유지하며 한도·실적 등 다른 근거를 선택하도록 유도한다.|다양성이 정답을 보장하지 않는다. [19](../notebooks/19_mmr_redundancy_ablation.ipynb), [20](../notebooks/20_selective_mmr_gte_gold_type_oracle.ipynb)|
|RRF·vector/BM25 weight — 순위 결합·가중치|여러 검색 순위를 합치는 방법과 비중이다. 결과를 Hit·Recall·MRR·nDCG로 평가한다.|카드명 검색의 BM25 순위와 의미 검색의 vector 순위를 합친다.|가중치 자체는 평가 점수가 아니다. [15](../notebooks/15_korean_morphology_retrieval_ablation.ipynb), [16](../notebooks/16_normalized_rrf_weight_ablation.ipynb)|
|Reranker·candidate depth·chunking·budget — 재순위·후보 수·청킹·예산|검색과 답변을 만드는 설정이다. 바꾼 뒤 품질·자원·실패를 측정한다.|Top20을 재순위한 결과와 Top50을 재순위한 결과를 같은 질문으로 비교한다.|설정값과 성과·채택 여부를 구분한다. [17](../notebooks/17_gte_reranker_ablation.ipynb), [21](../notebooks/21_current_chunking_prebranch_reranker_evaluation.ipynb), [34](../notebooks/34_bundle_comparison.ipynb), [35](../notebooks/35_answer_budget.ipynb)|

## 번호별 사용처 색인

|번호|실제 지표 또는 검사 사용처|
|---|---|
|01|카드 Hit@3, 카드 Recall@5, 카드 MRR@5, 등급 nDCG@5|
|02|텍스트 레이어 길이·문자 이상, token Jaccard, 숫자 재현율, CER, WER, 단어 Precision·Recall|
|03|OCR 수집 flag와 evaluation plan, `manual_full_and_field_metrics.json` engine summary 읽기, 존재 시 Claude manual summary 병합|
|04|방향별 token recall, 숫자 set 차이 목록, Vision·Upstage와 사람이 확인한 field verdict|
|05|입력 샘플 field routing의 `stp_rate_pct`, `auto_pass_accuracy_pct`, `human_queue_rate_pct`, confident-but-wrong count와 v1 비교|
|06|OCR schema·페이지 완전성 및 active-engine/최소-engine/fact-set 기반 `needs_review`·`auto_candidate` routing|
|07|OCR CER, 토큰·숫자 Precision·Recall·F1, exact text, structured field exact/present, 엔진 agreement|
|08|07과 같은 gold OCR·필드 측정에 runtime과 usage 기록 추가|
|09|fact exact, null rate, numeric/string/unit accuracy, 반복 실행 집계|
|10|관계형 critical fact match·mismatch·unresolved 진단|
|11|숫자 오류 원인과 critical numeric 관계 진단|
|12|macro/micro CER·token PRF, field/table exact·present/null, scorable relation·numeric·unit 지표, missing/unsafe mismatch와 자원 기록|
|13|청크 카드 Hit, strict evidence Hit, Recall, MRR, nDCG, embedding 요청·토큰|
|14|holdout 계약과 relevance·분모 고정 검사|
|15|형태소 ablation의 청크/card Hit, Recall, MRR, nDCG, paired 비교|
|16|RRF weight ablation의 같은 지표, guardrail, wins/losses/ties|
|17|GTE reranker ablation의 같은 검색 지표와 자원 기록|
|18|strict·answer-bearing raw, exact-doc-dedup, gold-family-oracle별 Hit/Recall/MRR/nDCG와 candidate coverage|
|19|MMR 선택의 view별 검색 지표와 cosine·duplicate·card/level 구성 redundancy 진단|
|20|19·18 저장 결과를 고른 gold-type oracle 비교. 새 측정이 아닌 사후 조합|
|21|현재 청킹과 prebranch reranker의 질문별 지표, paired delta, WLT|
|22|구조·heading chunking ablation의 검색 품질과 candidate 진단|
|23|구조 chunking/reranker 비교, top10 guardrail, resource 미측정 표기|
|24|multi-card Card Precision/Recall, evidence supported/accuracy, candidate·bundle reachable recall, zero hit·slots, parser F1/coverage/exact|
|25|자동 answer quality score·오류 지표와 80 pair single custom Codex blind review. 빈 human CSV는 측정 아님|
|26|Card/Supported Hit·MRR@3, evidence span coverage·complete coverage, paired 비교와 resource·integrity|
|27|integrated holdout 데이터셋과 scoring contract·seal 생성|
|28|candidate D20·Card/Supported Recall, claim coverage, negative correctness, retrieval/answer E2E exact·오류·WLT, stage ledger|
|29|runtime snapshot 내보내기와 후보·계약 일치 검사|
|30|structured DB 필드 support rates·fact 수, SQLite integrity·foreign-key·출처 보존 검사|
|31|operational chunking 비교와 결과 보존·resource 기록|
|32|구조 검색 비교의 카드 Hit/MRR/Recall, Witness, prefilter retention, Constraint Coverage, Known Positive Complete|
|33|32와 같은 평가에 query parser 결과와 parser 정확 검사 추가|
|34|bundle policy 비교의 retrieval 품질, payload token 한도, failure token audit|
|35|answer budget 비교의 answer 대용 점수, 상태, token, usage 기반 비용|
|36|profile/search 비교의 retrieval·answer 차이, diagnostics, cost ledger|
|37|고정 출력 true/false accept·reject, 호출·재시도 수, 운영 guard와 기존 산출물 보존|
|38|gold selector 손실·추가 parser 손실·최종 scope 손실, fallback과 검색 Hit/MRR/Witness|
|39|frozen lexical rubric: complete, citation ownership, unsupported percent, scope 보존. registered sum 한계 기록|
|40|새 질문 부분 비교: 카드 Hit/MRR, 40 방식 Witness, 39 rubric, 비용과 partial-grid 검증. 108개 중 28개 완료, 80개 미실행|

## 확인 방법과 해석 범위

정의의 직접 근거는 위 링크의 코드 셀과 helper 함수다. 저장된 JSON, CSV, HTML은 해당 지표가 실제 결과 표에 사용됐는지를 확인하는 보조 근거로만 읽었다. 03번의 지표 목록처럼 계획 또는 설명만 있고 실행 계산을 확인하지 못한 항목은 실측 지표로 승격하지 않았다. OCR의 gold 범위, 각 retrieval view의 relevance unit, 양성 질문 수, 실행한 조합은 해당 노트북의 frozen dataset·contract·result를 함께 확인해야 한다.

## 46번 추가: 조건 추출과 실패 경계

아래 항목은 [46번 노트북](../notebooks/46_query_boundary_validation.ipynb)의 개발 평가다. 24개 입력을 3회 추출한 72개 관측이며 독립 질문 72개가 아니다. 원래 자동 판정과 실행 뒤 확인한 주석·정규화 문제는 별도로 보존한다.

|지표|의미·분모·방향·통과 기준|예시|한계|
|---|---|---|---|
|Parser Success — 조건 추출 요청 성공|평가 관측 중 스키마 출력이 저장된 수. 분모는 해당 집단의 실제 요청 수이고 높을수록 좋다. 스키마 출력이 있으면 해당 요청 성공이다.|커피 10% 질문에서 유효 QueryPlan을 받으면 요청 성공1이다.|대상·조건의 의미 정확성이나 DB 검색 성공을 보장하지 않는다.|
|Frozen Annotation Match — 고정 주석 기준 충족|`semantic_match`는 사전에 적은 대상 별칭·행동·수치·조건·카드 범위와 맞는지를 관측별0·1로 검사한다. 모호한 입력은 사전 지정한 확인 행동이나 허용 범주를 검사한다. 분모는 해당 집단 관측 수이며 높을수록 좋다. 모든 해당 기준을 만족해야1이다.|커피10% 할인·전월30만원 이하의 세 요소를 같은 조건에서 설명해야1이다.|자유로운 의미 판정이나 검색·답변 정확도가 아니다. 46번 B11 정상 질문은 고정 주석에 명시 카드 범위가 빠져 자동0이 된3건을 별도로 표시했다. `Netflix`·`넷플릭스 구독료`처럼 뜻이 같아도 별칭 집합 밖이면0이 될 수 있다.|
|Mandatory Boundary Regression — 필수 경계 회귀|사전 정의한 경계 사례 중 기대 경로와 범위를 모두 지킨 수. 분모는 고정9사례이며9/9여야 다음 단계로 진행한다. 높을수록 좋다.|추출 API가 실패해도 사용자가 명시한 A카드 범위는 유지하고 비구조화 검색으로 복귀해야 통과다.|9사례 통과가 모든 자연어·실패 조건의 안전성을 보장하지 않는다.|
|Controlled Semantic Fallback — 고정 의미 반례의 복귀|준비한6개 잘못된 계획 중 비구조화로 복귀한 수. 의미 문자 규칙의 탐지와 독립 카드 범위 정책의 거절을 따로 센다. 알려진 반례에서 복귀가 많을수록 바람직하다.|커피20%를 넷플릭스20%로 바꾼 계획이 남기는 잘못된 후보를 확인한다.|합성 반례의 분모6은 실제 사용자 오류율의 분모가 아니다. 초기 정책6/6과 선정 정책5/6을 혼동하지 않는다.|
|Target Literal False Rejection — 대상 문자 검사 오거절|뜻이 같은 대상 표현을 문자 검사로 거절한 관측 수. 검토한 원응답이 분모이며 적을수록 좋다. 관측0이 바람직하지만 전체 오류율 보장은 아니다.|질문의 넷플릭스를 출력에서 Netflix로 표현했는데 다른 대상으로 판단하면 오거절이다.|원문·출력의 의미를 직접 확인해야 한다. 비구조화 복귀가 일어났다는 사실만으로 최종 답변 실패라고 판정하지 않는다.|
|P95 Request Latency — 요청 응답시간95% 지점|캐시 재생을 제외한 실제 요청의 경과시간을 정렬해 `ceil(0.95×N)`번째 값을 쓴다. 단위는 초이고 같은 품질이면 낮을수록 좋다. 운영 통과 SLA는 아직 정하지 않았다.|72회면69번째 응답시간이다. 평균보다 느린 요청 구간을 함께 본다.|파서 API 한 단계의 시간이며 전체 RAG 응답시간이 아니다. 표본이 작거나 다른 장비·시점이면 직접 비교에 주의한다.|

사용량 비용·캐시 토큰·최대 상주 메모리의 의미는 위 자원 지표 정의를 따른다. 실제 청구액·API 계정 잔액·운영 SLA는 이번 기록으로 확정하지 않는다.

## 47번 부분조건 복귀 비교의 원문 완전성

| 지표 | 의미 | 예시 |
| --- | --- | --- |
| Source-reviewed PASS / PARTIAL(원문 검토 완전 충족 / 일부 충족) | 질문별로 같은 카드의 요구 혜택과 직접 적용 조건을 원문과 대조한다. PASS가 높을수록 좋고 PARTIAL은 PASS에 포함하지 않는다. 부정 질문은 미충족을 명시하고 부분 혜택을 전체 정답으로 승격하지 않아야 PASS다. | 커피10%·넷플릭스20%를 찾더라도 커피07~15시·1회 대상 이용금액1만원 같은 조건이 빠지면 PARTIAL이다. 전월실적·월한도까지 필요한 조건을 설명하면 PASS다. |
| Question wins / losses / ties(질문별 개선 / 악화 / 동률) | 같은 질문·프로필에서 B의 PASS 여부를 A와 비교한다. B만 PASS면 개선, A만 PASS면 악화, 그 외 동률이다. 비율 분모는 실제 비교한 질문 수이며 미실행은 제외한다. | 4개 질문에서 PASS가 A/B 모두2개이고 질문별 결과가 같으면 개선0·악화0·동률4다. |
| Positive candidate loss(유효 카드 후보 손실) | A 검색 후보에 있던 유효 카드가 B 후보에서 사라지는지 확인한다. 낮을수록 좋고 신규 손실0이 선정 기준이다. 정답 목록 밖 유효 카드도 원문 검토 후 인정한다. | A가 찾은 NH 올원더풀의 카페10%·넷플릭스30%는 사전 정답 카드가 아니어도 유효하다. 이를 단순 오답으로 처리하면 안 된다. |

한계: main Codex의 비맹검 검토이며 사람의 독립 정답 심사·OCR 검증을 대체하지 않는다. 카드 발견(Hit@3/MRR)과 조건 완전성은 별개다. 일부 정답 목록에만 의존한 자동 점수는 진단용이다. 47번은 기존48개 출력 재사용과 통제DB view4개를 구분하며, 반복 신뢰도·자연 오류율로 일반화하지 않는다. 비용은 실제 요청과 재사용을 구분하고, 캐시가 섞인 검색 시간을 운영 지연 우열로 해석하지 않는다.

## 48번 구조화 단서 검색 보조의 적용·보존 지표

| 지표 | 의미 | 예시 |
| --- | --- | --- |
| Guidance application rate(보조 적용률) | 원문 출처와 실제 일치 fact를 확인해 보조어를 추가한 질문·프로필 조합 수 / 전체 비교 조합 수다. 높다는 이유만으로 품질이 좋다는 뜻은 아니다. 불확실한 자료는 보조하지 않는다. | 48질문×두프로필=96조합 중 benefit23·parent-child9조합에 적용하면32/96이다. |
| Known-positive answer match(자동 정답목록 일치) | 고정된 부분 정답 카드 목록과 혜택 규칙에 답변이 일치한 질문 수 / 사전 양성 질문 수다. 높을수록 좋고 개별 규칙 일치가1이다. 실제 원문 조건 완전성 통과율과 다르다. | 멜론 질문에서 정답 목록의 올바른FLEX를 찾으면1일 수 있다. 목록 밖 taptap DIGITAL의 실제 멜론50%를 찾았어도0일 수 있어 원문 대조가 필요하다. |
| Candidate loss(기존 후보 손실) | A후보20 또는 카드top3에 있던 사전 정답 카드가 B에서 빠진 질문 수다. 낮을수록 좋고 새 중요손실0을 보존 기준으로 쓴다. 분모는 같은 양성 질문 수다. | 원질문 결과의 올바른FLEX가 결합 뒤 후보20에서 밀리면 후보 손실이다. 다른 유효 카드가 대신 들어올 수 있어 이를 곧바로 질문 오답으로 세지 않는다. |
| Evidence changed(근거 변경) | 비교 질문 중 검색 근거 목록이 달라진 질문 수 / 해당 전체 질문 수다. 변화 자체에는 좋고 나쁨의 방향이 없다. | 온라인 쇼핑 카드의 혜택 청크는 남지만 전월40만원·월1만3천원 공통조건 청크가 빠질 수 있다. 이때 별도로 중요조건 손실을 확인한다. |
| Important-condition loss(중요조건 손실) | 기존 결과의 적용 여부·실적·대상금액·한도 같은 조건이 새 결과에서 사라진 경우다. 낮을수록 좋고 새 중요손실0이 선정 기준이다. 발생률을 말하려면 정해진 전체 심사 분모가 필요하다. | B근거에 롯데마트&MAXX의 발급 첫달 생활업종 할인 미제공이 있어도 전체 답변이 빼면 생성 누락이다. 요약문뿐 아니라 주장 본문·조건 배열·추천 사유를 모두 확인한다. LOCA LIKIT Shop의 전월40만원·월1만3천원 근거 자체가 빠지면 검색 근거 손실이다. |

48번은 기존48개 개발 질문의 자동 진단과 바뀐 B출력27쌍 비교, 정정 사례를 포함한7개 원문 집중 대조를 구분한다. 집중 사례를 전체 오류 빈도로 환산하지 않는다. 원질문 검색 성분을 보존해도 최종 후보/근거 예산에서 원래 정보가 밀릴 수 있다. A_budget은 조회 깊이만 늘린 진단군으로, 최고 원점수를 자동 운영 채택하지 않는다. 새 답변 생성과 재사용, API지연과 로컬 CPU 검색시간을 별도로 기록한다.

## 49번 신규 질문 확인의 요구 충족·반복 지표

| 지표 | 의미 | 예시 |
| --- | --- | --- |
| Core requirement PASS(핵심 요구 충족) | 질문에 미리 정한 핵심 의미를 모두 설명하고, 원문 근거와 모순되는 혜택·수치·조건·인용이 없는 답변이다. 높을수록 좋다. 정답 목록 밖 카드라도 원문이 실제 요구를 충족하면 인정한다. PARTIAL(일부 설명), FAIL(핵심 요구 미충족 또는 잘못된 설명), UNCERTAIN(판정 불가), 요청 실패는 PASS에 넣지 않는다. 검토한 실제 답변 수를 분모로 쓰며 미실행은 제외·별도 공개한다. | FLEX 넷플릭스6,500원 결제 질문에서 20% 할인, 건당7천원 이상, 전월30만원 등 사전 필수 설명을 모두 충족하면 PASS다. 적용되지 않는다는 결론이 맞아도 필수20% 설명이 없으면 PARTIAL이므로 단순 정답 정확도와 다르다. 카드만 찾고 최소금액을 설명하지 않아도 일부 설명이다. 조건 배열뿐 아니라 주장 본문·추천 사유를 모두 읽어야 한다. |
| Question-level wins/losses/ties(질문 단위 개선·퇴행·동률) | 같은 질문·프로필에서 각 방식의 예정3회 PASS 횟수(0~3)를 비교한다. 후보 횟수가 크면 개선, 작으면 퇴행, 같으면 동률이다. 개선은 높고 퇴행은 낮을수록 좋다. 프로필별32질문이 분모이며 각 하위유형은8질문이다. 전 회차를 실행하지 않으면 최종3회 지표를 계산하지 않고 실행된 회차 진단만 구분한다. | 기준1/3·후보3/3이면 개선1질문이다. 기준0/3·후보0/3도 동률이므로 동률을 정확한 답변이나 동등성 입증으로 해석하지 않는다. |
| New important error(새 중요 오류) | 기준 답변에서는 없던 잘못된 혜택·수치·적용조건·인용 또는 중요한 조건 손실이 후보에 나타난 출처 확인 사례다. 낮을수록 좋고 새 오류0이 확인 기준이다. 첫 회차 원문 검토에서 확인되면 해당 프로필의 남은2회 유료 실행을 중단한다. | 기준이 LPG 제외를 설명했는데 후보가 LPG도 리터당60원 할인된다고 확정하면 새 중요 오류다. 기존에도 같은 오류가 있으면 신규 퇴행과 공통 한계를 구분한다. 원문 자료가 부족하면 불확실로 남긴다. |
| Repeat consistency(반복 일관성) | 같은32질문에서 매회 신규 요청을 생성했을 때 PASS 횟수와 판정 변동을 본다.3회 모두 PASS는 관측상 안정 사례다. 단순 문장 일치율이 아니며 확률적 신뢰 보장은 아니다. 표준편차는 회차별 PASS율3개의 모집단 표준편차(분모3)로 정의하고, 실제3회가 없으면 미측정으로 남긴다. | 한 질문의 PASS/PARTIAL/PASS는2/3이다. 한 번 PASS 뒤 조기 중단됐으면1/3로 쓰지 않고 실행1회 PASS1, 남은2회 미실행으로 표시한다. |
| Paired API latency and cost(방식별 API 지연·비용) | 실제 외부 요청의 지연과 저장usage 단가 비용을 방식별 분리한다. 후보에만 필요한 parser 비용을 공유프로필 중복 없이 별도 표시하고, 답변 생성·로컬 검색·캐시 재사용을 구분한다. 낮을수록 자원 측면에서 유리하나 품질 기준을 대신하지 않는다. P95는 관측값을 정렬한 ceil(0.95×n)번째다. | 후보 답변API가10초여도 조건 추출3초와 로컬 검색이 추가될 수 있다. 배치 실행에서는 합계를 실제 웹 API 종단 지연으로 부르지 않는다. 실제 청구액·동시부하·미관측 자원은 미측정이다. |

49번의 질문 확인 자료는 같은106문서에서 새로 작성한32질문이며 새로운PDF에 대한 일반화 검사가 아니다. 평가 주석은 검색·생성 입력에 보내지 않는다.3회 신규 답변은96개 독립 질문이 아니다. 예정384논리답변과 실제API 요청·미실행을 분리하며, 확인 결과를 본 뒤 설정을 수정하면 해당 자료를 재확인 통과 근거로 재사용하지 않는다.

## 50–54 검색 경로 재검사와 출력 시간

| 지표 | 의미 | 예시와 한계 |
|---|---|---|
| Query contract regression pass — 조회 계약 회귀 통과 | 50번에 정의한 12개 오프라인 검사 중 성공 수다. 최초 10개에 독립 리뷰의 필수 필드 누락·결제일 반례 2개를 추가했다. 높을수록 좋고 12/12가 진입 조건이다. 이전 46번 고정 9사례와 분모가 다르다. | ‘커피 할인 월 몇 회인가’가 DB까지 조회되고 월 3회 이상 조건은 실제 WHERE 필터로 처리되면 해당 검사에 통과한다. 전체 자연어 추출 정확도는 아니다. |
| Eligible cards — 구조화 일치 카드 수 | 고정 질문 조건을 같은 카드에서 만족한 서로 다른 카드 수다. 크고 작음이 품질 우열은 아니며 단독 통과 점수로 쓰지 않는다. | 철도·고속버스·택시가 서로 다른 혜택 행이어도 같은 카드에 모두 있으면 1개다. 정답 여부는 원문과 질문으로 별도 판정한다. |
| Whole fact token bound pass — 전체 사실 입력 경계 통과 | 모든 필드·예외·출처를 자르지 않고 직렬화한 3,403개 사실 중 문서 4,096과 질문·문서 쌍 8,192 토큰 한도를 모두 지킨 수다. 높을수록 좋고 전부 통과해야 한다. 개발 질문 중 가장 긴 질문을 사용했다. | 커피 10% 행의 조건·횟수·월 한도·예외·출처까지 넣어 검사한다. 길이 통과가 사실 정확성이나 향후 더 긴 사용자 질문의 통과를 뜻하지 않는다. |
| Answer ready wall time — 답변 완료 경과 시간 | 논리 답변 생성 시작부터 검증·최대 1회 재시도를 포함해 완성된 답변을 반환할 때까지의 실측 초다. 실제 요청과 캐시 재생은 구분한다. 같은 품질이면 낮을수록 유리하며 운영 SLA는 정하지 않았다. | 첫 API가 5초에 잘리고 재시도가 7초면 두 요청과 검증 시간을 포함한다. 추출·검색을 미리 배치한 경우 이 값은 질의부터의 전체 응답 시간이 아니다. 첫 글자·첫 토큰·브라우저 표시 시간도 아니다. |
| Stage sum — 단계 시간 합계 | 별도로 실행한 추출·DB·임베딩·검색·재정렬·답변 생성의 측정값을 더한 참고값이다. 실제 연속 종단 실행이 아니면 end-to-end 실측으로 부르지 않는다. 같은 품질이면 낮을수록 유리하지만 통과 SLA는 없다. | 추출 2초와 별도 답변 완료 8초를 합쳐 10초로 표시할 수 있으나 공유·캐시·대기 방식 때문에 실제 채팅의 10초를 보장하지 않는다. 검색 시간에 이미 포함된 재정렬을 중복 가산하지 않는다. |
