# PickCardU RAG Lab — 팀 공유용 코드

ChatGPT Sites에서 사용하는 RAG 실험실의 공유용 소스입니다. Keyword·Vector·Hybrid·Weighted 검색, ChatGPT 로그인, 팀 공용 실험 저장·평가·비교, Codex·Work 브라우저용 Site tools를 구현합니다. 기존 `apps/main`, `apps/rag-lab`, FastAPI 서비스와 별도로 `sites/rag-lab`에서 관리합니다.

## 권한과 작업 흐름

- 팀원: 사이트에서 검색·실험 저장·비교, GitHub에서 코드 검토와 수정 PR 제안.
- 사이트 소유자: 수정안을 검토하고 운영 사이트에 배포. GitHub 코드 접근은 Sites 편집 권한을 부여하지 않습니다.
- 운영 사이트는 소유자와 초대된 이용자만 접근하며, 편집자는 별도로 추가하지 않습니다. 이용자는 앱 안의 실험 기록을 저장할 수 있지만 화면·서버 코드·배포 설정을 바꾸지는 못합니다.
- 저장소 지침에 따라 `develop`에서 `feat/*` 브랜치를 만들어 PR을 제출합니다. 공개 저장소이므로 GitHub 협업자가 아니어도 fork로 수정안을 제안할 수 있습니다.
- 이 폴더에는 운영 배포 자동화나 배포 자격 증명이 없습니다. GitHub 병합만으로 운영 사이트가 갱신되지 않습니다.

## 공개 코드와 비공개 데이터

공개하는 것은 코드, 모델 설정, 스키마·마이그레이션, 인덱스 버전 메타데이터와 재현 스크립트입니다. 다음 항목은 복사하지 않았으며 `.gitignore`로 제외합니다.

- API 키·토큰·환경 파일, 운영 계정 이메일·접근 허용 목록·운영 사이트 식별자
- D1 운영 DB 및 로컬 DB, 팀원의 질문·실험 기록·평가 결과
- `public/corpus/`의 문서 본문·질문·벡터, `tests/parity.json`, `tests/runtime-report.json`
- 모델 캐시, 로컬 도구 상태, 설치 패키지, 빌드 결과

`lib/corpus-manifest.json`은 데이터 개수·모델·해시 등 기준 메타데이터이며 실제 실험 기록이 아닙니다. `tests/runtime-smoke.mjs`의 사용자 값은 로컬 테스트용 가상 계정입니다. 라이선스 파일의 제작자 표기는 보존합니다.

커밋 전 `python sites/rag-lab/scripts/check-public-source.py`로 Git에 포함된 파일을 검사합니다. 알려진 자격 증명과 계정 식별자 패턴 및 금지된 파일 경로를 검사하는 보조 장치이며 모든 비밀을 탐지하는 보안 감사는 아닙니다. 강제 추가(`git add -f`)로 데이터 제외 규칙을 우회하지 않습니다.

## 설정을 수정할 위치

| 변경 대상 | 파일·설정 | 적용 절차 |
| --- | --- | --- |
| 청킹 크기·겹침 | 저장소 루트 `scripts/rag_pipeline/build_chunks.py`의 `--child-max-chars`, `--child-overlap-chars` | 청크와 인덱스 재생성 |
| 청킹 알고리즘 | 저장소 루트 `scripts/rag_pipeline/chunking.py`의 `chunk_document` | 기존 청킹 테스트와 새 후보 평가 |
| 임베딩 모델·revision·차원·접두어 | 이 폴더의 `lib/embedding-config.json` | 문서·평가 질문 벡터를 모두 재생성 |
| 브라우저 질문 임베딩 | `lib/embedding-worker.ts` | 문서 임베딩과 같은 모델·설정 사용 |
| 검색 계산 | `lib/search-engine.ts` | 원본 Python과 순위·점수 검증 |
| 평가 기준 | `lib/experiments.ts` | 고정 평가 질문과 지표 정의를 함께 검토 |

현재 기준은 `layout-parent-child-v2`, 청크 최대 1,600자·겹침 160자, `Xenova/multilingual-e5-small` 384차원입니다. 임베딩 실행기는 q8·mean pooling·L2 정규화·모델 tokenizer의 512토큰 제한을 사용합니다. 이 범위를 바꾸는 모델은 실행기와 브라우저 구현을 함께 수정해야 하며 설정 파일만 바꾸어 지원된다고 간주하지 않습니다. 공개 모델 다운로드 외에 유료 추론 API를 호출하지 않습니다.

저장소에는 후속 구조 기반 indexer도 있습니다. 이 Sites 구현의 현재 인덱스는 기존 `scripts/rag_pipeline`의 parent-child 형식이므로 `jobs/rag-indexer`의 다른 release 형식으로 곧바로 대체하지 않습니다.

## 로컬 실행

Node.js 22.13 이상과 Python 3.11 이상을 사용합니다. 원본 Python 검증에는 PyMuPDF와 Poppler의 `pdfinfo`가 필요합니다.

```bash
cd sites/rag-lab
npm ci
npx tsc --noEmit
npm run build
```

