# Experiment 07 Output

`07_goldset_vision_upstage_comparison.ipynb`의 수기 정답셋 기반 OpenAI Vision·Upstage OCR 평가 결과다. 현재 유효한 실행은 아래 폴더 하나다.

노트북에는 OpenAI Vision OCR 셀과 Upstage OCR 셀이 분리되어 있다. 기본 실행 플래그는 모두 `False`이며, 새 OCR은 `OCR_RUN_ID`의 별도 실행 폴더에 저장된다.

## 구성

| 파일/폴더 | 설명 |
|---|---|
| `2026-07-25_live_ocr/` | 현재 `data/raw/` PDF 5개를 새로 OCR한 실행 결과다. |
| `2026-07-25_live_ocr/raw/` | OpenAI Vision·Upstage의 제공사별 OCR 응답 캐시다. |
| `2026-07-25_live_ocr/evaluation_v4/` | 공통 TXT, 예측 필드 JSON, gold 대비 평가 CSV가 있는 최종 평가 결과다. |

원본 PDF와 `data/ocr_benchmark/gold/` 정답셋은 이 폴더에 복사하지 않으며 수정하지 않는다.
