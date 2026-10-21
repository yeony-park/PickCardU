"""Whole-unit retrieval/answer adapters reused by experiment notebooks 52–54.

The helper keeps candidate provenance, immutable input contracts and resumable
answer journals identical across development and confirmation. It generates no
reports and never changes operating code, facts, chunking or source indexes.
"""
from pathlib import Path
from dataclasses import replace
import copy
import time

import importlib.util
import sys
spec = importlib.util.spec_from_file_location("modes52_runtime", Path(__file__).with_name("51_experiment_runtime.py"))
X = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = X
spec.loader.exec_module(X)
P, Q, R = X.P, X.Q, X.R


def structured_units(routing):
    units = []
    facts = Q.facts_by_id()
    for card in routing["eligible_cards"]:
        for fid in routing["matching_fact_ids"][card]:
            f = facts[fid]
            if f["document_id"] != card:
                raise RuntimeError("Structured fact/card ownership mismatch")
            pages = [int(e["line_id"][1:5]) for rows in f["field_evidence"].values() for e in rows]
            units.append(dict(unit_id="db:"+fid, chunk_id=fid, card_key=card,
                card_name=f["identity"]["card_name"], issuer=f["identity"]["issuer_name"],
                text=Q.serialize_fact(f), origin="structured", page_num=min(pages) if pages else 0,
                level="structured_fact", section=f["fields"]["benefit_type"], fact_id=fid))
    return units


def unstructured_units(runtime, result, profile):
    """Recover the exact operating hydration texts before top3 packaging."""
    from pickcardu_rag.retrieval import Candidate, parent_child_bundles
    chunks, *_ = runtime.profile(profile)
    mapping = {c.chunk_id: c for c in chunks}
    texts = {}
    for bundle in result["trace"].get("bundles", []):
        ids = bundle["selected_chunk_ids"]
        view = dict(mapping)
        view[ids[0]] = replace(mapping[ids[0]], related_chunk_ids=tuple(ids[1:]))
        formatted = parent_child_bundles([Candidate(ids[0], 0., 1)], view, max_chunks=len(ids))[0]
        if formatted["selected_chunk_ids"] != ids or formatted["bundle_sha256"] != bundle["bundle_sha256"]:
            raise RuntimeError("Unstructured bundle reconstruction changed original text")
        texts.update(formatted["evidence_texts"])
    if profile == "parent_child_bundle" and any(
            row["chunk_id"] not in texts for row in result["trace"]["evidence_hydration"]):
        raise RuntimeError("Parent-child hydration escaped reconstructed bundles")
    units = []
    seen = set()
    for row in result["trace"]["evidence_hydration"]:
        cid = row["chunk_id"]
        if cid in seen:
            continue
        seen.add(cid)
        c = mapping[cid]
        units.append(dict(unit_id="u:"+cid, chunk_id=cid, card_key=c.card_key, card_name=c.card_name,
            issuer=c.issuer, text=texts.get(cid, c.text), origin="unstructured", page_num=c.page_num,
            section=c.section, level=c.level))
    return units


def union_units(structured, unstructured):
    """No SQL-order limit; preserve distinct fact and chunk provenance."""
    seen, units = set(), []
    for unit in [*structured, *unstructured]:
        if unit["unit_id"] in seen:
            continue
        seen.add(unit["unit_id"])
        units.append(copy.deepcopy(unit))
    return units


def pack_ranked(question, units, scores, *, top_cards=3):
    from pickcardu_rag.answering import measure_answer_payload
    import math
    if len(units) != len(scores) or not all(math.isfinite(float(s)) for s in scores):
        raise RuntimeError("Nonfinite or incomplete unit scores")
    ranked = sorted([{**u, "score": float(s)} for u,s in zip(units,scores,strict=True)],
                    key=lambda u: (-u["score"], u["card_key"], u["unit_id"]))
    card_scores, counts = {}, {}
    for unit in ranked:
        key = unit["card_key"]
        card_scores[key] = max(card_scores.get(key, -float("inf")), unit["score"])
        counts[key] = counts.get(key, 0) + 1
    chosen = sorted(card_scores, key=lambda k: (-card_scores[k], k))[:top_cards]
    evidence, cards, skipped = [], [], []
    for key in chosen:
        accepted = 0
        for unit in (u for u in ranked if u["card_key"] == key):
            if accepted >= 5:
                continue
            item = {k: unit[k] for k in ["chunk_id","card_key","card_name","issuer","text","page_num","section","level","score"]}
            item.update(rank=len(evidence)+1, origin=unit["origin"])
            if measure_answer_payload(question,[*evidence,item])[0] > 64000:
                skipped.append(unit["unit_id"])
                continue
            evidence.append(item)
            accepted += 1
        if accepted:
            source = next(u for u in ranked if u["card_key"] == key)
            cards.append(dict(card_key=key,card_name=source["card_name"],issuer=source["issuer"],
                score=card_scores[key],rank=len(cards)+1,evidence_count=accepted))
    return dict(cards=cards,evidence=evidence,trace=dict(candidate_cards=sorted(card_scores),
        card_scores=card_scores,unit_count_by_card=counts,ranked_units=ranked,
        omitted_for_payload=skipped,payload_bytes=measure_answer_payload(question,evidence)[0],
        card_score_policy="max_unit_score; more units offer more opportunities for a high score"))


