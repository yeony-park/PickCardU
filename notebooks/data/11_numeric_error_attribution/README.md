# 11. Numeric Error Attribution

`11_numeric_error_attribution.ipynb`의 오프라인 수치 오류 귀속 산출물이다. 10번 평가에서 실패한 수치 라벨을 수기 gold, 저장된 OCR 페이지 문맥, 저장된 구조화 JSON 순서로 비교한다. API는 다시 호출하지 않는다.

## 산출물

- `latest_run.json`: 최신 유효 실행의 `summary.json` 경로
- `runs/<run_id>/numeric_error_attribution.csv`: 수치 오답 라벨별 정답·OCR·JSON 값, 근거 문맥, 오류 단계
- `runs/<run_id>/root_issue_summary.csv`: 한 실행 안에서 같은 원인이 중복 라벨에 반영된 사례를 묶은 원인 발생 요약. 같은 원인이 다른 반복 실행에서 재발하면 각각 센다.
- `runs/<run_id>/attribution_by_run.csv`: 모델·반복 실행별 OCR/JSON/표 규칙 오류 개수
- `runs/<run_id>/summary.json`: 전체 집계, 판정 정의, 한계

현재 유효 실행은 `runs/20260810T060240Z/`다. 수기 gold와 structured gold가 정확하다는 전제의 감사 결과이며, 새로운 오류 유형은 근거 확인 후 귀속 규칙을 추가해야 한다.


## 구성

| 항목 | 설명 |
|---|---|
| `latest_run.json` | 현재 폴더의 실행·검증 또는 산출물 자료다. |
| `runs/` | 직속 하위 자료 또는 모듈 폴더다. |
