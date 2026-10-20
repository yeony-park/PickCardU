"""Shared read-only operating retrieval adapter for notebooks 47–49.

Only new stage directories receive index copies, caches and provider journals.
The notebooks freeze inputs and budgets and own execution/evaluation decisions.
"""
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import os
import resource
import shutil
import time
from decimal import Decimal

import importlib.util
import sys

_spec = importlib.util.spec_from_file_location("retrieval_preproduction", Path(__file__).with_name("46_preproduction_common.py"))
P = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = P
_spec.loader.exec_module(P)


def selected_route(question, value, facts, catalog, **kwargs):
    result = P.route(question, value, facts, catalog, **kwargs)
    flags = result["semantic_flags"]
    if result["reason"] == "semantic_conflict" and flags and all(f.startswith("missing_literal_target:") for f in flags):
        return {**P.route(question, value, facts, catalog, check_semantics=False, **kwargs),
                "diagnostic_target_flags": flags}
    return result


def partial_candidates(value, facts, routing):
    """True requirement matches are priorities only, never full-AND approval."""
    if routing["reason"] != "no_eligible_cards":
        return []
    plan = P.E.QueryPlan.model_validate(value).model_dump(mode="json")
    found = set()
    for group in plan["groups"]:
        for requirement in group:
            single = {**plan, "groups": [[requirement]], "card_keys": routing["scope"] or []}
            found.update(P.E.DB.select_cards(facts, single)["eligible_cards"])
    return sorted(found)


def merge_components(original, extra, limit):
    from pickcardu_rag.retrieval import weighted_rrf
    return weighted_rrf({"original": original, "supplement": extra},
                        {"original": .5, "supplement": .5})[:limit]


class BoundCache(P.BudgetCache):
    def reserve(self, normalized):
        request_input = P.sha(normalized["input"])
        if self.kind == "parser":
            expected = self.manifest["allowed_request_by_identity"][self.identity]
            if (P.sha(normalized["instructions"]) != self.manifest["parser_instructions_sha256"]
                    or P.sha(normalized["text_format"].model_json_schema()) != self.manifest["parser_schema_sha256"]):
                raise RuntimeError("Parser prompt/schema differs from frozen contract")
        else:
            contract = P.F.read_json(P.F.STAGES[self.stage] / "answer_payload_contract.json")
            if contract["execution_manifest_sha256"] != P.sha(self.manifest):
                raise RuntimeError("Answer payload contract has a different execution manifest")
            expected = contract["input_sha256_by_identity"][self.identity]
            if P.sha(normalized["text_format"].model_json_schema())!=contract["schema_sha256_by_identity"][self.identity]:
                raise RuntimeError("Answer schema differs from frozen retrieval payload")
        if request_input != expected:
            raise RuntimeError("Identity and frozen provider payload differ")
        super().reserve(normalized)


class FixedSearcher:
    def __init__(self, rows, depth):
        self.rows, self.depth = rows, depth

    def search(self, value, *, limit, card_keys=None):
        if limit != self.depth or card_keys is not None:
            raise ValueError("Frozen component depth or scope mismatch")
        return list(self.rows)