def rank_units(runtime, question, units):
    start, cpu = time.perf_counter(), time.process_time()
    scores, trace = runtime.score("bge", question, [u["text"] for u in units], document_token_limit=4096) if units else ([], {})
    result = pack_ranked(question,units,scores)
    result["experiment_timing"] = dict(search_wall_seconds=time.perf_counter()-start,
        search_cpu_seconds=time.process_time()-cpu,common_rerank=trace)
    return result


def assert_scope(result, scope):
    if scope is not None and any(e["card_key"] not in scope for e in [*result["cards"], *result["evidence"]]):
        raise RuntimeError("Evidence escaped the original user card scope")


def bind_retrieval(stage, row, *, question, profile, mode, parser_record, manifest, independent_u=None,
                   repeat=1, extra_inputs=None, new=False):
    """Bind loaded retrievals to current fixed inputs and a first-write digest."""
    identity = (f"{question['query_id']}:S_ONLY:r{repeat}" if mode == "S_ONLY" else
                f"{question['query_id']}:{profile}:{mode}:r{repeat}")
    expected = dict(identity=identity,query_id=question['query_id'],profile=profile,mode=mode,repeat=repeat,
        manifest_sha256=P.sha(manifest))
    if any(row.get(k) != v for k,v in expected.items()):
        raise RuntimeError("Retrieval identity/mode/profile/manifest mismatch")
    route, original = parser_record['routing'], parser_record['original_scope']
    scope = route['eligible_cards'] if mode == 'S_ONLY' else route['scope'] if mode in ['SEQ','A','B'] else original['card_keys']
    inputs = dict(**expected,question_sha256=P.sha(question['question']),parser_record_sha256=P.sha(parser_record),
        original_scope_sha256=P.sha(original),actual_search_scope=scope,
        structured_witness_sha256=P.sha(dict(cards=route['eligible_cards'],facts=route['matching_fact_ids']))
            if mode in ['S_ONLY','SEQ','PAR','A','B'] else None,
        independent_U_sha256=P.sha(independent_u) if mode=='PAR' else None,extra_inputs=extra_inputs)
    if mode == 'PAR' and independent_u is None:
        raise RuntimeError("PAR checkpoint has no bound independent U result")
    if row.get('original_scope') != original or row.get('route') != (None if mode=='U' else route):
        raise RuntimeError("Retrieval original scope or structured route changed")
    assert_scope(row['result'],scope)
    assert_scope(row['result'],original['card_keys'])
    if mode in ['U','SEQ'] and row['result'].get('experiment_scope',scope) != scope:
        raise RuntimeError('Recorded vector/BM25 scope differs from expected search scope')
    contract = dict(inputs=inputs,input_binding=P.sha(inputs),result_sha256=P.sha(row['result']))
    target = P.F.STAGES[stage]/'retrieval_contracts'/(identity+'.json')
    if new:
        row.update(input_binding=contract['input_binding'],result_sha256=contract['result_sha256'])
        X.frozen(target,contract)
    elif not target.exists() or P.F.read_json(target) != contract:
        raise RuntimeError("Saved retrieval input/result differs from first-write contract")
    if row.get('input_binding') != contract['input_binding'] or row.get('result_sha256') != contract['result_sha256']:
        raise RuntimeError("Saved retrieval row digest changed")
    return row


