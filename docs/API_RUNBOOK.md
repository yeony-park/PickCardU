# PickCardU RAG API 실행·운영 가이드

## 1. 문서 목적

이 문서는 PickCardU RAG API를 실행하고 검색 index release를 활성화·교체·점검하는 절차를 설명한다. HTTP method, 요청·응답 schema와 오류 계약은 [`API_SPEC.md`](API_SPEC.md)를 기준으로 한다.

명령은 저장소 루트의 POSIX shell 예시다. 특정 Conda 환경 이름이나 가상환경 도구를 계약으로 강제하지 않으며, 팀에서 사용하는 Python 환경이나 컨테이너 실행 방식에 맞게 조정할 수 있다.

## 2. 실행 전 확인

- macOS 또는 Linux/WSL을 사용한다. 기존 release 잠금은 POSIX `fcntl`을 사용하므로 Windows PowerShell/CMD 네이티브 실행은 현재 지원하지 않는다.
- Node.js `22.13.0` 이상을 사용한다.
- Python `3.11` 이상을 사용한다.
- 사용할 Python 환경을 먼저 활성화한다. 별도 환경을 자동 생성하지 않는다.
- `/v1/search` 또는 `/v1/answer`를 실제 호출하려면 `OPENAI_API_KEY`가 필요하다.
- 현재 API에는 인증이 없고 `production` 환경 실행도 차단되어 있으므로 외부에 공개하지 않는다.

## 3. 팀 개발 환경 최초 설정

저장소 루트에서 다음 명령을 실행한다.

```bash
npm run setup
```

setup은 다음 순서로 실행되고 개발 서버를 시작하지 않는다.

1. 플랫폼, Node와 Python 버전 확인
2. 현재 Python 환경에서 필수 RAG/API 모듈을 import할 수 있는지 확인하며 패키지는 설치·변경하지 않음
3. `apps/main/package-lock.json` 기준 Node 의존성을 프로젝트 내부에 설치
4. Hugging Face의 고정 revision에서 BGE reranker 다운로드와 파일별 SHA-256 검증
5. 고정된 GitHub Release asset에서 RAG index 다운로드와 archive·manifest·내부 DB hash 검증
6. `index-release/`, 검색용 `serving/`과 `active-index.json` 준비
7. 실제 `ActiveIndexLoader`로 SQLite, FTS5, Chroma와 embedding identity 검증

현재 자산 계약은 Git에 포함된 `config/dev-assets.json`에 있다. setup은 이 계약을 읽어 BGE와 RAG release를 준비하고, 선택된 release ID와 manifest hash를 로컬 `data/rag/runtime/active-index.json`에 기록한다. BGE는 `BAAI/bge-reranker-v2-m3`의 고정 commit을 사용하고, RAG DB는 `release_9854f965cbb4ba66`을 사용한다. BGE 런타임 파일은 약 2.3GB이고 RAG release archive는 64,029,451바이트다.

setup은 활성 Python/Conda 환경에 `pip install`을 실행하지 않는다. 필수 모듈이 없거나 import할 수 없으면 해당 이름을 보고하고, 프론트 설치나 대용량 자산 다운로드 전에 중단한다. 사용자는 기존 환경 관리 정책에 맞게 의존성을 준비한 뒤 setup을 다시 실행한다.

정상 자산이 이미 있으면 대용량 다운로드를 생략하고 검증·활성 상태만 확인한다. 기존 release가 있지만 hash가 다르면 자동 삭제하거나 덮어쓰지 않고 실패한다. 새 release 준비가 실패하면 기존 `active-index.json`은 유지한다.

setup을 다시 실행해야 하는 경우는 다음과 같다.

- 처음 저장소를 받은 경우
- Python 환경을 바꾼 뒤 실행 모듈 호환성을 다시 확인하려는 경우
- 프론트 Node 의존성 계약이 변경된 경우
- `config/dev-assets.json`의 release나 BGE revision이 변경된 경우
- 로컬 자산이 누락된 경우

setup 완료 후 평소 실행은 `npm run dev`만 사용한다.

## 4. Index Builder의 Release 활성화

이 절의 `activate` 명령은 같은 컴퓨터에서 indexer로 직접 생성하고 `indexer-state.sqlite`에 등록된 release를 운영자가 전환할 때 사용한다. GitHub에서 팀 공용 release를 받는 일반 개발자는 이 명령을 사용하지 않고 `npm run setup`을 사용한다.

### 4.1 활성화 전 검사