def validate_generated_answer(row, question, evidence, *, stage, identity, manifest, contract,
                              historical=None, reused=None):
    """Bind answer checkpoints to provider records or a verified reuse source."""
    from pickcardu_rag.answering import _generated_answer_model, _restore_generated_answer, serialize_answer_payload
    from pickcardu_rag.errors import LlmUngrounded
    expected = dict(identity=identity, manifest_sha256=P.sha(manifest),
                    payload_contract_sha256=P.sha(contract), input_sha256=P.sha([
                        dict(role="user",content=serialize_answer_payload(question,evidence))]))
    if any(row.get(k)!=v for k,v in expected.items()):
        raise RuntimeError("Saved answer identity/manifest/payload contract mismatch")
    if row["input_sha256"]!=contract["input_sha256_by_identity"][identity]:
        raise RuntimeError("Saved answer input differs from frozen retrieval")
    if historical is not None or reused is not None:
        source=historical if historical is not None else reused
        if row.get("reuse_source_sha256")!=P.sha(source) or row["answer"]!=source["answer"] or row["status"]!=source["status"]:
            raise RuntimeError("Saved reused answer differs from verified source")
        return
    if not evidence:
        if row["answer"] is None or row["answer"]["answer_status"]!="insufficient_evidence" or row["answer"]["claims"] or row["answer"]["recommendations"]:
            raise RuntimeError("Empty evidence checkpoint contains unsupported content")
        return
    schema=_generated_answer_model(len(evidence))
    api=BoundCache(stage,"answer",identity,allow_network=False)
    events=row.get("events") or []
    if not events or row.get("request_keys")!=[e["key"] for e in events]:
        raise RuntimeError("Saved answer lacks bound provider events")
    final=None
    for event in events:
        record=P.F.read_json(api.cache/(event["key"]+".json")); request=record["request"]
        kwargs={k:v for k,v in request.items() if k!="schema"};kwargs["text_format"]=schema
        if (api.key(kwargs)!=event["key"] or P.sha(request["input"])!=row["input_sha256"]
                or request["model"]!=manifest["model"] or request["reasoning"]!={"effort":manifest["reasoning"]}
                or request["max_output_tokens"]!=2400 or P.sha(request["schema"])!=P.sha(schema.model_json_schema())
                or record.get("usage")!=(record.get("raw_response") or {}).get("usage")):
            raise RuntimeError("Saved answer provider request/schema/model/usage mismatch")
        final=record
    restored=None
    if final["status"]=="ok":
        try:restored=_restore_generated_answer(schema.model_validate(final["parsed"]),evidence).model_dump(mode="json")
        except (ValueError,LlmUngrounded) as error:
            if row["status"]=="ok":raise RuntimeError("Successful checkpoint fails operating ownership restoration") from error
    if (row["status"]=="ok" and restored!=row["answer"]) or (row["status"]=="failed" and restored is not None):
        raise RuntimeError("Saved answer differs from restored provider output")


def verified_historical(question, profile, scope, *, manifest):
    """Read the original42 files, never the post-scoring combined row hash."""
    from pickcardu_rag.answering import _generated_answer_model, _restore_generated_answer, serialize_answer_payload
    prior=P.DATA/"42_expanded_query_evaluation"
    protocol=P.F.read_json(prior/"execution_protocol.json")
    expected=manifest["historical_execution_sha256"]
    if protocol["sha256"]!=expected or P.sha({k:v for k,v in protocol.items() if k!="sha256"})!=expected:
        raise RuntimeError("Historical execution contract changed")
    for key,wanted in dict(model="gpt-6-luna",reasoning="medium",answer_output=2400,bundle="budget5",
                           condition_depth=50,candidate_depth=20,top_k=3).items():
        if protocol[key]!=wanted:raise RuntimeError("Historical fixed configuration differs")
    filename=f"{question['query_id']}_{profile}_S_THEN_U.json"
    retrieval=P.F.read_json(prior/"retrieval"/filename);answer=P.F.read_json(prior/"answers"/filename)
    if (retrieval["execution_sha256"]!=expected or answer["execution_sha256"]!=expected
            or answer["retrieval_sha256"]!=P.sha(retrieval) or answer["original_answer_question"]!=question["question"]
            or retrieval["query_id"]!=question["query_id"] or retrieval["profile"]!=profile):
        raise RuntimeError("Historical answer/question/retrieval binding differs")
    plans=P.F.read_json(prior/"parser_results.json")["records"]
    plan=next(r["plan"] for r in plans if r["query_id"]==question["query_id"])
    old_scope=retrieval["selection"]["eligible_cards"] if not retrieval["fallback"] else plan["card_keys"] or None
    if scope!=old_scope:raise RuntimeError("Historical scope differs")
    key=P.sha(dict(question=question["question"],profile=profile,keys=scope))
    component=P.F.read_json(prior/"components"/(key+".json"))
    identity=component["identity"]
    chunks=f"notebooks/data/31_operational_chunking_comparison/{profile}_chunks.jsonl"
    if (P.sha({k:v for k,v in component.items() if k!="sha256"})!=component["sha256"]
            or identity["question_sha256"]!=P.sha(question["question"]) or identity["scope"]!=scope
            or identity["profile"]!=profile or identity["chunks_sha256"]!=manifest["data_sha256"][chunks]
            or any(component[k]!=retrieval["result"]["trace"][k] for k in ["bm25","vector"])):
        raise RuntimeError("Historical component/chunk identity differs")
    original_indices={r["path"]:r["sha256"] for r in P.F.read_json(prior/"protected_files.json")["files"]
                      if r["path"].startswith("notebooks/data/31_operational_chunking_comparison/")}
    for relative,wanted in manifest["data_sha256"].items():
        if relative.startswith("notebooks/data/31_operational_chunking_comparison/") and original_indices.get(relative)!=wanted:
            raise RuntimeError("Historical operating index differs from current frozen input")
    evidence=retrieval["result"]["evidence"]
    payload=P.sha([dict(role="user",content=serialize_answer_payload(question["question"],evidence))])
    final=None;schema=_generated_answer_model(len(evidence))
    for event in answer.get("events",[]):
        paths=[P.DATA/d/"api_cache"/(event["key"]+".json") for d in [
            "42_expanded_query_evaluation","41_query_expansion","40_new_query_comparison",
            "36_profile_search_comparison","35_answer_budget","30_structured_benefit_db"]]
        source=next((p for p in paths if p.exists()),None)
        if source is None:raise RuntimeError("Historical provider record missing")
        record=P.F.read_json(source);request=record["request"]
        if (P.sha(request)!=event["key"] or P.sha(request["input"])!=payload or request["model"]!="gpt-6-luna"
                or request["reasoning"]!={"effort":"medium"} or request["max_output_tokens"]!=2400
                or P.sha(request["schema"])!=P.sha(schema.model_json_schema())):
            raise RuntimeError("Historical provider payload/schema/model differs")
        final=record
    if evidence and (final is None or final["status"]!="ok" or _restore_generated_answer(
            schema.model_validate(final["parsed"]),evidence).model_dump(mode="json")!=answer["answer"]):
        raise RuntimeError("Historical restored provider output differs")
    return retrieval,answer,payload


