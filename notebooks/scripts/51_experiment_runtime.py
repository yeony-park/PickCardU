"""Resumable paid journals, global budget and timings shared by notebooks 51–54.

Notebook-only execution cannot safely share the exact request identity, budget
reservation and raw-before-parse protocol across four long experiments. This
helper owns that protocol; notebooks own questions, stages and decisions. Old
helpers and operating code are imported read-only and never modified on disk.
"""
from pathlib import Path
from decimal import Decimal
import importlib.util
import sys
import json
import time
import math
import statistics

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


R = load("experiment51_legacy_runtime", "47_retrieval_runtime.py")
Q = load("experiment51_query", "50_query_contract.py")
P = R.P
STAGES = {50: "50_query_contract_audit", 51: "51_query_extraction_scope",
          52: "52_four_retrieval_modes", 53: "53_sequential_fact_supplement",
          54: "54_new_question_confirmation"}
PROFILES = ["card_page_section_benefit", "parent_child_bundle"]
for stage, directory in STAGES.items():
    P.STAGES[stage] = directory
    for common in [P.F, P.A.F, Q.P.F, Q.P.A.F]:
        common.STAGES[stage] = P.DATA / directory


def ledger():
    records = [(str(stage), str(path.relative_to(ROOT)), P.F.read_json(path))
               for stage in sorted(P.STAGES)
               for path in sorted((P.F.STAGES[stage] / "api_cache").glob("*.json"))]
    return P.PRICE.aggregate(P.PRICE.price_records(records))


P.ledger = ledger


def frozen(path, value):
    path = Path(path)
    if path.exists():
        if P.F.read_json(path) != value:
            raise RuntimeError("Frozen input changed: " + str(path))
    else:
        P.F.write_json(path, value)


def notebook_code_hash(path):
    import nbformat
    return P.sha([c.source for c in nbformat.read(path, 4).cells if c.cell_type == "code"])


def guard_manifest(manifest):
    for name, expected in manifest["notebook_code_sha256"].items():
        if notebook_code_hash(ROOT / name) != expected:
            raise RuntimeError("Notebook code changed after freeze: " + name)
    for name, expected in manifest["source_sha256"].items():
        if P.sha((ROOT / name).read_bytes()) != expected:
            raise RuntimeError("Frozen executable source changed: " + name)


class Cache(R.BoundCache):
    def reserve(self, normalized):
        guard_manifest(self.manifest)
        if normalized["model"] != "gpt-6-luna" or normalized["reasoning"] != {"effort": "medium"}:
            raise RuntimeError("Model/reasoning differs from fixed contract")
        records = [P.F.read_json(p) for p in self.cache.glob("*.json") if not p.name.startswith("embedding_batch_")]
        # The parser cap and per-identity answer retry cap are physical calls,
        # independent of the total cap enforced by the old budget guard.
        own_kind = sum(r.get("request", {}).get("max_output_tokens") == (3000 if self.kind == "parser" else 2400)
                       for r in records)
        if own_kind >= self.manifest["maximum_" + self.kind + "_requests"]:
            raise RuntimeError("Request kind physical cap reached")
        identity_count = sum(1 for r in records if r.get("experiment_identity") == self.identity)
        if identity_count >= (1 if self.kind == "parser" else 2):
            raise RuntimeError("Identity attempt cap reached")
        return super().reserve(normalized)

    def parse(self, **kwargs):
        start, cpu = time.perf_counter(), time.process_time()
        count = len(self.events)
        key = self.key(kwargs)
        try:
            return super().parse(**kwargs)
        finally:
            elapsed = time.perf_counter() - start
            for event in self.events[count:]:
                event.update(observed_call_wall_seconds=elapsed,
                             observed_call_cpu_seconds=time.process_time() - cpu)
            path = self.cache / (key + ".json")
            if path.exists():
                record = P.F.read_json(path)
                if record.get("experiment_identity") not in [None, self.identity]:
                    raise RuntimeError("Request identity collision")
                if not record.get("experiment_identity"):
                    record.update(experiment_identity=self.identity, experiment_kind=self.kind)
                    P.F.write_json(path, record)


class Runtime(R.Runtime):
    def embeddings(self, texts, *, allow_network=False):
        guard_manifest(self.manifest)
        start = time.perf_counter()
        vectors = super().embeddings(texts, allow_network=allow_network)
        self.last_embedding_seconds = time.perf_counter() - start
        return vectors

    def search(self, *args, **kwargs):
        guard_manifest(self.manifest)
        start, cpu = time.perf_counter(), time.process_time()
        offset = len(self.scorer_calls)
        cold_model = not self.model_loaded
        result = super().search(*args, **kwargs)
        calls = self.scorer_calls[offset:]
        result["experiment_timing"] = dict(search_wall_seconds=time.perf_counter()-start,
            search_cpu_seconds=time.process_time()-cpu,
            rerank_wall_seconds=sum(c["wall_seconds"] for c in calls),
            rerank_new_pairs=sum(c["new_pairs"] for c in calls),
            rerank_cached_pairs=sum(c["cache_hits"] for c in calls),
            cold_model_at_start=cold_model)
        return result


