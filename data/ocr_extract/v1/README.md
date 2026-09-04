# OCR Extract v1

106개 카드 PDF, 617쪽의 최종 Luna·Upstage OCR extract 산출물이다. 구조화 결과와 API 원응답, 실패·중복 캐시, SQLite 실행 상태는 포함하지 않는다.

팀원이 구조화 단계부터 이어서 실행하려면 저장소 루트에서 다음 명령으로 캐시를 복원한다.

```bash
mkdir -p data/rag/runtime
cp -a data/ocr_extract/v1/ocr-cache data/rag/runtime/
```

복원 전 파일 무결성은 다음 명령으로 확인한다.

```bash
cd data/ocr_extract/v1
sha256sum -c checksums.sha256
```

- `pages.json`: 파이프라인이 읽는 페이지별 OCR 결과와 provenance
- `ocr.txt`: 사람이 확인하기 위한 페이지 구분 텍스트
- `source-manifest.json`: 입력 PDF 106개의 경로·SHA-256·페이지 수
- `artifact-manifest.json`: 고정된 OCR 설정과 산출물 범위