배포 대상 `manifest.json`에서 최소한 다음 항목을 확인한다.

| 항목 | 확인 내용 |
|---|---|
| `release_id` | 활성화하려는 ID와 일치하는가 |
| `release_status` | 로더가 허용하는 `production`인가 |
| `source_validation` | OCR·원천 데이터 검증 수준이 배포 목적에 적합한가 |
| `embedding_model` | 런타임 `PICKCARDU_EMBEDDING_MODEL`과 일치하는가 |
| `embedding_dimension` | 저장된 vector와 런타임 계약이 일치하는가 |
| `strategy` | 사용할 검색 프로필과 일치하는가 |
| hash 항목 | SQLite, Chroma, corpus와 embedding 무결성 값이 존재하는가 |

`release_status=production`은 API 로더가 활성화할 수 있다는 뜻이다. OCR 검증 완료 여부는 별도 `source_validation`으로 판단한다.

### 4.2 활성화 명령

```bash
PYTHONPATH=jobs/rag-indexer/src:packages/rag-core/src \
  python -m pickcardu_indexer \
  --runtime-root data/rag/runtime \
  activate RELEASE_ID
```

`RELEASE_ID`는 배포 대상 manifest의 실제 값으로 바꾼다. 활성화는 다음 작업만 수행한다.

- `active-index.json`을 해당 release ID와 manifest SHA-256으로 원자적 교체
- indexer state DB에 활성화 이력 기록

활성화 과정에서는 OCR, 문서 embedding, 질문 embedding 또는 LLM 답변 API를 호출하지 않는다.

### 4.3 활성 포인터 확인

```bash
sed -n '1,20p' data/rag/runtime/active-index.json
sha256sum data/rag/runtime/index-release/RELEASE_ID/manifest.json
```

포인터의 `release_id`와 `manifest_sha256`이 대상 release와 일치해야 한다.

## 5. 환경 변수

| 변수 | 기본값/필수 여부 | 설명 |
|---|---|---|
| `PICKCARDU_ENV` | `development` | 현재 `production` 값은 시작 단계에서 차단된다. |
| `PICKCARDU_INDEX_RUNTIME_ROOT` | `data/rag/runtime` | active pointer와 index release가 있는 경로다. |
| `PICKCARDU_ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | 허용할 프론트엔드 Origin의 쉼표 구분 목록이다. |
| `PICKCARDU_EMBEDDING_MODEL` | `text-embedding-3-small` | active release의 embedding 모델과 일치해야 한다. |
| `PICKCARDU_LLM_MODEL` | `gpt-5.6-luna` | `/v1/answer`의 답변 생성 모델이다. |
| `PICKCARDU_BGE_MODEL_PATH` | `.cache/reranker/bge-reranker-v2-m3` | 로컬 reranker 모델 경로다. |
| `OPENAI_API_KEY` | 실제 검색·답변 시 필수 | 앱 생성과 health 확인만으로는 외부 호출이 발생하지 않는다. |

setup과 dev에서 사용할 Python이 현재 `PATH`의 `python`과 다르면 `PICKCARDU_PYTHON`에 실행 파일 경로를 지정할 수 있다.

비밀값은 `.env`를 포함한 저장소 파일에 커밋하지 않는다. 배포 환경의 secret 관리 방식을 사용한다.

## 6. FastAPI 실행

로컬에서 프론트엔드와 FastAPI를 함께 실행할 때는 저장소 루트에서 다음 명령을 사용한다.

```bash
npm run dev
```

루트 실행기는 현재 활성화된 Python 환경을 사용하며, FastAPI에 필요한 소스 경로를 내부적으로 설정한다. FastAPI 진입점은 루트 `.env`가 있으면 자동으로 읽되 이미 설정된 셸 환경변수는 덮어쓰지 않는다. Next.js는 `http://localhost:3000`, FastAPI는 `http://127.0.0.1:8000`에서 실행된다. `Ctrl+C`로 두 프로세스를 함께 종료한다.

백엔드만 별도로 실행할 때는 로컬 패키지를 먼저 설치한 뒤 다음 명령을 사용한다.

```bash
python -m pip install -e "packages/rag-core[reranker]" -e services/rag-api
python -m pickcardu_rag_api
```

백엔드 진입점도 루트 `.env`를 자동으로 읽고 기존 환경변수를 우선한다. 기본 bind 주소는 `127.0.0.1:8000`이다. 실행 환경에서 지속적으로 서비스하려면 해당 환경의 프로세스 관리자 또는 컨테이너 정책을 사용한다.

