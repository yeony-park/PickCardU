# RAG Indexer

운영 인덱서는 source manifest → 독립 Luna/Upstage JSON lane → grounding·관계·정규화 검증 → provider-neutral canonical facts → `benefit_hierarchy` → immutable FTS5+Chroma release 순서로 실행합니다. OCR·임베딩의 live adapter는 구현하지 않았으며, 명시적 확인 플래그로도 외부 호출하지 않고 fail-closed 합니다.

현재는 새 canonical fixture 기반의 10-card closed MVP 경계만 검증합니다. 106-card 전체 실행은 비용 승인이 있기 전에는 실행하지 않습니다. 기존 개발 corpus/chunks/index와 notebook은 runtime 입력이 아닙니다.

## Install and use

```bash
conda run -n skn25 python -m pip install -e jobs/rag-indexer --no-deps
conda run -n skn25 python -m pickcardu_indexer run \
  --source-manifest fixtures/source-manifest.json \
  --luna-json-dir fixtures/luna \
  --upstage-json-dir fixtures/upstage \
  --fake-vectors
```

The test-only `--fake-vectors` option is explicit; without an approved injected embedding adapter, publishing is blocked. Runtime paths are:

Live embedding adapter is unavailable; no activatable production release can currently be built.

```
data/rag/runtime/indexer-state.sqlite
data/rag/runtime/index-release/<release-id>/{manifest.json,corpus.sqlite,chroma/}
data/rag/runtime/serving/<release-id>/<chroma-tree-sha256>/{version.json,chroma/}
data/rag/runtime/active-index.json
```

Commands: `run`, `status`, `review list`, `review show`, `review resolve`, `activate`, and `rollback`. `rollback` updates only the active pointer. A release is published only when scoped documents are canonical-approved and have no unresolved reviews; `--allow-partial` lists excluded documents in the release manifest.

Finalized release files, including `chroma/`, are chmod read-only. Chroma 1.5.5 reopens its local store with write intent, so it is never opened from the release. Under a release-specific file lock, the indexer copies it to a unique staging directory, validates identity/dimension there, and atomically installs a writable content-addressed serving version. An existing version is checksum-revalidated and reused without deletion or replacement. Any source-hash or serving validation failure blocks publication.

`source-manifest.json` is `{"documents":[{"document_id":"issuer/card","source_pdf":"relative-or-absolute.pdf"}]}`. Each provider directory has one `<issuer__card>.json` containing its own `document_id`, OCR `pages` (`page`, `text`), and structured `facts` with `target`, `condition`, `value`, `unit`, optional relation fields, and an own-lane `evidence` (`page`, `quote`). The indexer does not read either provider's artifact from the other lane.

Provider JSON also requires `provider`, `source_pdf_sha256`, `provenance={endpoint,model,config_hash}`, and `identity={issuer_name,card_name,issuer_evidence,card_evidence}`; every identity evidence is an own-lane page/quote reference. A review resolution requires both `selected_provider` and `selected_identity_provider`, a canonical `identity` with exact per-lane identity evidence and `supports_selected`, and the complete selected-provider `canonical` relation set. `rejected_relations` contains exactly the non-selected lane's tuple-set difference as `{provider,tuple,reason}` entries. Missing, extra, duplicate, mixed-lane, or unsupported facts and identities fail closed.
