# Structured search experiment implementation plan

> **For agentic workers:** Execute according to the active AGENTS.md. The main Codex implements the experiment; registered reviewers perform read-only reviews. No original source or existing artifact may be modified.

**Goal:** Reproduce structured-first retrieval and compare both operational chunking profiles, including question parsing and answers, on the new PC.

**Architecture:** Reuse frozen Upstage normalized JSON and the unchanged operational chunk builders, FTS5/vector adapters, RagPipeline and answer generator. New adapters, SQLite databases, indexes, cached API responses, evaluation fixtures and notebooks live only under notebooks/. Reports live only under docs/report/.

**Tech stack:** conda skn25; Python, SQLite FTS5, NumPy, Chroma, operational local BGE, OpenAI SDK 3.28.0, Pydantic, nbformat. Every experiment LLM request uses gpt-6-luna and reasoning.effort=medium.

**Spec:** The approved conversation plan: stages 30 database, 31 operational chunking, 32 six-condition retrieval/answer experiment, 33 query routing. This file records the executable interpretation.

## Global constraints

- Read original code, runtime, OCR artifacts, older notebooks and reports only. Record SHA-256 and metadata before execution and verify preservation afterward.
- Run Python exclusively through conda run --no-capture-output -n skn25 python -B. Disable bytecode, telemetry and model downloads; direct new caches into the experiment directory.
- OCR accuracy is assumed, not verified. Use one frozen Upstage lane per document from run_f2d29820c6dca642. Never mark the lane approved or publish a release.
- Use unchanged build_benefit_chunks, build_structural_chunks and RagPipeline. Do not execute the indexer publication/approval pipeline.
- Preserve the current imperfect heading hierarchy. Any excluded or failed documents must remain visible in coverage and experiment eligibility.
- Structured facts are independent of chunks. Preserve raw values, typed numbers, condition scopes and evidence. Unknown is distinct from false.
- Evaluate U, S_THEN_U, S_PLUS_U for each operational profile with equal component depth50, candidate depth20 and output Top3. Preserve selective BGE for benefit and all-query bundle BGE for parent_child.
- Freeze evaluation queries, plans, category aliases, fallback and fusion rules before retrieval results. Do not use sealed historical holdouts for tuning.
- Separate controlled source-relative diagnostics from natural-question development and new confirmatory results. Do not claim source correctness, population generalization or production adoption.
- Cache API calls by exact payload/model/reasoning fingerprint. Save every failure and response; no silent model fallback. No paid OCR extraction.
- Do not install dependencies or change .env. Commit verified checkpoints as explicitly authorized; never push automatically.

## Tasks and verification

### 30: Inputs, structured database and experiment contracts

Files: create notebooks/30_structured_search_common.py, notebooks/30_structured_benefit_db.ipynb and notebooks/data/30_structured_benefit_db/. Create docs/report/30_structured_benefit_db_report.html.

- [ ] Snapshot original hashes and runtime/environment versions.
- [ ] Load the 106 Upstage normalized JSON files read-only; keep document, fact and evidence identities.
- [ ] Materialize cards, facts, condition/evidence records and a versioned category dictionary in a new SQLite file.
- [ ] Freeze QueryPlan: requirement groups (OR between groups, AND within), same-benefit target/action/value constraints, spend/cap constraints, exclusions, card scope, route and unresolved intent.
- [ ] Verify percent scaling, KRW values, unknown conditions, AND/OR, exceptions, and independent benefit linkage with focused tests under notebooks/.
- [ ] Freeze source-relative gold plus natural-query fixtures and dataset split before retrieval; document gold creation and independence limits.
- [ ] Generate report30 with counts, availability and unsupported cases.

### 31: Operational chunk generation

Files: create notebooks/31_operational_chunking.py, notebooks/31_operational_chunking_comparison.ipynb and notebooks/data/31_operational_chunking_comparison/. Create docs/report/31_operational_chunking_comparison_report.html.

- [ ] Import unchanged chunk functions with bytecode disabled and transform inputs only in the notebook adapter.
- [ ] Generate both profiles from the same pages/facts; save records, hierarchy audits and fact-to-chunk witnesses.
- [ ] Verify unique IDs, source coverage, parent/child references, foreign-card relationships and unmapped facts.
- [ ] Record failures rather than modifying/chopping source text to make the production algorithm pass.
- [ ] Build new FTS5/Chroma/embedding artifacts under the stage directory; reuse exact embedding text hashes where possible.
- [ ] Report hierarchy depth, fact coverage, chunk counts and measured resources.

### 32: Six retrieval conditions and answer evaluation

Files: create notebooks/32_search_experiment.py, notebooks/32_structured_search_comparison.ipynb and notebooks/data/32_structured_search_comparison/. Create docs/report/32_structured_search_comparison_report.html.

- [ ] Run local functional tests before external calls.
- [ ] Validate one authorized GPT-6 Luna medium response; log its metadata without secrets.
- [ ] Run six conditions against frozen human-authored plans; retain strict structured selection even when fallback is used.
- [ ] U uses the unchanged operational search pipeline. S_THEN_U scopes both search components to structurally eligible cards. S_PLUS_U combines independent card candidates using a predeclared card rank rule and unchanged operational evidence hydration.
- [ ] Evaluate known-positive card hit/MRR, condition satisfaction, evidence witness coverage and source-relative negative controls. Report paired query wins/losses/ties and subgroups.
- [ ] Generate answers with unchanged operational answer logic and an experiment-only SDK proxy enforcing gpt-6-luna/medium. Validate citation ownership and query constraints; save raw responses.
- [ ] Cache repeated candidates/scorer pairs, include failure rows in denominators, and distinguish actual from estimated resources.
- [ ] Diagnose material losses with prespecified additional checks; never rewrite earlier gold to improve a result.

### 33: LLM query planning, routing and confirmation

Files: create notebooks/33_query_routing.py, notebooks/33_query_routing.ipynb and notebooks/data/33_query_routing/. Create docs/report/33_query_routing_report.html.

- [ ] Use direct OpenAI responses.parse with the frozen Pydantic QueryPlan and medium reasoning.
- [ ] Compare fields, units, operators, Boolean grouping and routing to frozen human plans. Reuse each plan across profiles.
- [ ] Execute actual LLM plans and quantify performance loss relative to oracle plans.
- [ ] Apply the frozen configuration to the new confirmatory cohort without tuning. Repeat parser probes on a small prespecified boundary subset to measure variability.
- [ ] Produce four matching HTML reports, each with nearby Korean metric definitions/examples/limitations, full condition matrices, resource measurements, decisions and remaining limits.
- [ ] Independent registered reviewer examines adapters/scoring and source preservation. Correct substantive errors and rerun affected checks.
- [ ] Execute all four notebooks in skn25, inspect output errors, verify report links and original hashes. Audit all explicit requirements before goal completion.

## Additional-test rule

Add tests when a concrete observed failure, ambiguity or material regression justifies them. Record the trigger, freeze the follow-up protocol before results, and label it development/post-hoc. Query-expansion retrieval remains deferred.