코드만으로 빌드할 수 있지만 검색에는 별도의 corpus가 필요합니다. 사이트 소유자가 승인한 **원문 처리 결과와 인덱스**를 비공개 경로로 전달받아 `public/corpus/`에 배치합니다. 운영 DB·사용자 기록·자격 증명은 가져오지 않습니다. 기존 결과를 재현하려면 `manifest.json`의 자산 해시를 검증하고 `lib/corpus-manifest.json`과 버전·모델·임베딩 설정을 맞춰야 합니다. 기준 원문 검증 기록은 `document-source.json`입니다.

필요한 corpus 파일은 `index.json`, `gold.json`, `e5-vectors.f32`, `gold-vectors.json`, `manifest.json`, `document-source.json`입니다. 기존 OpenAI 벡터는 서비스 실행에 필요하지 않습니다. 다만 전달받은 manifest가 추가 자산을 열거한다면 해시 검증에는 해당 파일도 필요합니다.

```bash
# 이 폴더에서 로컬 DB에 스키마만 적용
npx wrangler d1 execute site-creator-d1 --config dist/server/wrangler.json --local --persist-to .wrangler/state --file drizzle/0000_lovely_harry_osborn.sql
npm run dev
```

개발 서버는 로컬 테스트 로그인을 제공합니다. 운영 인증은 Sites가 전달하는 ChatGPT identity를 사용합니다. 로컬 DB와 운영 DB는 별개입니다. corpus가 없으면 검색은 실행되지 않으며 코드만 받은 상태를 검색 준비 완료로 간주하지 않습니다.

## 청킹·임베딩 후보 재생성

청킹은 검증된 canonical 문서가 필요합니다. 소유자가 제공하는 원문 처리 결과를 별도 작업 사본의 `data/rag/runtime/canonical`에 둡니다. 아래 과정은 기존 OCR 결과를 재사용하며 새 OCR·OpenAI 임베딩 요청을 하지 않습니다. 원문을 바꾸어 OCR이 새로 필요한 경우에는 별도로 범위와 비용을 검토합니다.

```bash
# 저장소 루트, 별도 후보 작업 사본에서 실행
python scripts/rag_pipeline/build_chunks.py --child-max-chars 1200 --child-overlap-chars 120

# Sites 형식의 BM25 자산을 생성; OpenAI 벡터 없이 동작
cd sites/rag-lab
python scripts/export-corpus.py ../.. --pdf-root ../..
node scripts/build-e5.mjs
npx tsc --noEmit
npm run build
```

`build_chunks.py`는 기존 파이프라인의 `data/rag/reports/chunk_summary.json`도 갱신합니다. 후보 평가 산출물을 이 소스 공유 PR에 함께 커밋하지 말고 별도 실험 작업에서 관리합니다. `export-corpus.py`는 원문·canonical·청크·설정 fingerprint를 검증하고 임시 메모리 SQLite에서 BM25 통계를 만듭니다. `build-e5.mjs`는 설정 파일의 모델과 revision으로 문서·질문을 임베딩하고 새 인덱스 버전을 기록합니다. 스크립트는 재현 가능한 일괄 처리와 해시 검증을 담당하므로 notebook 실행에 의존하지 않습니다.

새 corpus를 만들면 기준 메타데이터도 바뀝니다. `lib/corpus-manifest.json`과 모델 설정은 PR 검토 대상이지만 실제 본문·벡터·실험 결과는 제외합니다. 문서 목록을 바꾼 경우에는 출처 검증 기록과 `lib/document-source.json`도 검증 후 갱신해야 합니다. 과거 메타데이터를 새 corpus에 그대로 붙이지 않습니다.

## 검증과 운영 반영

- `npx tsc --noEmit`, `npm run build`: 공유용 코드 검증.
- `python scripts/verify-document-source.py /absolute/path/to/source-checkout`: 승인된 corpus와 원문 목록·해시 검증.
- `node tests/parity.mjs`: 별도로 제공된 원본 벡터·fixture가 있을 때 수행하는 과거 150개 검색 순위·점수 비교. 새 후보의 성능 평가를 대신하지 않습니다.
- `node tests/runtime-smoke.mjs`: 로컬 Worker의 네 모드·인증·사용자 간 공유·평가 재개 검증. 로컬 테스트 DB에 기록을 생성하며 운영 사이트를 대상으로 실행하지 않습니다. 결과 파일은 Git에서 제외됩니다.

검색 평가는 모델 다운로드·질문 임베딩 시간을 제외하며, 기존 Recall@K 정의는 정답 근거 적중률입니다. `gold-v1`의 정답 불일치 1건 등 기준 데이터의 제약을 함께 확인합니다. 같은 평가셋으로 반복 조정한 결과는 개발 결과이며 일반화 성능으로 단정하지 않습니다.

검토 후 소유자가 승인된 소스와 corpus를 운영용 Sites checkout에 반영하고, 운영 manifest·접근 권한을 보존한 채 배포합니다. 이 공유용 `.openai/hosting.json`에는 운영 프로젝트 ID가 없으므로 그대로 운영 설정을 덮어쓰지 않습니다. 후보 버전과 운영 버전을 구분하고 기존 실험 기록은 보존합니다.
