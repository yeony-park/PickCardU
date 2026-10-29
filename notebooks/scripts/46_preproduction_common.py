"""Shared experiment-only routing and paid-call guard for notebooks 46–49.

Notebook cells own fixtures, execution and evaluation. Existing operating code
is imported read-only; this module never activates an index or writes old data.
"""
from pathlib import Path
from decimal import Decimal
import hashlib
import importlib.util
import json
import math
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "notebooks/data"
STAGES = {46: "46_query_boundary_validation", 47: "47_partial_condition_recovery",
          48: "48_structured_retrieval_guidance", 49: "49_selected_pipeline_confirmation"}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


F = load("preproduction_io", "34_followup_common.py")
A = load("preproduction_response_cache", "35_answer_experiment.py")
PRICE = load("preproduction_prices", "36_cost_accounting.py")
E = load("preproduction_schema", "32_search_experiment.py")
for number, directory in STAGES.items():
    F.STAGES[number] = DATA / directory
    A.F.STAGES[number] = DATA / directory


def sha(value):
    return F.digest(value)


def compact(text):
    return re.sub(r"\s+", "", text).casefold()


def explicit_scope(question, catalog):
    """Only exact full catalog names; shorter overlapping names lose to longer ones."""
    text = compact(question)
    matched = [(key, compact(name)) for key, name in catalog.items()
               if len(compact(name)) >= 4 and compact(name) in text]
    return sorted(key for key, name in matched
                  if not any(name != other and name in other for _, other in matched)) or None


def semantic_flags(question, plan):
    """Conservative literal checks, NOT a complete natural-language verifier.

    shortcut: implicit meanings and omitted conditions can escape these checks;
    require source review and confirmatory evaluation before operating use.
    """
    if not question:
        return []
    text = compact(question)
    requirements = [r for group in plan["groups"] for r in group]
    flags = []
    percentages = {float(v) for v in re.findall(r"(\d+(?:\.\d+)?)\s*%", question)}
    for r in requirements:
        if r["min_percent"] is not None and float(r["min_percent"]) not in percentages:
            flags.append("percent_not_in_question")
        if "할인" in text and not any(word in text for word in ["적립", "캐시백"]) and r["action"] == "earn":
            flags.append("action_conflicts_with_discount")
        if "적립" in text and "할인" not in text and r["action"] == "discount":
            flags.append("action_conflicts_with_earn")
        if ("월한도" in text or "월할인한도" in text) and r["cap_period"] == "day":
            flags.append("period_conflicts_with_month")
    # Explicit conjunction only; '과' inside an ordinary word is not a relation.
    if len(plan["groups"]) > 1 and any(word in text for word in ["모두", "각각", "동시에", "둘다"]) and not any(
            word in text for word in ["또는", "중하나", "혹은"]):
        flags.append("or_conflicts_with_explicit_and")
    targets = compact(" ".join(t for r in requirements for t in r["targets"]))
    for literal in ["넷플릭스", "GS25", "CU", "병원", "약국"]:
        if compact(literal) in text and compact(literal) not in targets:
            flags.append("missing_literal_target:" + literal)
    if "커피" in text and not any(t in targets for t in ["커피", "카페", "스타벅스", "투썸", "이디야", "폴바셋"]):
        flags.append("missing_literal_target:커피")
    return sorted(set(flags))


def route(question, value, facts, catalog, *, trusted_scope=None, parser_failed=False,
          scope_unclear=False, check_semantics=True):
    keys = set(catalog)
    scope = sorted(set(trusted_scope)) if trusted_scope is not None else None
    if scope is not None and (not scope or not set(scope) <= keys):
        raise ValueError("Trusted scope must be nonempty known catalog keys")
    base = {"route": "unstructured", "scope": scope, "reason": None,
            "eligible_cards": [], "selection": None, "semantic_flags": []}
    if scope_unclear:
        return {**base, "route": "clarify", "scope": [], "reason": "unclear_explicit_scope"}
    if parser_failed:
        return {**base, "reason": "parser_failure"}
    try:
        normalized = E.QueryPlan.model_validate(value).model_dump(mode="json")
        if not set(normalized["card_keys"]) <= keys:
            raise ValueError("Unknown parser card key")
        for group in normalized["groups"]:
            if not group:
                raise ValueError("Empty inner AND")
            for requirement in group:
                numeric = [requirement[k] for k in ["min_percent", "max_spend_krw", "min_cap_krw"]]
                if any(v is not None and (not math.isfinite(v) or v < 0) for v in numeric):
                    raise ValueError("Invalid numeric condition")
                if not any(str(t).strip() for t in requirement["targets"]):
                    raise ValueError("Empty benefit target")
        if not normalized["groups"] or normalized["unresolved"] or normalized["route"] == "unstructured":
            return {**base, "reason": "empty_unresolved_or_unstructured"}
        # The parser cannot invent a narrower card scope than independently identified text.
        if normalized["card_keys"] and (scope is None or set(normalized["card_keys"]) != set(scope)):
            return {**base, "reason": "untrusted_parser_scope"}
        flags = semantic_flags(question, normalized) if check_semantics else []
        if flags:
            return {**base, "reason": "semantic_conflict", "semantic_flags": flags}
        normalized["card_keys"] = scope or []
        selection = E.DB.select_cards(facts, normalized)
        eligible = sorted(selection["eligible_cards"])
        return {**base, "route": "structured_then_unstructured" if eligible else "unstructured",
                "scope": eligible if eligible else scope, "reason": "matched" if eligible else "no_eligible_cards",
                "eligible_cards": eligible, "selection": selection}
    except (ValueError, TypeError, KeyError) as error:
        return {**base, "reason": "invalid_plan", "error": str(error)}


