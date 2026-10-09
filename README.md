# PickCardU

카드 상품안내서 OCR 벤치마크와 근거 기반 검색·생성 파이프라인입니다.

## 로컬 개발 실행

macOS 또는 Linux/WSL에서 Node.js 22.13 이상과 Python 3.11 이상을 준비합니다. `apps/main/package.json`의 프론트 패키지와 Python RAG/API 의존성은 각자 사용하는 환경 관리 방식으로 미리 준비해야 합니다. 사용할 Python 환경을 활성화하고 저장소 루트에서 다음 명령을 순서대로 실행합니다.

```bash
npm run setup
npm run dev
```

`npm run setup`은 프로젝트의 프론트 패키지 설치 여부와 현재 Python 환경의 필수 RAG/API 모듈을 확인한 뒤 고정 BGE reranker와 고정 RAG index release를 다운로드·검증합니다. `npm ci`, `npm install`, `pip install`을 실행하거나 Python·Conda 환경을 만들지 않습니다. 기존 프론트/Python 패키지를 삭제·재설치·업데이트하지 않으며, 누락되면 이름을 안내하고 자산 다운로드 전에 중단합니다. BGE는 약 2.3GB이고 RAG release 다운로드 파일은 약 64MB이므로 최초 실행에는 시간이 걸릴 수 있습니다. 정상 자산이 이미 있으면 재사용하며 검색용 serving과 활성 포인터 등 로컬 자산 설정도 준비합니다. setup 자체는 OpenAI API를 호출하지 않습니다.

최초 설정이 끝난 뒤 평소에는 저장소 루트에서 다음 명령만 실행합니다.

```bash
npm run dev
```

`config/dev-assets.json`의 release가 변경되거나 의존성을 준비한 뒤 다시 확인하려면 `npm run setup`을 실행합니다. 프론트/Python 검사에 실패하면 사용할 환경의 관리 방식에 따라 필요한 패키지를 직접 준비한 뒤 setup을 다시 실행합니다. 프론트 검사는 패키지 메타데이터의 존재·가독성 확인이며, 설치 버전 일치나 전체 의존성 호환성을 보장하지 않습니다. 버전이 다르더라도 자동 교체하지 않으며 실행·빌드 검증은 별도로 진행합니다. `PICKCARDU_PYTHON`을 지정하지 않으면 setup과 dev 모두 현재 `PATH`의 `python`을 사용합니다. 루트 `.env`와 `OPENAI_API_KEY`는 Git으로 배포하지 않으므로 팀원이 각자 준비해야 합니다.

`npm run dev`는 Next.js와 FastAPI를 함께 실행합니다. FastAPI 진입점은 루트 `.env`를 자동으로 읽고 기존 셸 환경변수를 우선합니다.

- 서비스 화면: `http://localhost:3000`
- FastAPI readiness: `http://127.0.0.1:8000/v1/health/ready`
- 종료: 실행한 터미널에서 `Ctrl+C`

상세한 자산 검증, release 활성화와 장애 확인 방법은 [RAG API 실행·운영 가이드](docs/API_RUNBOOK.md)를 참고합니다.

## 로컬 대화 저장

채팅은 익명 HttpOnly 브라우저 쿠키로 식별하고 RAG 인덱스와 별도 SQLite(`data/chat/runtime/chat.sqlite`, `PICKCARDU_CHAT_DB_PATH`로 변경 가능)에 저장합니다. 실제 채팅 API 사용 시 생성하며 setup은 이 DB를 다운로드하거나 환경을 설치하지 않습니다. 쿠키 삭제/만료, 다른 브라우저·PC에서는 기존 대화가 자동 복원되지 않습니다. 로컬 평문 저장이며 자동 삭제나 계정 로그인은 없습니다.

브라우저는 같은 출처 `/api/chat` proxy를 사용합니다. 최초 세션의 다중 탭 초기화에는 Web Locks가 필요하며 기능 미지원 환경은 안내 후 저장형 채팅을 중단합니다. localhost 또는 HTTPS 접속을 사용합니다. 설문은 대화별로 처음 한 번 저장하고 새 채팅에서 초기화합니다. My Page 등록 원본은 브라우저 localStorage로 유지하며 일반 질문은 전체 검색, 개인화·내 카드 질문에서만 설정을 참고합니다. 설정 정보가 있는 첫 질문은 의도 판별 rewrite1회가 추가되고, 후속 질문은 최근 완료 대화 최대2쌍을 사용하는 기존 rewrite1회에 판별을 통합합니다. 조회/복원/완료 결과 재전송은 유료 API를 호출하지 않습니다. 상세 계약과 재시도 제약은 [API 명세](docs/API_SPEC.md#8-저장형-채팅-http-계약)를 참고합니다.

채팅 schema v2는 기존 v1 DB를 첫 접근 때 비파괴 migration합니다. 기존 DB를 쓰는 코드 업그레이드 전에는 [대화 DB 백업·migration·복구 절차](docs/API_RUNBOOK.md#schema-v2-적용과-복구)를 확인하세요. RAG DB와 설치 환경은 이 변경의 대상이 아닙니다.

## 저장소 구조

- `apps/main`: 실제 서비스 UI. Next.js 기반이며 Vercel 배포 대상입니다.
- `apps/rag-lab`: 검색·생성·평가용 팀 내부 UI 예정 위치입니다.
- `services/rag-api`: 두 UI가 함께 사용하는 FastAPI 서비스입니다.
- `packages/rag-core`: 검색·재정렬·프롬프팅·평가 공통 로직입니다.
- `packages/contracts`: UI와 API 사이의 OpenAPI 계약과 생성 타입입니다.
- `jobs/rag-indexer`: OCR·청킹·임베딩·인덱스 생성 작업 예정 위치입니다.
- `data`: 원본 및 평가 데이터이며 앱 배포 산출물에는 포함하지 않습니다.
- `infra`: 배포 및 인프라 설정 예정 위치입니다.

현재 Python RAG 파이프라인은 동작 경로를 보존하기 위해 `scripts/rag_pipeline`에 유지하며, API 경계가 확정된 뒤 단계적으로 옮깁니다.

- [전수 파싱 → 검증 → parent-child 청킹 → 하이브리드 검색 → 생성 실행 가이드](docs/rag_pipeline.md)
- [기존 파서 비교와 106문서/617페이지 전수 실행 결과](data/rag/reports/pipeline_performance.md)
- [팀 공유용 단계별 RAG 실행 결과 HTML](data/rag/reports/rag_pipeline_dashboard.html)
- [로컬 자연어 검색·Luna 답변 테스트 HTML](data/rag/reports/rag_search_tester.html)

A RAG-based card recommendation service that prioritizes user-owned cards before suggesting new ones