def make_manifest(stage, notebook, queries, identities, *, maximum_parser=0, maximum_answer=0,
                  maximum_embedding=0, stage_cap="1", extra=None):
    previous = P.F.read_json(P.F.STAGES[49] / "execution_manifest.json")
    sources = set(previous["source_sha256"]) | {
        "notebooks/scripts/50_query_contract.py", "notebooks/scripts/51_experiment_runtime.py"}
    question_inputs = {f"{q['query_id']}:parser:r{r}": P.sha([dict(role="user", content=q["question"])])
                       for q in queries for r in [1, 2, 3]}
    manifest = dict(stage=stage, model="gpt-6-luna", reasoning="medium", max_output_tokens=3000,
        parser_output=3000, answer_output=2400, global_cost_cap_usd="8", stage_cost_cap_usd=stage_cap,
        allowed_identities=identities, allowed_request_by_identity=question_inputs,
        parser_instructions_sha256=P.sha(Q.PROMPT), parser_schema_sha256=P.sha(Q.QueryPlan.model_json_schema()),
        allowed_embedding_text_sha256=[P.sha(q["question"]) for q in queries],
        source_sha256={name: P.sha((ROOT/name).read_bytes()) for name in sorted(sources)},
        data_sha256={name: value for name,value in previous["data_sha256"].items()
                     if name.startswith("notebooks/data/30_") or name.startswith("notebooks/data/31_")},
        notebook_code_sha256={str(notebook.relative_to(ROOT)): notebook_code_hash(notebook)},
        maximum_parser_requests=maximum_parser, maximum_answer_requests=maximum_answer,
        maximum_embedding_requests=maximum_embedding,
        maximum_physical_requests=maximum_parser+maximum_answer+maximum_embedding,
        maximum_network_retries=0, maximum_validation_retries_per_answer=1,
        questions_sha256=P.sha(queries), parameters=dict(component_depth=50,candidate_depth=20,top_cards=3,
        evidence_per_card=5,parent_bundle_max_chunks=5,answer_payload_bytes=64000,vector_weight=.4,cpu_threads=4),
        output_timing="physical API wall; logical answer-ready with retries; cache separate; no streaming TTFT/UI display measurement",
        operating_changes=False)
    if extra:
        manifest.update(extra)
    for name, wanted in manifest["data_sha256"].items():
        if P.sha((ROOT/name).read_bytes()) != wanted:
            raise RuntimeError("Original corpus/index data changed: " + name)
    frozen(P.F.STAGES[stage] / "execution_manifest.json", manifest)
    return manifest


def extract(stage, q, repeat, *, allow_network=False):
    path = P.F.STAGES[stage]
    identity = f"{q['query_id']}:parser:r{repeat}"
    cache = Cache(stage, "parser", identity, allow_network=allow_network)
    manifest = cache.manifest
    kwargs = dict(model="gpt-6-luna", instructions=Q.PROMPT,
        input=[dict(role="user", content=q["question"])], text_format=Q.QueryPlan,
        tools=[], store=False, max_output_tokens=3000)
    target = path / "plans" / (identity + ".json")
    if target.exists():
        row = P.F.read_json(target)
    else:
        start, cpu = time.perf_counter(), time.process_time()
        value, error = None, None
        try:
            response = cache.parse(**kwargs)
            value = response.output_parsed.model_dump(mode="json") if response.output_parsed else None
        except Exception as exc:
            if not cache.events:
                raise
            error = f"{type(exc).__name__}: {exc}"
        row = dict(identity=identity, query_id=q["query_id"], repeat=repeat, question=q["question"],
            question_sha256=P.sha(q["question"]), manifest_sha256=P.sha(manifest), request_key=cache.key(kwargs),
            status="ok" if value is not None else "failed", error=error, plan=value, events=cache.events,
            parser_wall_seconds=time.perf_counter()-start, parser_cpu_seconds=time.process_time()-cpu)
        P.F.write_json(target, row)
    P.validate_parser_row(row, identity=identity, query_id=q["query_id"], repeat=repeat,
        question=q["question"], manifest=manifest, request_key=cache.key(kwargs), cache=cache.cache)
    original = Q.original_scope(q["question"])
    route_path = path / "routes" / (identity + ".json")
    binding = P.sha(dict(parser=row, original_scope=original))
    if route_path.exists():
        saved = P.F.read_json(route_path)
        if saved["input_binding"] != binding:
            raise RuntimeError("Saved SQL route differs from parser/original scope")
        return saved["record"]
    routing = Q.route(row["plan"], original, parser_failed=row["status"] != "ok")
    record = dict(parser=row, original_scope=original, routing=routing)
    P.F.write_json(route_path, dict(input_binding=binding, record=record))
    return record


def timing_summary(values):
    values = [float(v) for v in values if v is not None]
    if not values:
        return dict(n=0, mean=None, median=None, p95=None, maximum=None)
    ordered = sorted(values)
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values),
                p95=ordered[math.ceil(.95*len(values))-1], maximum=ordered[-1])
