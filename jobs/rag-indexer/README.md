# RAG Indexer

인덱스 준비 파이프라인은 두 명령으로 분리합니다. `ocr`은 source manifest → 독립 Luna/Upstage OCR → 공통 Luna 구조화 → 검증 → provider-neutral canonical facts까지 수행하고, `index`는 승인된 canonical facts → 청킹 → immutable SQLite FTS5+Chroma release를 만듭니다. OCR 차이는 구조화를 막지 않고 결과의 `ocr_difference_documents`와 검증 artifact에 남깁니다. 숫자·조건·identity·근거 또는 두 구조화 JSON의 관계가 다르면 review resolution 전까지 production release를 막습니다.

OCR 검증은 서로 다른 목적의 세 단계입니다.

1. **OCR끼리 비교**: 같은 페이지의 Luna/Upstage 텍스트, 숫자, token Jaccard를 비교해 차이를 찾습니다. 두 결과 중 무엇이 정답인지는 이 비교만으로 결정하지 않습니다.
2. **각 OCR과 자체 JSON 비교**: JSON의 카드명·발급사·혜택·숫자·조건이 해당 OCR 페이지의 인용문에 실제로 있는지 검사합니다. JSON 변환 중 생긴 누락이나 추가를 차단하는 단계입니다.
3. **두 JSON 비교**: 양쪽에서 정규화한 카드 identity와 혜택 관계 tuple을 비교합니다. 서로 다르면 자동 선택하지 않고 review resolution이 완료될 때까지 publish를 막습니다.

청킹 프로필은 두 개를 독립적으로 만들 수 있습니다.

- `card_page_section_benefit`: card/page/section/benefit을 만들고 section·benefit을 검색합니다. 현재 canonical fixture에는 원문 heading tree가 없으므로 section 이름은 fact의 `target`을 사용합니다. 실제 heading을 복원한 완성형은 아닙니다.
- `parent_child_bundle`: 검증된 OCR 원문의 Markdown H1~H6를 계층으로 만들고 각 노드의 직접 본문만 최대 4,000자, overlap 없이 검색합니다. 검색문은 `발급사 + 카드명 + 전체 제목 경로 + 직접 본문`입니다. D20 이후 같은 카드의 결정론적 1-hop 근거를 최대 5개 묶어 모든 질의에 BGE를 적용합니다. 과거 `STRUCT-D20-K3` 개발 후보를 재현하지만 production 승격을 뜻하지 않습니다. 제목이 하나도 없는 OCR lane은 구조 손실로 보고 release를 차단합니다.

live OCR은 과거 전수 처리 계약대로 원본 PDF를 PyMuPDF 200 DPI PNG로 렌더링해 최대 6페이지씩 Luna에 전송하고, 원본 PDF는 Upstage에 전송합니다. Luna 배치 응답은 즉시 별도 체크포인트로 저장되므로 중단 후에는 완료된 배치를 재사용합니다. 두 OCR 텍스트는 서로 섞지 않은 별도 요청으로 같은 Luna 구조화 모델에 전송합니다. 외부 호출은 두 provider 승인 플래그와 API key가 모두 있어야 시작합니다. provider raw와 parsed structure를 각각 불변 체크포인트로 저장하므로 후속 parsing이 실패해도 같은 성공 응답을 재사용합니다. 손상 PDF는 해당 문서만 blocked로 기록하고 다음 문서를 계속 처리합니다. 106-card 전체 실행은 전송 범위와 비용 승인 전에는 실행하지 않습니다. 기존 개발 corpus/chunks/index와 notebook은 runtime 입력이 아닙니다.

Luna OCR·구조화의 요청별 출력 한도는 reasoning `max`가 JSON을 만들기 전에 작은 고정 한도를 모두 쓰는 일을 막기 위해 모델 상한인 128,000토큰으로 둡니다. 상한 전체가 아니라 실제 사용량만 과금됩니다.

## Install and use

```bash
conda run -n skn25 python -m pip install -e jobs/rag-indexer --no-deps
conda run -n skn25 pickcardu-indexer ocr \
  --source-manifest fixtures/source-manifest.json \
  --luna-json-dir fixtures/luna \
  --upstage-json-dir fixtures/upstage

conda run -n skn25 pickcardu-indexer index \
  --run-id <ocr 명령이 반환한 run_id> \
  --profile card_page_section_benefit \
  --fake-vectors
```

live OCR은 `OPENAI_API_KEY`, `UPSTAGE_API_KEY`를 환경 변수로 제공하고 아래처럼 실행합니다. `--confirm-luna`는 원본 PDF를 렌더링한 200 DPI 페이지 이미지와 두 OCR 텍스트의 OpenAI 전송, `--confirm-upstage`는 원본 PDF의 Upstage 전송을 승인합니다. 불변 provider 원응답과 단계별 checkpoint는 `data/rag/runtime/ocr-cache/`, run별 OCR text·JSON·검증 view는 `data/rag/runtime/working/<run-id>/` 아래에 남고 Git에는 포함하지 않습니다. OCR cache key와 구조화 cache key는 분리되어 구조화 설정만 바꾼 새 run이 원본 PDF OCR을 다시 호출하지 않습니다.

