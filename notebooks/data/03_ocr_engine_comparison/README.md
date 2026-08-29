# Experiment 03 Output

`03_ocr_engine_comparison.ipynb`의 실행 결과다.

## 구성

| 경로 또는 파일 | 설명 |
|---|---|
| `claude_raw/` | Claude Vision의 페이지별 API 응답 원문이다. |
| `paddle_raw/`, `paddle_cache/` | PaddleOCR 원문 응답과 재사용 캐시다. |
| `upstage_raw/` | Upstage Document Parse API 응답 원문이다. |
| `rendered_pages/` | OCR 입력·검토용으로 렌더링한 PDF 페이지 이미지다. |
| `comparison_manifest.json` | 표본 PDF·페이지·엔진 실행 정보를 기록한다. |
| `comparison_metrics.json` | 비교 지표의 중간 산출물이다. |
| `manual_full_and_field_metrics.json` | Vision/Paddle/Upstage의 최신 전체 페이지·필드 정답 기준 지표다. |
| `claude_manual_metrics.json` | Claude Vision의 같은 기준 지표다. |
| `manual_field_metrics.json` | 필드 정답 관련 중간 지표다. |
| `vision_upstage_validation_results.json` | Vision·Upstage 검증 실험 결과다. |
| `03_manual_full_page_gold.json` | 10페이지 전체 텍스트 원본 대조 정답이다. |
| `03_manual_field_gold.json` | 같은 표본의 수동 필드 정답이다. |
| `03_vision_paddle_upstage_gold.json` | 3엔진 비교에 사용한 공통 정답 데이터다. |

여기의 CER/WER은 읽기 순서와 부가 텍스트에도 영향을 받으므로 단독 순위 지표가 아니다.


| `03_manual_field_gold.json` | 구조화된 평가 또는 설정 자료. |
| `03_manual_full_page_gold.json` | 구조화된 평가 또는 설정 자료. |
| `03_vision_paddle_upstage_gold.json` | 구조화된 평가 또는 설정 자료. |
| `claude_manual_metrics.json` | 구조화된 평가 또는 설정 자료. |
| `claude_raw/` | 직속 하위 자료를 보관하는 폴더다. |
| `comparison_manifest.json` | 실행 무결성·재현 확인 자료. |
| `comparison_metrics.json` | 구조화된 평가 또는 설정 자료. |
| `manual_field_metrics.json` | 구조화된 평가 또는 설정 자료. |
| `manual_full_and_field_metrics.json` | 구조화된 평가 또는 설정 자료. |
| `paddle_raw/` | 직속 하위 자료를 보관하는 폴더다. |
| `rendered_pages/` | 직속 하위 자료를 보관하는 폴더다. |
| `upstage_raw/` | 직속 하위 자료를 보관하는 폴더다. |
| `vision_upstage_validation_results.json` | 구조화된 평가 또는 설정 자료. |