def ledger():
    records = []
    for stage in STAGES:
        for path in sorted((F.STAGES[stage] / "api_cache").glob("*.json")):
            value = F.read_json(path)
            records.append((str(stage), str(path.relative_to(ROOT)), value))
    return PRICE.aggregate(PRICE.price_records(records))


def validate_parser_row(row, *, identity, query_id, repeat, question, manifest, request_key, cache):
    """Reject mixed question/repeat rows and payloads during notebook resume."""
    expected = {"identity": identity, "query_id": query_id, "repeat": repeat, "question": question,
                "question_sha256": sha(question), "manifest_sha256": sha(manifest),
                "request_key": request_key}
    if any(row.get(k) != v for k, v in expected.items()):
        raise RuntimeError("Saved parser row identity/input/manifest mismatch")
    if not row.get("events") or any(event["key"] != request_key for event in row["events"]):
        raise RuntimeError("Saved parser event key mismatch")
    record = F.read_json(Path(cache) / (request_key + ".json"))
    request = record["request"]
    if (sha(request["input"]) != manifest["allowed_request_by_identity"][identity]
            or sha(request["instructions"]) != manifest["parser_instructions_sha256"]
            or sha(request["schema"]) != manifest["parser_schema_sha256"]
            or request["model"] != manifest["model"]
            or request["reasoning"] != {"effort": manifest["reasoning"]}
            or request["max_output_tokens"] != manifest["max_output_tokens"]):
        raise RuntimeError("Saved provider request differs from frozen parser input")
    if record["status"] != row["status"] or (record["status"] == "ok" and record["parsed"] != row["plan"]):
        raise RuntimeError("Saved parser row differs from durable raw cache")


class BudgetCache(A.ResponseCache):
    """Existing raw-before-parse cache with identity salts and pre-call reservation."""
    def __init__(self, stage, kind, identity, *, allow_network=False):
        self.stage, self.kind, self.identity = stage, kind, identity
        self.manifest = F.read_json(F.STAGES[stage] / "execution_manifest.json")
        super().__init__(cache=F.STAGES[stage] / "api_cache",
                         old_cache=F.STAGES[stage] / "no_prior_cache", allow_network=allow_network)

    def normalized(self, kwargs):
        result = super().normalized(kwargs)
        result["max_output_tokens"] = 3000 if self.kind == "parser" else 2400
        return result

    def key(self, kwargs):
        return sha({"request": super().key(kwargs), "identity": self.identity, "kind": self.kind})

    def deliver(self, stored, schema):
        # Failed calls remain failed on resume; never silently make a paid retry.
        if stored["status"] != "ok":
            raise RuntimeError("Saved unsuccessful provider request: " + stored.get("error", stored["status"]))
        return super().deliver(stored, schema)

    def parse(self, **kwargs):
        normalized = self.normalized(kwargs)
        key = self.key(kwargs)
        if not (self.cache / (key + ".json")).exists() and self.allow_network:
            self.reserve(normalized)
        return super().parse(**kwargs)

    def reserve(self, normalized):
        for relative, wanted in self.manifest["source_sha256"].items():
            if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != wanted:
                raise RuntimeError("Frozen source changed: " + relative)
        if self.identity not in self.manifest["allowed_identities"][self.kind]:
            raise RuntimeError("Request identity outside frozen matrix")
        if self.stage == 46:
            if sha(normalized["instructions"]) != self.manifest["parser_instructions_sha256"] or sha(
                    normalized["input"]) != self.manifest["allowed_request_by_identity"][self.identity] or sha(
                    normalized["text_format"].model_json_schema()) != self.manifest["parser_schema_sha256"]:
                raise RuntimeError("Parser input outside frozen questions")
        current = ledger()
        if current["unknown_cost_records"]:
            raise RuntimeError("Unknown prior request cost; inspect before another paid call")
        own_records = list(self.cache.glob("*.json"))
        cap = self.manifest["maximum_physical_requests"]
        # UTF-8 byte count upper-bounds tokens; include full schema plus protocol overhead.
        wire = self.wire_kwargs(normalized)
        byte_bound = len(F.canonical(wire).encode()) + 8192
        input_rate = Decimal("0.25") if byte_bound > 272000 else Decimal("0.125")
        output_rate = Decimal("0.75") if byte_bound > 272000 else Decimal("0.50")
        reserve = (Decimal(byte_bound) * input_rate + Decimal(normalized["max_output_tokens"]) * output_rate) / 1000000
        if Decimal(current["known_cost_usd"]) + reserve > Decimal("8"):
            raise RuntimeError("Global 8 USD cap including reservation reached")
        if len(own_records) >= cap:
            raise RuntimeError("Stage physical request cap reached")
        own = PRICE.aggregate(PRICE.price_records([
            (str(self.stage), str(p.relative_to(ROOT)), F.read_json(p)) for p in own_records]))
        if Decimal(own["known_cost_usd"]) + reserve > Decimal(self.manifest["stage_cost_cap_usd"]):
            raise RuntimeError("Stage cost allocation including reservation reached")