```bash
conda run -n skn25 pickcardu-indexer ocr \
  --source-manifest fixtures/source-manifest.json \
  --confirm-luna \
  --confirm-upstage

conda run -n skn25 pickcardu-indexer index \
  --run-id <run-id> \
  --profile card_page_section_benefit \
  --confirm-embedding
```

106개 source는 기존 `data/rag/manifest.json`을 직접 사용할 수 있습니다. 외부 OCR·구조화와 embedding 전송 승인을 각각 받은 뒤에만 실행합니다.

```bash
conda run -n skn25 pickcardu-indexer ocr \
  --source-manifest data/rag/manifest.json \
  --confirm-luna \
  --confirm-upstage

conda run -n skn25 pickcardu-indexer index \
  --run-id <run-id> \
  --profile parent_child_bundle \
  --confirm-embedding
```

`--confirm-embedding`은 각 청크의 `retrieval_text`를 OpenAI `text-embedding-3-small`로 전송한다는 승인입니다. API key는 artifact, manifest 또는 출력에 저장하지 않습니다. `--fake-vectors`는 test-only이며 활성화할 수 없습니다. 검토 중인 문서가 있을 때 `--allow-preview`를 명시하면 승인 문서만 포함한 `preview` release를 만들 수 있지만 `activate`는 production release만 허용합니다.

Runtime paths are:

```
data/rag/runtime/indexer-state.sqlite
data/rag/runtime/ocr-cache/<document>/<provider>/<source-sha>/<ocr-config-sha>/
data/rag/runtime/working/<run-id>/
data/rag/runtime/index-release/<release-id>/{manifest.json,corpus.sqlite,chroma/}
data/rag/runtime/serving/<release-id>/<chroma-tree-sha256>/{version.json,chroma/}
data/rag/runtime/active-index.json
```

Commands: `ocr`, `index`, `status`, `review list`, `review show`, `review resolve`, `activate`, and `rollback`. 기존 `run`은 로컬 fixture 호환용입니다. `review resolve`는 state DB에 고정된 lane artifact를 기본으로 사용하므로 live 실행에서는 provider 디렉터리를 다시 지정하지 않습니다. `rollback`은 active pointer만 변경합니다.

Finalized release files, including `corpus.sqlite` and `chroma/`, are chmod read-only. The manifest binds the raw SQLite SHA-256 as well as its logical chunk corpus hash. Chroma 1.5.5 reopens its local store with write intent, so it is never opened from the release. Under a release-specific file lock, the indexer copies it to a unique staging directory, validates chunk identity, dimension, collection metadata, and ordered embedding SHA-256, then atomically installs a writable content-addressed serving version. The immutable source tree keeps its raw file-tree SHA-256; the writable serving copy is checked by logical embedding identity because Chroma may legitimately update local store files when opened. Any source-hash or serving validation failure blocks publication.

`source-manifest.json` is `{"documents":[{"document_id":"issuer/card","source_pdf":"relative-or-absolute.pdf"}]}`. 각 live lane은 `raw_response.*.json`, `pages.json`, `ocr.txt`, `structure_raw_response.*.json`, `structured.json`, `normalized.json`을 별도로 보존합니다. 다페이지 Upstage element는 명시적 page 번호와 모든 페이지의 text를 요구합니다. 로컬 fixture 모드는 provider 디렉터리의 `<issuer__card>.json`을 가져오고 동일한 page/text/normalized 검토 view를 working 디렉터리에 만듭니다. 각 normalized lane은 `document_id`, OCR `pages` (`page`, `text`), identity, `span_dispositions`, structured `facts`와 own-lane `evidence` (`page`, `quote`)를 갖습니다. 할인·적립·수치 조건처럼 혜택 가능성이 있는 줄을 `ignore`로 넘기면 해당 문서를 `restructure_required`로 차단합니다. 이는 provider 선택으로 해결할 수 있는 차이가 아니므로 review resolve가 아니라 구조화 설정을 바꾼 새 run이 필요합니다.

Provider JSON also requires `provider`, `source_pdf_sha256`, `provenance={endpoint,model,config_hash}`, and `identity={issuer_name,card_name,issuer_evidence,card_evidence}`; every identity evidence is an own-lane page/quote reference. A review resolution requires both `selected_provider` and `selected_identity_provider`, a canonical `identity` with exact per-lane identity evidence and `supports_selected`, and the complete selected-provider `canonical` relation set. `rejected_relations` contains exactly the non-selected lane's tuple-set difference as `{provider,tuple,reason}` entries. Missing, extra, duplicate, mixed-lane, or unsupported facts and identities fail closed.
