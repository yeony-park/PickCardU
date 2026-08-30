# API Contracts

FastAPI OpenAPI가 HTTP 계약의 단일 원본입니다. 아래 명령은 `openapi.yaml`과 `generated/api.ts`를 결정적으로 생성하며, `--check`는 drift가 있으면 실패합니다.

```bash
PYTHONPATH=services/rag-api/src:packages/rag-core/src conda run -n skn25 python packages/contracts/generate.py
PYTHONPATH=services/rag-api/src:packages/rag-core/src conda run -n skn25 python packages/contracts/generate.py --check
```
