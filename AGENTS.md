# Repository Guidelines

## Project Structure & Module Organization

- `apps/main/` is the user-facing Next.js application; `apps/rag-lab/` is reserved for the internal RAG dashboard.
- `services/rag-api/` is the shared FastAPI service for search and answers.
- `packages/rag-core/` contains storage-independent retrieval, reranking, prompting, and evaluation logic; `packages/contracts/` contains the generated OpenAPI snapshot and client-facing TypeScript types.
- `jobs/rag-indexer/` owns the offline OCR, validation, chunking, embedding, and index-release pipeline.
- `data/raw/<issuer>/` contains source card-benefit PDFs. Do not rename, delete, or modify these files without explicit approval.
- `data/rag/runtime/` contains local OCR checkpoints and active index releases and must not be bundled into either app.
- `scripts/ocr_benchmark/` contains reusable Python runners for PyMuPDF, Mistral OCR, Upstage Document Parse, and benchmark evaluation.
- `tests/` contains pytest coverage for runner normalization, schema, and metric helpers.
- `notebooks/` contains numbered, reproducible experiments. Supporting evaluation data, cached OCR responses, and validation outputs live under `notebooks/data/`.
- `data/ocr_benchmark/` holds benchmark artifacts and reports; `docs/` contains the team-facing API specification, API runbook, RAG pipeline notes, evaluation guidance, and published reports.

Keep new issuer data in the existing issuer-folder pattern. Put new benchmark scripts in `scripts/ocr_benchmark/` and corresponding tests in `tests/test_<script_name>.py`.

## Experiment & Notebook Workflow

- Use notebooks under `notebooks/` for interactive exploration and evaluation work that benefits from visible, staged execution and inline results.
- Use a `.py` runner or helper when the work requires unattended or long-running execution, resumable or cache-aware behavior, stable exit codes, reuse through cron, CI, or operations, or shared logic that should be covered by automated tests.
- When adding a runner or helper, document in the related notebook, nearby README, or pull request why the notebook alone is insufficient and what reusable responsibility the script owns.
- Keep experiment inputs, cached provider responses, normalized outputs, and evaluation artifacts separate so results can be reproduced and inspected.
- Do not delete or move existing notebooks or scripts solely to conform to these guidelines. Any migration should have a scoped rationale and verify affected path consumers.

## Build, Test, and Development Commands

Use Python 3.11 or newer in an isolated environment with the dependencies required by the component being changed. Do not assume a contributor-specific environment name, virtual-environment path, or machine-local wrapper in shared commands or documentation. The examples below use plain `python`; adapt the environment launcher without changing the command semantics, and report the actual command used for validation.

```bash
python -m pytest -q
python scripts/ocr_benchmark/run_pymupdf.py
python scripts/ocr_benchmark/evaluate_ocr_benchmark.py
```

The first command runs the unit tests. The PyMuPDF runner generates local extraction artifacts; the evaluator writes the benchmark report. API-backed runners require the relevant key in the root `.env` and may transmit PDFs externally, so confirm the enabled issuer and run flags before executing them.

## API Contract & Operations Documentation

- `services/rag-api/src/pickcardu_rag_api/main.py` and its Pydantic models are the implementation source for the HTTP contract.
- `packages/contracts/openapi.yaml` and `packages/contracts/generated/api.ts` are generated from the FastAPI OpenAPI schema by `packages/contracts/generate.py`. Do not edit either generated file manually.
- `docs/API_SPEC.md` is the team-facing HTTP specification. Read it first for base URLs, authentication status, methods, paths, request and response fields, status codes, errors, examples, and unresolved contract decisions.
- `docs/API_RUNBOOK.md` is the operator guide for release activation, environment variables, service startup, health checks, smoke-test boundaries, rollback, and troubleshooting.
- `docs/rag_pipeline.md` explains the internal OCR, indexing, retrieval, reranking, and answer-generation flow; it does not replace the HTTP contract.
- API consumers should use `docs/API_SPEC.md` for behavior, `packages/contracts/openapi.yaml` for the machine-readable schema, and `packages/contracts/generated/api.ts` when a TypeScript integration needs generated types.
- When the HTTP contract changes, update the FastAPI route or Pydantic model first, regenerate the contract artifacts, run the drift check, and update `docs/API_SPEC.md`. Update `docs/API_RUNBOOK.md` only when operational procedures or runtime settings change.
- Generating or reviewing the shared TypeScript contract does not authorize changes to `apps/main/` or `apps/rag-lab/`; modify an application only when the task explicitly includes client integration.
- Keep contributor-specific environment names, local absolute paths, active release IDs, and one-machine runtime counts out of the shared API documents. Use placeholders or clearly labeled examples instead.

