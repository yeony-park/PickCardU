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
- Cache API calls by exact payload/model/reasoning fingerprint. Save successful raw responses and every failure/request; failed SDK parse response status/usage were unavailable and are reported unmeasured. No silent model fallback. No paid OCR extraction.
- Do not install dependencies or change .env. Commit verified checkpoints as explicitly authorized; never push automatically.

## Tasks and verification

### 30: Inputs, structured database and experiment contracts

Files: create notebooks/scripts/30_structured_search_common.py, notebooks/30_structured_benefit_db.ipynb and notebooks/data/30_structured_benefit_db/. Create docs/report/30_structured_benefit_db_report.html.

- [x] Snapshot original hashes and runtime/environment versions.
- [x] Load the 106 Upstage normalized JSON files read-only; keep document, fact and evidence identities.
- [x] Materialize cards, facts, condition/evidence records and a versioned category dictionary in a new SQLite file.
- [x] Freeze QueryPlan: requirement groups (OR between groups, AND within), same-benefit target/action/value constraints, spend/cap constraints, exclusions, card scope, route and unresolved intent.
- [x] Verify percent scaling, KRW values, unknown conditions, AND/OR, exceptions, and independent benefit linkage with focused tests under notebooks/.
- [x] Freeze source-relative gold plus natural-query fixtures and dataset split before retrieval; document gold creation and independence limits.
- [x] Generate report30 with counts, availability and unsupported cases.

### 31: Operational chunk generation

Files: create notebooks/scripts/31_operational_chunking.py, notebooks/31_operational_chunking_comparison.ipynb and notebooks/data/31_operational_chunking_comparison/. Create docs/report/31_operational_chunking_comparison_report.html.

- [x] Import unchanged chunk functions with bytecode disabled and transform inputs only in the notebook adapter.
- [x] Generate both profiles from the same pages/facts; save records, hierarchy audits and fact-to-chunk witnesses.
- [x] Verify unique IDs, source coverage, parent/child references, foreign-card relationships and unmapped facts.
- [x] Record failures rather than modifying/chopping source text to make the production algorithm pass.
- [x] Build new FTS5/Chroma/embedding artifacts under the stage directory; reuse exact embedding text hashes where possible.
- [x] Report hierarchy depth, fact coverage, chunk counts and measured resources.

### 32: Six retrieval conditions and answer evaluation

Files: create notebooks/scripts/32_search_experiment.py, notebooks/32_structured_search_comparison.ipynb and notebooks/data/32_structured_search_comparison/. Create docs/report/32_structured_search_comparison_report.html.

- [x] Run local functional tests before external calls.
- [x] Validate one authorized GPT-6 Luna medium response; log its metadata without secrets.
- [x] Run six conditions against frozen human-authored plans; retain strict structured selection even when fallback is used.
- [x] U uses the unchanged operational search pipeline. S_THEN_U scopes both search components to structurally eligible cards. S_PLUS_U combines independent card candidates using a predeclared card rank rule and unchanged operational evidence hydration.
- [x] Evaluate known-positive card hit/MRR, condition satisfaction, evidence witness coverage and source-relative negative controls. Report paired query wins/losses/ties and subgroups.
- [x] Generate answers with unchanged operational answer logic and an experiment-only SDK proxy enforcing gpt-6-luna/medium. Validate citation ownership and query constraints; save raw responses.
- [x] Cache repeated candidates/scorer pairs, include failure rows in denominators, and distinguish actual from estimated resources.
- [x] Diagnose material losses with prespecified additional checks; never rewrite earlier gold to improve a result.

### 33: LLM query planning, routing and confirmation

Files: create notebooks/scripts/33_query_routing.py, notebooks/33_query_routing.ipynb and notebooks/data/33_query_routing/. Create docs/report/33_query_routing_report.html.