class Runtime:
    def __init__(self, stage):
        import numpy as np
        import torch
        self.stage, self.path = stage, P.F.STAGES[stage]
        self.manifest = P.F.read_json(self.path / "execution_manifest.json")
        P.F.initialize(stage)
        torch.set_num_threads(4)
        self.np = np
        self.started, self.cpu_started = time.perf_counter(), time.process_time()
        self.minimum_available_kib = None
        self.B = P.load("preproduction_budget_functions", "34_bundle_experiment.py")
        self.B.F.STAGES[34] = self.path
        self.guard = self.B.TokenGuard()
        from pickcardu_rag.retrieval import LocalReranker
        self.delegate = LocalReranker(str(P.ROOT / ".cache/reranker/bge-reranker-v2-m3"))
        self.reranker_contract = self.delegate.artifact_contract("bge")
        self.data, self.scorer_calls = {}, []
        self.index_identity = {}
        self.model_loaded = False

    def write(self, name, value):
        P.F.write_json(self.path / name, value)

    def memory(self):
        available = int(next(l.split()[1] for l in Path("/proc/meminfo").read_text().splitlines()
                             if l.startswith("MemAvailable:")))
        self.minimum_available_kib = min(self.minimum_available_kib or available, available)
        if available < 1048576:
            raise RuntimeError("Host memory below 1 GiB reserve; stop before another reranker call")

    def score(self, mode, query, documents, **kwargs):
        from pickcardu_rag.errors import RerankerUnavailable
        values, hits = [], 0
        started = time.perf_counter()
        for document in documents:
            lengths = self.guard.lengths(query, document)
            if lengths["pair_tokens"] > self.guard.max_length or (
                    kwargs.get("document_token_limit") is not None
                    and lengths["document_tokens"] > kwargs["document_token_limit"]):
                raise RerankerUnavailable("Unchanged reranker token bound exceeded; truncation forbidden")
            key = P.sha(dict(mode=mode, q=query, doc=document, kwargs=kwargs, contract=self.reranker_contract))
            current = self.path / "bge_cache" / (key + ".json")
            old_dirs = ["42_expanded_query_evaluation", "41_query_expansion", "40_new_query_comparison",
                        "34_bundle_comparison", "32_structured_search_comparison"]
            paths = [current] + [P.DATA / d / "bge_cache" / current.name for d in old_dirs]
            found = next((p for p in paths if p.exists()), None)
            if found:
                value = P.F.read_json(found)["score"]
                hits += 1
            else:
                self.memory()
                if not self.model_loaded and self.minimum_available_kib < 3145728:
                    raise RuntimeError("First reranker allocation requires 3 GiB available memory")
                scores, trace = self.delegate.score(mode, query, [document], **kwargs)
                self.model_loaded = True
                if len(scores) != 1:
                    raise RuntimeError("Operating singleton reranker returned a different pair count")
                value = scores[0]
                self.write("bge_cache/" + current.name, dict(score=value, query_sha256=P.sha(query),
                           document_sha256=P.sha(document), measured_trace=trace))
            values.append(float(value))
        trace = dict(cache_hits=hits, new_pairs=len(values)-hits, device="cpu", singleton=True,
                     wall_seconds=time.perf_counter()-started)
        self.scorer_calls.append(trace)
        return values, trace

    def profile(self, profile):
        if profile in self.data:
            return self.data[profile]
        import chromadb
        from chromadb.config import Settings
        from pickcardu_rag.retrieval import Chunk
        from pickcardu_rag_api.index import SQLiteFTSSearcher, ChromaVectorSearcher
        source = P.DATA / "31_operational_chunking_comparison" / profile
        destination = self.path / "indexes" / profile
        receipt = self.path / "index_copies" / (profile + ".json")
        if not receipt.exists():
            if destination.exists():
                raise RuntimeError("Unfinished private index copy requires inspection")
            before = [P.F.file_record(p) for p in sorted(source.rglob("*")) if p.is_file()]
            if any(r["path"].endswith("-wal") and r["size"] for r in before):
                raise RuntimeError("Source index has nonempty WAL")
            shutil.copytree(source, destination)
            for r in before:
                old = P.ROOT / r["path"]
                new = destination / old.relative_to(source)
                if P.F.file_record(old) != r or P.sha(new.read_bytes()) != r["sha256"] or old.stat().st_ino == new.stat().st_ino:
                    raise RuntimeError("Private index copy failed preservation/identity check")
            files=[dict(source=r["path"],copy=str((destination/(P.ROOT/r["path"]).relative_to(source)).relative_to(P.ROOT)),
                        sha256=r["sha256"],bytes=r["size"]) for r in before]
            self.write("index_copies/" + profile + ".json", dict(files=files, copy_bytes=sum(r["size"] for r in before)))
        self.B.F.validate_copy_receipt(P.F.read_json(receipt),self.path/"indexes")
        records = [json.loads(line) for line in (P.DATA / "31_operational_chunking_comparison" / (profile + "_chunks.jsonl")).read_text().splitlines()]
        chunks = []
        for r in records:
            m = r["metadata"]
            chunks.append(Chunk(r["chunk_id"], r["text"], r["document_id"], m["card_name"], m["issuer_name"], r["level"],
                min(m["source_pages"]), m.get("section"), m["reranker_text"], m.get("parent_id"), tuple(m.get("child_ids", [])),
                m.get("node_id"), tuple(m.get("heading_path", [])), m.get("part_index", 1), tuple(m.get("related_chunk_ids", [])), m.get("optional_parent_heading")))
        client = chromadb.PersistentClient(path=str(destination / "chroma"), settings=Settings(anonymized_telemetry=False))
        collection = client.get_collection(profile)
        matrix = self.np.load(source / "embeddings.npy", allow_pickle=False, mmap_mode="r")
        ids=[chunk.chunk_id for chunk in chunks]
        stored=collection.get(ids=ids,include=["embeddings","metadatas"])
        by_id={key:(vec,meta) for key,vec,meta in zip(stored["ids"],stored["embeddings"],stored["metadatas"],strict=True)}
        if set(by_id)!=set(ids) or collection.count()!=len(ids):raise RuntimeError("Private Chroma IDs changed")
        for i,key in enumerate(ids):
            vec,meta=by_id[key]
            if not self.np.array_equal(matrix[i],self.np.asarray(vec,dtype=self.np.float32)) or meta["document_id"]!=chunks[i].card_key:
                raise RuntimeError("Private Chroma vector/card identity changed")
        logical=dict(chunks_sha256=P.sha((P.DATA/"31_operational_chunking_comparison"/(profile+"_chunks.jsonl")).read_bytes()),
            fts_sha256=P.sha((source/"corpus.sqlite").read_bytes()),embeddings_sha256=P.sha((source/"embeddings.npy").read_bytes()),
            chroma_ids_sha256=P.sha(ids),chroma_metadata_sha256=P.sha([by_id[k][1] for k in ids]),
            chroma_configuration=P.E.chroma_config_identity(collection.configuration),collection_metadata=collection.metadata)
        contract=self.path/"index_copies"/(profile+"_logical_identity.json")
        if contract.exists() and P.F.read_json(contract)!=logical:raise RuntimeError("Resumed logical index identity differs")
        if not contract.exists():self.write(str(contract.relative_to(self.path)),logical)
        self.index_identity[profile]=logical
        self.data[profile] = (chunks, SQLiteFTSSearcher(source / "corpus.sqlite"),
                             ChromaVectorSearcher(collection, "text-embedding-3-small", chunks=chunks, embeddings=matrix), client)
        return self.data[profile]

    def search(self, question, vector, profile, scope=None, *, supplement=None, depth=50, baseline_result=None):
        from pickcardu_rag.retrieval import RagPipeline, SearchConfig, CHUNKING_PROFILES, Candidate, _candidate_dict
        self.memory()
        chunks, lexical, dense, _ = self.profile(profile)
        identity = P.sha(dict(question=question,vector=P.sha(self.np.asarray(vector,dtype=self.np.float32).tobytes()),
                              profile=profile,scope=scope,depth=depth))
        paths = {}
        for label, text, embedding, keys in [("original", question, vector, scope)] + (
                [("supplement", supplement["question"], supplement["vector"], supplement["scope"])] if supplement else []):
            if label == "original" and baseline_result is not None:
                if baseline_result.get("experiment_component_identity") != identity:
                    raise RuntimeError("Baseline component query/vector/profile/scope/depth changed")
                paths[label] = {name:[Candidate(**r) for r in records] for name,records in
                                baseline_result["experiment_paths"]["original"].items()}
                continue
            kw = dict(limit=depth)
            if keys is not None:
                kw["card_keys"] = tuple(keys)
            paths[label] = dict(bm25=lexical.search(text, **kw), vector=dense.search(embedding, **kw))
        components = {}
        for name in ["bm25", "vector"]:
            components[name] = merge_components(paths["original"][name], paths["supplement"][name], depth) if supplement else paths["original"][name]
        pipeline = RagPipeline(chunks, FixedSearcher(components["bm25"], depth), FixedSearcher(components["vector"], depth),
                               self, profile=CHUNKING_PROFILES[profile])
        def bundles(rows, mapping):
            return self.B.build_budgeted(rows, mapping, max_chunks=5, fits=lambda text: self.guard.fits(question, text))
        config = SearchConfig(profile=profile, vector_weight=.4, component_depth=depth, candidate_depth=20,
            top_k=3, reranker="bge", reranker_route="all" if profile=="parent_child_bundle" else "selective", answer_payload_bytes=64000)
        result = self.B.isolated_search(bundles)(pipeline, question, vector, config)
        result["experiment_paths"] = {label:{name:[_candidate_dict(r) for r in records] for name,records in value.items()}
                                      for label,value in paths.items()}
        result["experiment_scope"] = scope
        result["experiment_component_identity"] = identity
        return result

    def embeddings(self, texts, *, allow_network=False):
        keys = [P.sha(dict(text=t, model="text-embedding-3-small", dimension=1536)) for t in texts]
        target = self.path / "api_cache/embeddings"
        target.mkdir(parents=True, exist_ok=True)
        directories = [P.F.STAGES[s]/"api_cache/embeddings" for s in [47,48,49]] + [P.DATA/d/"api_cache/embeddings" for d in
            ["42_expanded_query_evaluation","41_query_expansion","40_new_query_comparison","30_structured_benefit_db"]]
        paths = {key:next((d/(key+".npy") for d in directories if (d/(key+".npy")).exists()),None) for key in keys}
        missing = list(dict.fromkeys(k for k in keys if paths[k] is None))
        for offset in range(0,len(missing),32):
            batch=missing[offset:offset+32]
            request=dict(model="text-embedding-3-small",input=[texts[keys.index(k)] for k in batch],dimensions=1536)
            path=self.path/"api_cache"/("embedding_batch_"+P.sha(request)+".json")
            if not allow_network:raise RuntimeError("Offline embedding cache miss")
            if path.exists():raise RuntimeError("Interrupted embedding request requires inspection, not automatic retry")
            for relative,wanted in {**self.manifest["source_sha256"],**self.manifest["data_sha256"]}.items():
                if P.sha((P.ROOT/relative).read_bytes())!=wanted:raise RuntimeError("Frozen embedding source/input changed")
            allowed = set(self.manifest["allowed_embedding_text_sha256"])
            auxiliary_contract = self.path/"embedding_text_contract.json"
            if auxiliary_contract.exists():
                extra=P.F.read_json(auxiliary_contract)
                if extra["execution_manifest_sha256"]!=P.sha(self.manifest):raise RuntimeError("Auxiliary embedding contract changed")
                allowed.update(extra["text_sha256"])
            if any(P.sha(t) not in allowed for t in request["input"]):
                raise RuntimeError("Embedding input outside frozen text list")
            cost=P.ledger()
            reserve=Decimal(len(P.F.canonical(request).encode())+8192)*Decimal(".02")/1000000
            if cost["unknown_cost_records"] or Decimal(cost["known_cost_usd"])+reserve>Decimal("8"):
                raise RuntimeError("Embedding blocked by global cost uncertainty/cap")
            own=list((self.path/"api_cache").glob("*.json"))
            priced=P.PRICE.aggregate(P.PRICE.price_records([(str(self.stage),str(p),P.F.read_json(p)) for p in own]))
            if len(own)>=self.manifest["maximum_physical_requests"] or Decimal(priced["known_cost_usd"])+reserve>Decimal(self.manifest["stage_cost_cap_usd"]):
                raise RuntimeError("Embedding blocked by stage request/cost cap")
            record=dict(status="started",request=request,started_at=time.time())
            self.write(str(path.relative_to(self.path)),record)
            try:
                response=P.E.C.api_client().embeddings.create(**request)
                raw=response.model_dump(mode="json")
                record.update(raw_response=raw,usage=raw.get("usage",{}))
                self.write(str(path.relative_to(self.path)),record)
                values=sorted(response.data,key=lambda r:r.index)
                if len(values)!=len(batch):raise ValueError("Embedding count mismatch")
                for key,value in zip(batch,values,strict=True):
                    vector=self.np.asarray(value.embedding,dtype=self.np.float32)
                    if vector.shape!=(1536,) or not self.np.isfinite(vector).all():raise ValueError("Invalid embedding")
                    paths[key]=target/(key+".npy");self.np.save(paths[key],vector)
                record.update(status="ok",finished_at=time.time())
            except Exception as error:
                record.update(status="failed",error=str(error),finished_at=time.time())
                raise
            finally:self.write(str(path.relative_to(self.path)),record)
        vectors=self.np.asarray([self.np.load(paths[k],allow_pickle=False) for k in keys],dtype=self.np.float32)
        if vectors.shape!=(len(texts),1536) or not self.np.isfinite(vectors).all():raise ValueError("Invalid cached embedding")
        return vectors

    def resources(self):
        return dict(wall_seconds=time.perf_counter()-self.started,cpu_seconds=time.process_time()-self.cpu_started,
            peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,minimum_available_kib=self.minimum_available_kib,
            new_reranker_pairs=sum(r["new_pairs"] for r in self.scorer_calls),reranker_calls=len(self.scorer_calls),
            gpu="미사용",api_ledger=P.ledger(),actual_billed_cost="미측정")