def supplement(question, result, routing):
    """Add full matching DB facts only to the answer input; never evict U text."""
    from pickcardu_rag.answering import measure_answer_payload
    supplemented = copy.deepcopy(result)
    cards = {c["card_key"] for c in result["cards"]}
    additions = [u for u in structured_units(routing) if u["card_key"] in cards]
    appended = []
    for unit in additions:
        item = {k: unit[k] for k in ["chunk_id","card_key","card_name","issuer","text","page_num","section","level"]}
        item.update(rank=len(supplemented["evidence"])+1,origin="structured",score=None)
        if measure_answer_payload(question,[*supplemented["evidence"],item])[0] <= 64000:
            supplemented["evidence"].append(item)
            appended.append(unit["fact_id"])
    supplemented["supplement"] = dict(eligible_fact_count=len(additions),appended_fact_ids=appended,
        unchanged_U_evidence_sha256=P.sha(result["evidence"]),no_eviction=True)
    assert supplemented["evidence"][:len(result["evidence"])] == result["evidence"]
    return supplemented


def answer(stage, identity, question, result, *, allow_network=False, reused=None):
    from pickcardu_rag.answering import OpenAIService, serialize_answer_payload, _generated_answer_model
    path = P.F.STAGES[stage]
    manifest = P.F.read_json(path/"execution_manifest.json")
    evidence = result["evidence"]
    evidence_hash = P.sha(evidence)
    input_hash = P.sha([dict(role="user",content=serialize_answer_payload(question,evidence))])
    schema_hash = P.sha(_generated_answer_model(len(evidence)).model_json_schema()) if evidence else None
    contract = dict(execution_manifest_sha256=P.sha(manifest),input_sha256_by_identity={identity:input_hash},
                    schema_sha256_by_identity={identity:schema_hash})
    X.frozen(path/"payload_contracts"/(identity+".json"),contract)
    global_path = path/"answer_payload_contract.json"
    combined = P.F.read_json(global_path) if global_path.exists() else dict(execution_manifest_sha256=P.sha(manifest),
        input_sha256_by_identity={},schema_sha256_by_identity={})
    if combined["execution_manifest_sha256"] != P.sha(manifest):
        raise RuntimeError("Global payload contract changed manifest")
    for name in ["input_sha256_by_identity","schema_sha256_by_identity"]:
        if identity in combined[name] and combined[name][identity] != contract[name][identity]:
            raise RuntimeError("Saved answer input/schema changed")
        combined[name].update(contract[name])
    P.F.write_json(global_path,combined)
    target = path/"answers"/(identity+".json")
    if target.exists():
        row = P.F.read_json(target)
    else:
        start, cpu = time.perf_counter(), time.process_time()
        api = X.Cache(stage,"answer",identity,allow_network=allow_network)
        value, usage, error = None, {}, None
        if reused is not None:
            if (identity.replace(':B:',':A:') != reused['identity'] or identity == reused['identity'] or
                    reused['input_sha256'] != input_hash or reused.get('evidence_sha256') != evidence_hash or
                    reused['manifest_sha256'] != P.sha(manifest)):
                raise RuntimeError('Only same-repeat identical-input A/B answer sharing is permitted')
            value, usage, error = copy.deepcopy(reused['answer']), {}, reused.get('error')
        elif not evidence:
            text = "발급사와 정확한 카드명을 확인해 주세요." if result.get("clarify") else "검색된 근거가 없습니다."
            value = dict(answer_status="insufficient_evidence",answer_text=text,recommendations=[],claims=[])
        else:
            try:
                parsed,usage = OpenAIService(api_key=None,client=api,llm_model="gpt-6-luna",answer_payload_bytes=64000).answer(question,evidence)
                value = parsed.model_dump(mode="json")
            except Exception as exc:
                if not api.events:
                    raise
                error = f"{type(exc).__name__}: {exc}"
        row = dict(identity=identity,status="ok" if value is not None else "failed",answer=value,usage=usage,error=error,
            events=api.events,request_keys=[e["key"] for e in api.events],manifest_sha256=P.sha(manifest),
            payload_contract_sha256=P.sha(contract),input_sha256=input_hash,retrieval_sha256=P.sha(result),
            evidence_sha256=evidence_hash,reuse_source_sha256=P.sha(reused) if reused is not None else None,
            answer_ready_wall_seconds=time.perf_counter()-start,answer_cpu_seconds=time.process_time()-cpu,
            first_token_seconds=None,browser_display_seconds=None,cache_policy="fresh identity for each mode/repeat")
        P.F.write_json(target,row)
    if row["retrieval_sha256"] != P.sha(result):
        raise RuntimeError("Answer/retrieval checkpoint changed")
    if row.get('evidence_sha256') != evidence_hash:
        raise RuntimeError('Answer evidence ownership/content changed')
    R.validate_generated_answer(row,question,evidence,stage=stage,identity=identity,manifest=manifest,contract=contract,reused=reused)
    return row