- [x] Use direct OpenAI responses.parse with the frozen Pydantic QueryPlan and medium reasoning.
- [x] Compare fields, units, operators, Boolean grouping and routing to frozen human plans. Reuse each plan across profiles.
- [x] Execute actual LLM plans and quantify performance loss relative to oracle plans.
- [x] Apply the frozen configuration to the new confirmatory cohort without tuning. Repeat parser probes on a small prespecified boundary subset to measure variability.
- [x] Produce four matching HTML reports, each with nearby Korean metric definitions/examples/limitations, full condition matrices, resource measurements, decisions and remaining limits.
- [x] Independent registered reviewer examines adapters/scoring and source preservation. Correct substantive errors and rerun affected checks.
- [x] Execute all four notebooks in skn25, inspect output errors, verify report links and original hashes. Audit all explicit requirements before goal completion.

## Additional-test rule

Add tests when a concrete observed failure, ambiguity or material regression justifies them. Record the trigger, freeze the follow-up protocol before results, and label it development/post-hoc. Query-expansion retrieval remains deferred.

## Verified checkpoint dd87843 and follow-up work

- DB v3: 106 documents / 617 pages / 3403 facts; guaranteed scalar percent1033, spend187, benefit cap381, explicit cap period66. Range/bound37 and spend-scope cap90 remain unknown. Original fields/IDs/provenance unchanged.
- Benefit3183 and parent1834 chunks/indexes built with operational inputs/functions. New unique vectors4363 plus33 query vectors; no new OCR extraction.
- Current checks:49 passed; source and used model preservation8934+27 verified. Pytest-generated root cache relocated to notebooks; all future tests set cache_dir explicitly.
- Oracle 198-row search completed:194 successful,4 operational bundle-limit/fusion failures. Current identity-bound cache replay verified all198 card/evidence/metrics and all33 selections identical. Oracle/LLM primary answers, scores and follow-ups completed.
- Completed:oracle answers/scores;33 LLM plans;actual LLM searches/answers;10 additional repeat requests for5 predeclared questions. All5 repeated plans stable in3 trials (not population reliability).
- Justified backend ablation completed:6 questions x2 profiles with all106 cards explicitly in scope; all12 card rankings identical to global paths.0 new API/embedding,1 new CPU BGE score pair. This limited development diagnostic does not establish backend equivalence for other questions.
- Final compact summaries, fresh preservation/HTML links/notebook outputs and independent review completed. Final result commit follows the final display audit.

## Final measured results and limitations

- Both oracle and actual LLM S_THEN_U:27/27 known-positive card Hit@3 and MRR1. U benefit26/27, parent24/27. Four search failures per source are parent U/fusion Q05/Q28 bundle limits; no production changes.
- Actual LLM witness mean (24 questions):benefit S_THEN_U41.13%, parent39.07%; fusion35.89%/30.97%. Parent superiority did not reproduce; this is the whole profile, not a pure chunk effect.
- Actual parser route29/33, exact conditions20/33, candidate sets23/33, scope33/33. Equivalent interpretations may differ; Hit/MRR preserved but known-card recall changed.
- Primary answers:oracle189/198, LLM188/198 completed. Cached EOF errors became RuntimeError on reuse, skipping operational retry; raw comparisons retain that limitation. Offline retry repaired5 row failures with0 calls. The remaining6 rows reused one new4800-token response.17 follow-up records include6 failed offline attempts. Operational search failures remain.
- Answer proxies retain their frozen rules:negative Q18 explicitly denies1000% but answered status scores0; detailed card descriptions can have no recommendations; Q10 arithmetic1+0.5=1.5 is not literal support. None of these proxy failures alone proves a wrong financial answer.
- All16 notebook code cells executed in skn25;54 focused tests passed. Fresh final audit validates current code/input/result/scorer/report/plan binding, links and preserved8934+27 original files; new files outside notebooks/report0.
- Follow-up priority:structured-first retrieval; compare both profiles again after heading repair. Fusion remains an answer-quality comparison candidate. Query expansion remains deferred. No production adoption, no PDF truth validation, billed cost unmeasured.
