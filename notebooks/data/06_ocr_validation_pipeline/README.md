# Experiment 06 Output

`06_ocr_validation_pipeline.ipynb`의 출력 폴더다. 현재 BC·NH 20개 PDF, 46페이지에 대해 Vision과 Upstage를 실행한 결과가 들어 있다.

## 구성

| 경로 또는 파일 | 설명 |
|---|---|
| `raw/` | 엔진별 수정하지 않은 OCR 응답을 묶는 상위 폴더. |
| `document_manifest.json` | 실행 대상 PDF, 카드사, 페이지 수, 문서 해시를 기록한다. |
| `raw/vision/` | Vision OCR의 페이지별 원문 응답 캐시다. |
| `raw/upstage/` | Upstage의 PDF별 원문 응답 캐시다. |
| `raw/claude/` | Claude를 실행했을 때 생성되는 페이지별 원문 응답 캐시다. 현재 BC·NH 실험에서는 Claude를 실행하지 않았다. |
| `normalized/page_records.json` | 엔진별 페이지 텍스트를 정규화하고 비교용 핵심 사실을 추출한 레코드다. |
| `validation/validation_results.json` | 페이지별 엔진 존재 여부, 사실 불일치, 자동 통과 후보/검토 상태다. |
| `validation/review_queue.json` | 자동으로 통과하지 못한 페이지 목록이다. |
| `validation/runtime_errors.json` | OCR 호출 중 발생한 런타임 오류 기록이다. |

캐시가 있으면 노트북 재실행 시 같은 OCR API를 다시 호출하지 않는다. `auto_candidate`는 현재 핵심 사실 규칙 일치일 뿐, 전체 페이지 정확도 확정이 아니다.


| `document_manifest.json` | 실행 무결성·재현 확인 자료. |
| `normalized/` | 직속 하위 자료를 보관하는 폴더다. |
| `validation/` | 직속 하위 자료를 보관하는 폴더다. |
