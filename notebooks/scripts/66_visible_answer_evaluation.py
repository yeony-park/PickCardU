"""Screen-answer review binding and aggregation, shared by experiments66--69.

Notebooks own semantic reviews and execution. This helper prevents a hidden
claim, stale review, or partial matrix from changing the displayed-answer score.
It does not infer truth, call a provider, or generate HTML.
"""
from collections import Counter
from functools import lru_cache
import hashlib
from itertools import product
import json
from pathlib import Path
import re
import statistics


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "notebooks/data/66_visible_answer_policy"
UPSTREAM = ROOT / "notebooks/data/65_fixed_profile_confirmation"
POLICY_VERSION = "visible_answer66_v1"
STAGE_FOLDERS = {66: "66_visible_answer_policy", 68: "68_structured_evidence_generation", 69: "69_repaired_sequential_confirmation"}
_FILE_DIGEST_CACHE = {}


def expected_roles(stage):
    base = "notebooks/data/" + STAGE_FOLDERS[stage] + "/"
    return {"policy": "notebooks/data/66_visible_answer_policy/policy.json",
            "evaluator": "notebooks/scripts/66_visible_answer_evaluation.py",
            "question_contract": base + "question_contract.json",
            "source_registry": base + "source_registry.json",
            "source_proofs": base + "source_proof_inventory.json", "provider_inputs": base + "input_verification.json",
            "payload_contract": "notebooks/data/65_fixed_profile_confirmation/answer_payload_contract.json" if stage == 66 else base + "answer_payload_contract.json"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_digest(path):
    path = Path(path).resolve()
    stat = path.stat()
    key = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if key in _FILE_DIGEST_CACHE:
        return _FILE_DIGEST_CACHE[key]
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    value = h.hexdigest()
    _FILE_DIGEST_CACHE[key] = value
    return value


def verify_file_map(files, root=ROOT, *, fresh=False):
    if not files:
        raise ValueError("Preservation file map is empty")
    if fresh:
        _FILE_DIGEST_CACHE.clear()
    root = Path(root).resolve()
    for name, expected in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_digest(path) != expected:
            raise ValueError("Preserved file changed: " + name)
    return len(files)


def read(path):
    return json.loads(Path(path).read_text())


def visible_surfaces(payload):
    if payload is None:
        return {}
    body = payload.get("answer_text", "")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Missing visible answer body")
    surfaces = {"answer_text": body}
    for i, recommendation in enumerate(payload.get("recommendations", [])):
        text = recommendation.get("reason", "")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Missing visible recommendation reason")
        surfaces[f"recommendations.{i}.reason"] = text
    return surfaces


def validate_rubric(rubric, question):
    if rubric.get("question") != question or not rubric.get("query_id"):
        raise ValueError("Question contract changed")
    requirements = rubric.get("requirements", [])
    ids = [r["id"] for r in requirements]
    if not ids or len(ids) != len(set(ids)) or any(not r.get("description") for r in requirements):
        raise ValueError("Missing/duplicate direct requirements")


def binding(job, answer_row, rubric, context):
    validate_rubric(rubric, job["question"])
    if job["identity"] != answer_row["identity"] or job["query_id"] != rubric["query_id"]:
        raise ValueError("Answer identity differs from question")
    return dict(policy_version=POLICY_VERSION, evaluation_context_sha256=digest(context), identity=job["identity"],
                question_sha256=digest(job["question"]), rubric_sha256=digest(rubric),
                answer_row_sha256=digest(answer_row), job_result_sha256=digest(job["result"]),
                actual_input_sha256=answer_row.get("input_sha256"),
                visible_surfaces_sha256=digest(visible_surfaces(answer_row.get("answer"))))


def _visible_quote(item, surfaces):
    surface, quote = item.get("surface"), item.get("quote")
    if surface not in surfaces or not isinstance(quote, str) or not quote.strip() or quote not in surfaces[surface]:
        raise ValueError("Quote is absent from the actual visible surface")


@lru_cache(maxsize=4)
def _page_reference_text(path, expected_sha):
    if file_digest(path) != expected_sha:
        raise ValueError("Source page reference registry changed")
    result = {}
    for card in read(path)["records"]:
        for fact in card["facts"]:
            if fact.get("reference_unit") == "pdf_page":
                key = (card["card_key"], fact["condition_id"])
                text = fact["source_support"][0]["quote"]
                if key in result and result[key] != text:
                    raise ValueError("Same source page has conflicting reference text")
                result[key] = text
    return result


def _check_context(job, answer_row, allowed_source_refs, context):
    roles = {"policy", "evaluator", "question_contract", "source_registry", "source_proofs", "provider_inputs", "payload_contract"}
    if not roles <= set(context.get("artifact_roles", {})):
        raise ValueError("Incomplete evaluation context roles")
    if context["artifact_roles"] != expected_roles(context["stage"]):
        raise ValueError("Evaluation authority role path was replaced")
    if not set(context["artifact_roles"].values()) <= set(context.get("files_sha256", {})):
        raise ValueError("Unbound policy/source/provider context artifact")
    verify_file_map(context["files_sha256"], ROOT)
    if context["source_refs_sha256_by_query"].get(job["query_id"]) != digest(sorted(allowed_source_refs)):
        raise ValueError("Source reference registry changed")
    proof = context["input_receipts_by_identity"].get(job["identity"])
    if (not proof or proof.get("status") != "verified_actual_saved_input"
            or proof["answer_row_sha256"] != digest(answer_row)
            or proof["job_result_sha256"] != digest(job["result"])
            or proof["input_sha256"] != answer_row.get("input_sha256")):
        raise ValueError("Actual input/provider receipt is missing or changed")


def verify_saved_input(job, answer_row, base=UPSTREAM):
    """Rebuild the real payload and follow exact same-repeat physical reuse."""
    from pickcardu_rag.answering import serialize_answer_payload

    base = Path(base)
    expected_input = [{"role": "user", "content": serialize_answer_payload(job["question"], job["result"]["evidence"])}]
    expected_sha = digest(expected_input)
    if (answer_row.get("input_sha256") != expected_sha
            or answer_row.get("evidence_sha256") != digest(job["result"]["evidence"])
            or answer_row.get("retrieval_sha256") != digest(job["result"])):
        raise ValueError("Saved job evidence differs from actual input binding")
    contract_path = base / "payload_contracts" / (job["identity"] + ".json")
    contract = read(contract_path)
    if (digest(contract) != answer_row["payload_contract_sha256"]
            or contract["input_sha256_by_identity"].get(job["identity"]) != expected_sha):
        raise ValueError("Saved provider payload contract differs")
    files = {str(contract_path.relative_to(ROOT)): file_digest(contract_path)}
    physical, seen, reused = answer_row, set(), []
    while physical.get("reuse_source_identity"):
        source_id = physical["reuse_source_identity"]
        if source_id in seen:
            raise ValueError("Cyclic physical answer reuse")
        seen.add(source_id)
        path = base / "answers" / (source_id + ".json")
        source = read(path)
        current_parts, source_parts = physical["identity"].split(":"), source_id.split(":")
        if (digest(source) != physical["reuse_source_sha256"] or source.get("answer") != physical.get("answer")
                or current_parts[0:2] != source_parts[0:2] or current_parts[-1] != source_parts[-1]
                or source.get("input_sha256") != expected_sha):
            raise ValueError("Physical source differs from same-repeat answer/input")
        files[str(path.relative_to(ROOT))] = file_digest(path)
        reused.append(source_id)
        physical = source
    keys = physical.get("request_keys", [])
    if not keys and not job["result"].get("clarify") and answer_row["status"] == "ok":
        raise ValueError("Successful generated answer lacks its provider request")
    for key in keys:
        path = base / "api_cache" / (key + ".json")
        raw = read(path)
        if raw["request"]["input"] != expected_input or digest(raw["request"]["input"]) != expected_sha:
            raise ValueError("Actual transmitted provider input differs from saved evidence")
        files[str(path.relative_to(ROOT))] = file_digest(path)
    return dict(status="verified_actual_saved_input", identity=job["identity"], input_sha256=expected_sha,
                answer_row_sha256=digest(answer_row), job_result_sha256=digest(job["result"]),
                reused_source_identities=reused, physical_request_count=len(keys), files_sha256=files)


def score_review(job, answer_row, rubric, review, allowed_source_refs, *, context):
    _check_context(job, answer_row, allowed_source_refs, context)
    expected = binding(job, answer_row, rubric, context)
    if any(review.get(k) != v for k, v in expected.items()):
        raise ValueError("Stale/missing review binding")
    if not review.get("reviewed_by") or not review.get("reason"):
        raise ValueError("Missing semantic reviewer/reason")
    surfaces = visible_surfaces(answer_row.get("answer"))
    requirements = review.get("requirements", [])
    wanted = {r["id"] for r in rubric["requirements"]}
    if len(requirements) != len(wanted) or {r.get("id") for r in requirements} != wanted:
        raise ValueError("Review dropped/invented a direct requirement")
    for item in requirements:
        if item.get("status") not in {"satisfied", "missing", "uncertain"} or not item.get("reason"):
            raise ValueError("Incomplete direct requirement judgment")
        if item["status"] == "satisfied" and not item.get("quotes"):
            raise ValueError("Direct fulfillment requires visible quotes")
        for quote in item.get("quotes", []):
            _visible_quote(quote, surfaces)
    audits = review.get("surface_audits", [])
    if len(audits) != len(surfaces) or {a.get("surface") for a in audits} != set(surfaces):
        raise ValueError("Incomplete/duplicate visible surface audit")
    evidence = {e["chunk_id"]: e for e in job["result"].get("evidence", [])}
    errors = []
    for audit in audits:
        surface = audit["surface"]
        spans = audit.get("spans", [])
        cursor = 0
        for span in spans:
            if (span.get("start") != cursor or type(span.get("end")) is not int
                    or not cursor < span["end"] <= len(surfaces[surface])
                    or span.get("quote") != surfaces[surface][cursor:span["end"]]
                    or span.get("kind") not in {"factual", "question_context", "nonfactual"}
                    or not span.get("reason")):
                raise ValueError("Visible factual span partition is incomplete or changed")
            if span["kind"] == "factual":
                if not span.get("card_keys") or not span.get("source_refs"):
                    raise ValueError("Visible factual span has no owning card/source")
                owners = set(span["card_keys"])
                refs_in_span = {(r["card_key"], r["condition_id"]) for r in span["source_refs"]}
                if {k for k, _ in refs_in_span} != owners or not refs_in_span <= allowed_source_refs:
                    raise ValueError("Factual span source belongs to another card or registry")
                for ref in span["source_refs"]:
                    if ":pdf_page66." in ref["condition_id"]:
                        name = context["artifact_roles"]["source_registry"]
                        pages = _page_reference_text(str(ROOT / name), context["files_sha256"][name])
                        text = pages.get((ref["card_key"], ref["condition_id"]), "")
                        quote = ref.get("quote")
                        if not isinstance(quote, str) or not quote.strip() or quote not in text:
                            raise ValueError("Source page reference lacks an owning-page literal quote")
                if span.get("status") not in {"correct", "incorrect", "uncertain"}:
                    raise ValueError("Visible factual span lacks a semantic judgment")
                if span.get("input_status") not in {"supported", "partial", "unsupported", "uncertain"}:
                    raise ValueError("Factual span lacks its own input-support judgment")
                supplied = set()
                for quote in span.get("input_quotes", []):
                    item = evidence.get(quote.get("chunk_id"))
                    text = quote.get("quote")
                    if (item is None or not isinstance(text, str) or not text.strip()
                            or text not in item["text"] or item["card_key"] not in owners):
                        raise ValueError("Factual span input quote has wrong card or text")
                    supplied.add(item["card_key"])
                if span["input_status"] == "supported" and supplied != owners:
                    raise ValueError("Supported factual span lacks owning-card input proof")
                if surface.startswith("recommendations."):
                    index = int(surface.split(".")[1])
                    if owners != {answer_row["answer"]["recommendations"][index]["card_key"]}:
                        raise ValueError("Visible recommendation span belongs to another card")
            elif span["kind"] == "question_context":
                if span.get("status") not in {"correct", "incorrect", "uncertain"}:
                    raise ValueError("Question context needs a semantic accuracy judgment")
                quotes = span.get("question_quotes", [])
                if not quotes or any(not q.strip() or q not in job["question"] for q in quotes):
                    raise ValueError("Question context has no literal question pointer")
                if not set(re.findall(r"\d+(?:[.,]\d+)*", span["quote"])) <= set(re.findall(r"\d+(?:[.,]\d+)*", job["question"])):
                    raise ValueError("Question context invented a numeric assertion")
                if span["quote"].strip() not in job["question"] and not re.search(r"(?:알|확인|계산|확정|판단|특정|비교|단정).{0,15}(?:수(?:는|가|도)?\s*없|어려|어렵|불가|못|되지\s*않)|알아야.{0,40}(?:계산|확인|판단)|정보.{0,12}(?:필요|부족)|추가.{0,10}(?:확인|정보)|(?:근거|자료).{0,30}(?:미기재|명시되지|안내되지|제시되지)", span["quote"]):
                    raise ValueError("Nonliteral question context must be an uncertainty explanation")
            elif span["kind"] == "nonfactual":
                if re.search(r"\d|%", span["quote"]):
                    raise ValueError("A numeric assertion cannot be hidden as nonfactual")
                neutral = re.sub(r"[\s.,:;!?·•*#()\-]", "", span["quote"])
                headings = {"", "추천카드", "추천이유", "확인사항", "참고", "요약"}
                notices = {"신청전공식상품설명서를확인해주세요", "신청전공식상품설명서를다시확인해주세요",
                           "카드신청전상품설명서및약관을확인해주세요", "카드신청전상품설명서와약관을확인해주세요"}
                official_notice = (re.fullmatch(r"(?:카드)?신청(?:이용|결제)?전(?:에|에는)?(?:공식)?상품설명서[가-힣A-Za-z·/()]*확인(?:해주세요|해보세요|하세요|하시기바랍니다)", neutral)
                                   and not re.search(r"무제한|무조건|항상|모든|없(?:이|음|습니다)|됩니다|돼요|보장|확정", neutral))
                if neutral not in headings | notices and not official_notice:
                    raise ValueError("Nonfactual span is not an allowed neutral heading/notice")
            cursor = span["end"]
        if cursor != len(surfaces[surface]):
            raise ValueError("Visible factual span partition does not cover the full surface")
        factual_statuses = [s["status"] for s in spans if s["kind"] != "nonfactual"]
        span_status = "incorrect" if "incorrect" in factual_statuses else "uncertain" if "uncertain" in factual_statuses else "correct"
        if audit["status"] != span_status:
            raise ValueError("Factual span error cannot disappear in surface status")
        factual_spans = [s for s in spans if s["kind"] == "factual"]
        input_statuses = [s["input_status"] for s in factual_spans]
        input_status = ("not_applicable" if not input_statuses else "supported" if all(s == "supported" for s in input_statuses)
                        else "unsupported" if all(s == "unsupported" for s in input_statuses)
                        else "uncertain" if "uncertain" in input_statuses else "partial")
        if audit.get("input_status") != input_status:
            raise ValueError("Factual span input support cannot disappear in surface status")
        span_refs = {(r["card_key"], r["condition_id"]) for s in factual_spans for r in s["source_refs"]}
        if {(r["card_key"], r["condition_id"]) for r in audit.get("source_refs", [])} != span_refs:
            raise ValueError("Surface source references differ from its factual spans")
        if audit.get("status") not in {"correct", "incorrect", "uncertain"} or not audit.get("reason"):
            raise ValueError("Incomplete factual accuracy review")
        if audit.get("input_status") not in {"supported", "partial", "unsupported", "uncertain", "not_applicable"}:
            raise ValueError("Missing input-support judgment")
        refs = {(r["card_key"], r["condition_id"]) for r in audit.get("source_refs", [])}
        if not refs <= allowed_source_refs:
            raise ValueError("Unknown source fact reference")
        if audit.get("basis") not in {"source", "question_context"}:
            raise ValueError("Unknown accuracy basis")
        if audit["basis"] == "source" and not refs:
            raise ValueError("Source accuracy needs a source reference")
        if surface.startswith("recommendations."):
            index = int(surface.split(".")[1])
            card = answer_row["answer"]["recommendations"][index]["card_key"]
            if refs and any(k != card for k, _ in refs):
                raise ValueError("Recommendation source belongs to another card")
        if audit["input_status"] == "supported" and audit["basis"] == "source" and not audit.get("input_quotes"):
            raise ValueError("Supported factual input requires actual evidence quotes")
        for quote in audit.get("input_quotes", []):
            item = evidence.get(quote.get("chunk_id"))
            text = quote.get("quote")
            if item is None or not isinstance(text, str) or not text.strip() or text not in item["text"]:
                raise ValueError("Input quote is absent from the delivered evidence")
            if refs and item["card_key"] not in {k for k, _ in refs}:
                raise ValueError("Input quote belongs to another card")
        if audit["status"] == "incorrect" and not audit.get("errors"):
            raise ValueError("Incorrect visible statement requires a concrete error")
        if audit["status"] != "incorrect" and audit.get("errors"):
            raise ValueError("Visible error cannot disappear from factual status")
        for error in audit.get("errors", []):
            _visible_quote({"surface": surface, "quote": error.get("quote")}, surfaces)
            if not error.get("code") or not error.get("reason"):
                raise ValueError("Unexplained visible factual error")
            errors.append(dict(surface=surface, **error))
    if not isinstance(review.get("optional_details"), dict) or not isinstance(review.get("claims_diagnostic"), dict):
        raise ValueError("Missing separate optional/hidden diagnostics")
    technical = answer_row["status"] == "ok"
    direct = bool(requirements) and all(r["status"] == "satisfied" for r in requirements)
    correct = bool(audits) and all(a["status"] == "correct" for a in audits)
    grounded = bool(audits) and all(a["input_status"] in {"supported", "not_applicable"} for a in audits)
    return {**{k: job[k] for k in ("identity", "query_id", "profile", "mode", "repeat")},
            "technical_success": technical, "direct_request_satisfied": direct,
            "visible_factual_accuracy": correct, "input_fully_supported": grounded,
            "passed": technical and direct and correct, "visible_errors": errors,
            "missing_requirement_ids": [r["id"] for r in requirements if r["status"] != "satisfied"],
            "review_sha256": digest(review), "binding": expected}


def aggregate(rows, *, query_ids, profiles, modes, repeats, context):
    verify_file_map(context["files_sha256"], ROOT, fresh=True)
    if any(r["binding"]["evaluation_context_sha256"] != digest(context) for r in rows):
        raise ValueError("Evaluation context changed before aggregation")
    expected = set(product(query_ids, profiles, modes, repeats))
    actual = [(r["query_id"], r["profile"], r["mode"], r["repeat"]) for r in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Incomplete/duplicate evaluation matrix")
    result = []
    for profile, mode in product(profiles, modes):
        chosen = [r for r in rows if r["profile"] == profile and r["mode"] == mode]
        counts = {q: sum(r["passed"] for r in chosen if r["query_id"] == q) for q in query_ids}
        result.append(dict(profile=profile, mode=mode, question_count=len(query_ids), logical_answers=len(chosen),
                           passed=sum(counts.values()), pass_macro=statistics.mean(v / len(repeats) for v in counts.values()),
                           per_query_pass_count=counts,
                           axes={field: sum(r[field] for r in chosen) / len(chosen) for field in
                                 ("technical_success", "direct_request_satisfied", "visible_factual_accuracy", "input_fully_supported")},
                           visible_error_counts=dict(Counter(e["code"] for r in chosen for e in r["visible_errors"]))))
    return result
