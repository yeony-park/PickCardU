# 08 OCR model and runtime comparison

`08_ocr_model_runtime_comparison.ipynb`의 실행 산출물만 저장한다. 기존 07의 OpenAI Vision(`gpt-5.4-mini`)과 Upstage 결과는 이 폴더로 복사하지 않고, 08이 읽기 전용 기준선으로 참조한다.

## Layout

- `runs/<run_id>/rendered_pages/`: API·CLI에 동일하게 전달한 2× 렌더링 PNG
- `runs/<run_id>/raw/<config>/`: API 원본 응답 JSON 또는 Codex CLI 최종 OCR TXT
- `runs/<run_id>/events/<config>/`: Codex CLI JSONL 이벤트와 표준 오류
- `runs/<run_id>/evaluation/`: 공통 TXT, 필드 JSON, 전체·필드 지표 CSV
- `runs/<run_id>/run_manifest.json`: 프롬프트, 모델, 입력 문서, 기준선 경로

`<config>`는 `api_terra_high`, `api_luna_high`, `codex_cli_terra`, `codex_cli_luna` 중 하나다. Codex CLI의 이미지 detail은 명시 옵션을 확인하지 못했으므로 manifest에 `unknown`으로 기록한다.

## Execution

기본값은 OCR을 실행하지 않는다. 5개 카드 전체 실행은 예를 들어 다음처럼 구성 목록을 지정한다.

```bash
OCR08_RUN_CONFIGS=api_terra_high,api_luna_high,codex_cli_terra,codex_cli_luna \
conda run -n skn25 jupyter execute --inplace \
  notebooks/08_ocr_model_runtime_comparison.ipynb
```

새 실행은 `OCR08_RUN_ID`로 구분한다. 같은 ID에서 기본값 `OCR08_OVERWRITE=false`는 기존 원문을 보존하고 건너뛴다.


## 구성

| 항목 | 설명 |
|---|---|
| `runs/` | 실행 ID별 렌더링 입력·원본 응답·평가 산출물을 보관. |