## 7. Health 점검

### 7.1 프로세스 생존 확인

```bash
curl -sS http://127.0.0.1:8000/v1/health/live
```

정상 응답:

```json
{"status":"live"}
```

### 7.2 Active release 준비 확인

```bash
curl -sS http://127.0.0.1:8000/v1/health/ready
```

정상 응답에는 실제 active release의 값이 들어간다.

```json
{
  "status": "ready",
  "release_id": "release_example",
  "profile": "card_page_section_benefit",
  "document_count": 100,
  "chunk_count": 2000
}
```

첫 `/ready` 또는 pointer 변경 후 첫 요청은 manifest, SQLite, FTS5, Chroma와 embedding identity를 검증한다. 이 과정은 로컬 파일만 읽고 외부 API를 호출하지 않는다.

## 8. 검색·답변 Smoke Test

health 확인 이후에만 실제 검색과 답변을 점검한다.

| 단계 | Endpoint | 외부 전송 |
|---|---|---|
| 검색 | `POST /v1/search` | 질문 문자열을 embedding provider로 전송 |
| 답변 | `POST /v1/answer` | 질문 embedding 호출 후, 검색 근거가 있으면 질문과 선별 근거를 LLM provider로 전송 |

실제 질문을 호출하기 전에는 테스트 질문, 호출 횟수, 전송 가능한 데이터 범위와 비용 승인을 확인한다. 요청·응답 예시는 [`API_SPEC.md`](API_SPEC.md)를 참고한다.

## 9. Release 교체와 Rollback

### 9.1 새 release로 교체

기존 active release를 유지한 상태에서 새 release를 생성·검증한 뒤 `activate`로 pointer를 교체한다. FastAPI loader는 pointer 변경을 감지하면 새 release를 다시 검증한다.

### 9.2 이전 release로 rollback

```bash
PYTHONPATH=jobs/rag-indexer/src:packages/rag-core/src \
  python -m pickcardu_indexer \
  --runtime-root data/rag/runtime \
  rollback PREVIOUS_RELEASE_ID
```

rollback도 기존 immutable release를 가리키도록 pointer를 교체하며 OCR이나 embedding을 다시 수행하지 않는다. 대상 release가 보존되어 있고 현재 코드의 loader 계약과 호환되어야 한다.

## 10. 장애 확인 순서

| 증상 | 먼저 확인할 항목 |
|---|---|
| `/live` 연결 실패 | 프로세스 실행 여부, bind 주소와 포트 |
| `/live` 200, `/ready` 503 | `active-index.json`, manifest hash, release 파일 권한·무결성, serving Chroma copy |
| `CONTRACT_MISMATCH` | 요청 profile과 active strategy, runtime/index embedding 모델 |
| `EMBEDDING_UNAVAILABLE` | `OPENAI_API_KEY`, provider 연결과 timeout |
| `RERANKER_UNAVAILABLE` | BGE 경로, 파일 권한, 로컬 모델 호환성 |
| `LLM_UNAVAILABLE` | 답변 모델 설정, API key, provider 연결과 timeout |
| `LLM_UNGROUNDED` | LLM 응답 schema와 citation의 카드·청크 소유권 |

setup 단계에서 실패하면 오류 메시지의 첫 실패 단계를 확인한다.

| setup 오류 | 확인할 항목 |
|---|---|
| 지원하지 않는 플랫폼/버전 | macOS 또는 Linux/WSL, Node 22.13+, Python 3.11+인지 확인 |
| Python runtime dependencies 오류 | 현재 선택한 Python에 보고된 모듈이 설치되어 있고 import 가능한지 확인; setup은 패키지를 자동 설치하거나 버전을 변경하지 않음 |
| BGE hash mismatch | 기존 `.cache/reranker/bge-reranker-v2-m3`가 완전한지 확인; setup은 손상된 기존 폴더를 자동 삭제하지 않음 |
| release archive hash mismatch | 네트워크 프록시나 불완전 다운로드 여부 확인; 부분 다운로드는 활성화되지 않음 |
| existing release is invalid | 기존 immutable release가 명세와 다름; 임의 삭제 전에 경로와 보존 필요성을 확인 |
| loader validation 실패 | manifest, SQLite/FTS5, Chroma, serving marker와 active pointer 중 보고된 항목 확인 |

오류 응답의 세부 계약과 `retryable` 값은 [`API_SPEC.md`](API_SPEC.md)의 오류 계약을 기준으로 한다.
