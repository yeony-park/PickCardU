# RAG Indexer

인덱스 준비 파이프라인은 source manifest → 독립 Luna/Upstage JSON lane → 검증 → provider-neutral canonical facts → 청킹 → immutable SQLite FTS5+Chroma release 순서로 실행합니다. live OCR adapter는 아직 구현하지 않았으므로 검증된 로컬 OCR JSON 두 lane이 필요합니다. 문서 임베딩은 `--confirm-embedding`을 명시한 실행에서만 OpenAI `text-embedding-3-small`을 호출하며, 그 외에는 외부 전송 없이 canonical 단계에서 멈추거나 명시적인 test-only vector를 사용합니다.

OCR 검증은 서로 다른 목적의 세 단계입니다.

1. **OCR끼리 비교**: 같은 페이지의 Luna/Upstage 텍스트, 숫자, token Jaccard를 비교해 차이를 찾습니다. 두 결과 중 무엇이 정답인지는 이 비교만으로 결정하지 않습니다.
2. **각 OCR과 자체 JSON 비교**: JSON의 카드명·발급사·혜택·숫자·조건이 해당 OCR 페이지의 인용문에 실제로 있는지 검사합니다. JSON 변환 중 생긴 누락이나 추가를 차단하는 단계입니다.
3. **두 JSON 비교**: 양쪽에서 정규화한 카드 identity와 혜택 관계 tuple을 비교합니다. 서로 다르면 자동 선택하지 않고 review resolution이 완료될 때까지 publish를 막습니다.

청킹 프로필은 두 개를 독립적으로 만들 수 있습니다. 두 프로필 모두 FTS5와 vector 검색에는 `발급사 | 카드명 | 섹션 제목 + 혜택 본문`인 `retrieval_text`를 사용하고, 원문 혜택 text와 출처 정보는 별도로 보존합니다.

- `card_page_section_benefit`: card/page/section/benefit을 만들고 section·benefit을 검색합니다. 현재 canonical fixture에는 원문 heading tree가 없으므로 section 이름은 fact의 `target`을 사용합니다. 실제 heading을 복원한 완성형은 아닙니다.
- `parent_child_bundle`: 같은 `target`의 benefit들을 bundle 부모 아래 연결하고 bundle·benefit을 검색합니다. 실험 후보이며 기본 프로필을 대체한 것으로 간주하지 않습니다.

현재는 새 canonical fixture 기반의 10-card closed MVP 경계만 검증합니다. 106-card 전체 실행은 비용 승인이 있기 전에는 실행하지 않습니다. 기존 개발 corpus/chunks/index와 notebook은 runtime 입력이 아닙니다.

## Install and use

```bash
conda run -n skn25 python -m pip install -e jobs/rag-indexer --no-deps
conda run -n skn25 python -m pickcardu_indexer run \
  --source-manifest fixtures/source-manifest.json \
  --luna-json-dir fixtures/luna \
  --upstage-json-dir fixtures/upstage \
  --profile card_page_section_benefit \
  --fake-vectors
```

실제 문서 임베딩 release는 `OPENAI_API_KEY`를 환경 변수로 제공하고 `--fake-vectors` 대신 `--confirm-embedding`을 사용합니다. 이 플래그는 각 청크의 `retrieval_text`가 외부 OpenAI API로 전송된다는 명시적 승인입니다. API key는 manifest나 출력에 저장하지 않습니다. `--fake-vectors`는 test-only이며 활성화할 수 없습니다.

```bash
conda run -n skn25 python -m pickcardu_indexer run \
  --source-manifest fixtures/source-manifest.json \
  --luna-json-dir fixtures/luna \
  --upstage-json-dir fixtures/upstage \
  --profile card_page_section_benefit \
  --confirm-embedding
```

Runtime paths are:

```
data/rag/runtime/indexer-state.sqlite
data/rag/runtime/index-release/<release-id>/{manifest.json,corpus.sqlite,chroma/}
data/rag/runtime/serving/<release-id>/<chroma-tree-sha256>/{version.json,chroma/}
data/rag/runtime/active-index.json
```

Commands: `run`, `status`, `review list`, `review show`, `review resolve`, `activate`, and `rollback`. `rollback` updates only the active pointer. A release is published only when scoped documents are canonical-approved and have no unresolved reviews; `--allow-partial` lists excluded documents in the release manifest.

Finalized release files, including `corpus.sqlite` and `chroma/`, are chmod read-only. The manifest binds the raw SQLite SHA-256 as well as its logical chunk corpus hash. Chroma 1.5.5 reopens its local store with write intent, so it is never opened from the release. Under a release-specific file lock, the indexer copies it to a unique staging directory, validates chunk identity, dimension, collection metadata, and ordered embedding SHA-256, then atomically installs a writable content-addressed serving version. The immutable source tree keeps its raw file-tree SHA-256; the writable serving copy is checked by logical embedding identity because Chroma may legitimately update local store files when opened. Any source-hash or serving validation failure blocks publication.

`source-manifest.json` is `{"documents":[{"document_id":"issuer/card","source_pdf":"relative-or-absolute.pdf"}]}`. Each provider directory has one `<issuer__card>.json` containing its own `document_id`, OCR `pages` (`page`, `text`), and structured `facts` with `target`, `condition`, `value`, `unit`, optional relation fields, and an own-lane `evidence` (`page`, `quote`). The indexer does not read either provider's artifact from the other lane.

Provider JSON also requires `provider`, `source_pdf_sha256`, `provenance={endpoint,model,config_hash}`, and `identity={issuer_name,card_name,issuer_evidence,card_evidence}`; every identity evidence is an own-lane page/quote reference. A review resolution requires both `selected_provider` and `selected_identity_provider`, a canonical `identity` with exact per-lane identity evidence and `supports_selected`, and the complete selected-provider `canonical` relation set. `rejected_relations` contains exactly the non-selected lane's tuple-set difference as `{provider,tuple,reason}` entries. Missing, extra, duplicate, mixed-lane, or unsupported facts and identities fail closed.
