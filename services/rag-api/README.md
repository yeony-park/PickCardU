# RAG API

`apps/main`과 `apps/rag-lab`이 함께 사용하는 FastAPI 서비스입니다. 공개 사용자 경로는 `/v1`, developer 전용 Lab은 `/internal/v1`에만 둡니다.

서비스는 `active-index.json`을 요청마다 검증하고, 해당 요청 동안 하나의 release handle만 사용합니다. `index-release/<id>/corpus.sqlite`는 URI read-only FTS5로, `serving/<id>/<tree-hash>/chroma`는 vector 검색으로 열며 검색·답변 동작은 `pickcardu-rag`에 위임합니다. 활성 production release가 없어도 auth/profile/conversation API는 동작하고 catalog/chat/Lab 검색만 503으로 차단됩니다.

```bash
PYTHONPATH=services/rag-api/src:packages/rag-core/src \
  conda run -n skn25 python -m pickcardu_rag_api
```

주요 설정은 `PICKCARDU_DB_PATH`, `PICKCARDU_INDEX_RUNTIME_ROOT`, `PICKCARDU_ALLOWED_ORIGINS`, `PICKCARDU_SEED_ACCOUNTS_JSON`, `OPENAI_API_KEY`입니다. 짧은 로컬 seed 비밀번호는 `PICKCARDU_ENV=development`가 명시된 경우에만 허용됩니다. 현재 확정 배포 범위는 로컬 단일 프로세스뿐이며 `PICKCARDU_ENV=production`은 시작 단계에서 거부됩니다. 명시적 공개 승인, Secure cookie, HTTPS Origin allowlist는 후속 production 구현의 필요조건이며 제품·법적 공개 승인을 대신하지 않습니다.