```bash
PYTHONPATH=services/rag-api/src:packages/rag-core/src \
  python packages/contracts/generate.py
PYTHONPATH=services/rag-api/src:packages/rag-core/src \
  python packages/contracts/generate.py --check
```

## Coding Style & Naming Conventions

Write Python with four-space indentation, type hints where practical, `snake_case` functions and variables, and `UPPER_CASE` module constants. Keep path construction rooted at the repository rather than hard-coding machine-specific paths. Preserve provider responses separately from normalized search text. Use English, descriptive filenames such as `run_upstage_document_parse.py` and `test_evaluate_ocr_benchmark.py`.

## Testing Guidelines

Add focused pytest tests for changed parsing, normalization, or scoring behavior. Name tests `test_<behavior>()`. Mock network calls and use existing saved artifacts for integration-style checks; do not make paid API calls during ordinary test runs. Verify generated JSON retains the expected page schema, table references, and coordinates when applicable.

## Commit & Pull Request Guidelines

Follow the existing conventional style: `feat:`, `test:`, `fix:`, `refactor:`, or `chore:` followed by a concise imperative summary (for example, `test: add OCR benchmark validation`). Keep each change focused. Pull requests should describe the affected pipeline, list validation commands run, identify any regenerated artifacts, and call out API cost or external-data handling. Do not commit `.env` files or secrets.

## Branch Workflow

Create implementation branches as `feat/*` from `develop`, merge reviewed features into `develop` for integration and staging, and move `develop` to `main` only through an explicit reviewed merge. Keep exploratory notebooks and generated evaluation artifacts on dedicated experiment branches, and do not merge an experiment branch wholesale into `develop`. Do not push or modify `main` unless the user explicitly requests it.

## Evaluation Reporting

- Separate each experiment clearly, and present it in the order: why it was run, what question it was intended to answer, what changed, what stayed fixed, cautions, results, and conclusion. Continue follow-up tests in the same report when they belong to the same methodological branch; create a new report for a major architecture or data-pipeline branch.
- Clearly distinguish the previous baseline, current development prototype, new candidate, raw best result, selected configuration, and production-adopted configuration. Do not use `best`, `selected`, `promoted`, `operational`, or `production` as interchangeable terms. State the source experiment or artifact for values carried over from an earlier test.
- For parameter searches and ablations, do not report only the winning configuration. Include a readable condition-by-condition table or matrix, keep non-target variables fixed for fair comparisons, highlight the best and runner-up values, and link the complete machine-readable results when the full grid is too large for the report body.
- Report aggregate results together with relevant subgroups such as card/proper-noun, numeric/condition, and semantic/benefit queries. Show query-level wins, losses, and ties when averages can hide regressions. A gain in one subgroup must not conceal a material decline in another.
- Every HTML result table must have a nearby metric guide written in easy Korean. For every metric, explain the English term, what data and denominator it uses, whether higher or lower is better, what counts as a pass, a concrete card-benefit example, and a key limitation or failure case. Define newly introduced metrics in `docs/EVALUATION_METRICS.md` with the same example and limitation.
- Identify development, confirmatory, and holdout results explicitly. Warn when many configurations were compared on the same development set, when a rule was chosen after seeing results, or when the result is not eligible for promotion or generalization.
- When corpus, chunking, relevance units, or candidate depth changes, verify whether metric denominators remain comparable. If they do not, treat affected metrics such as Recall or nDCG as diagnostic only, and prioritize metrics with comparable query-level meaning such as Hit and MRR unless a fixed relevance unit is available.
- Report measurable quality-versus-resource trade-offs: API requests and cost, transmitted and cached tokens, new embeddings, wall/CPU time, throughput, GPU and peak memory, model/cache/index size, chunk and candidate counts, and storage changes. Mark unavailable values as `미측정` instead of estimating them. A cost or latency reduction alone must not override a failed quality guardrail.
- For repeated runs, state whether values are a single run or a mean, define variance or standard deviation and its denominator, and keep measured, estimated, and historical resource values separate. If only appended notebook cells were executed, state that the full notebook was not rerun and describe any resulting reproducibility limitation.
- End every report with a concise final summary covering what improved, what regressed, the adopted and rejected settings, cost/time impact, unresolved limitations, and the next validation step. Write English labels and decision codes with an adjacent plain-Korean explanation so the report can be understood without reading raw artifacts.
