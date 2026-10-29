"""Shared query contract for notebook experiments 50–54 (never operating code)."""
from pathlib import Path
import importlib.util
import json
import re
import sqlite3
import sys
import time
from functools import lru_cache
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("legacy50", Path(__file__).with_name("46_preproduction_common.py"))
P = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = P
spec.loader.exec_module(P)
FIELDS = ["target", "action", "benefit_type", "value", "unit", "condition", "cap", "period", "frequency", "exceptions"]

class Requirement(P.E.Requirement):
    min_frequency: int | None = Field(description="Minimum allowed uses, only an explicit search constraint; null for asking how many")
    frequency_period: Literal["month", "day", "any"]

    @model_validator(mode="after")
    def validate_bounds(self):
        import math
        for name in ["min_percent", "max_spend_krw", "min_cap_krw", "min_frequency"]:
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError("Negative or nonfinite filter")
        if not any(t.strip() for t in self.targets):
            raise ValueError("A benefit filter requires a target")
        return self


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[list[Requirement]] = Field(description="OR between groups, AND within a group, all requirements on the SAME card; empty outer list means no benefit filter")
    requested_fields: list[Literal["target", "action", "benefit_type", "value", "unit", "condition", "cap", "period", "frequency", "exceptions"]] = Field(description="Information to explain, NOT WHERE predicates unless an explicit constraint also appears")
    unsupported_filters: list[str] = Field(description="Explicit hard constraints that cannot be represented; NOT requested explanations or answer details")

    @model_validator(mode="after")
    def validate_groups(self):
        if any(not group for group in self.groups):
            raise ValueError("Empty inner AND is invalid")
        return self


PROMPT = """카드 질문을 고정 스키마의 조회 조건으로 변환하세요. SQL 자체를 생성하지 않습니다.
원질문의 명시된 혜택 대상/할인·적립/숫자 제한만 groups에 넣습니다. 각 group의 조건은 동일 카드에서 모두 만족해야 하며 서로 다른 혜택 행이어도 됩니다.
대상 목록은 한 혜택의 또는 관계이고, 커피와 넷플릭스처럼 함께 필요한 혜택은 한 group 안의 별도 조건입니다.
커피 혜택은 targets=[커피], action=any, 할인은 discount, 적립/캐시백은 earn입니다. 요구하지 않은 %를 만들지 마세요.
월 몇 번인가/전월실적과 한도가 얼마인가/최소 결제금액도 알려줘는 requested_fields이며 필터 실패가 아닙니다.
반대로 월 3회 이상 가능한/전월실적 기준 30만원 이하/월 한도 1만원 이상은 실제 숫자 필터입니다.
사람의 이용실적·결제금액이나 가정 계산용 숫자는 해당 카드의 최소 실적/혜택률 필터로 오인하지 마세요. 6500원 결제하면 적용되는가의 숫자는 설명 자료입니다.
금액은 원, 혜택률은 퍼센트 숫자, 횟수는 정수로 출력하세요. '월'과 '일'을 구분하세요.
기차를 철도로 교체하거나 오타를 임의 보정하지 마세요. 그대로 추출하고 DB 일치 여부에 맡깁니다.
카드명·발급사는 외부의 독립 범위 처리에서 적용하므로 혜택 targets에 넣지 마세요.
unsupported_filters에는 표현할 수 없는 명시적 필터만 넣고 상세 설명 요청은 넣지 마세요.
불명확한 생활 추천처럼 혜택 대상을 특정할 수 없으면 groups=[]입니다. 빈 내부 group은 만들지 마세요.
모든 필드를 명시하고 요청하지 않은 숫자는 null, 기간은 any로 출력하세요."""


def requirement(targets, **kwargs):
    result = {**P.E.req(targets), "min_frequency": None, "frequency_period": "any"}
    result.update(kwargs)
    return result


def plan(requirements, requested_fields=None, unsupported_filters=None):
    return dict(groups=[requirements] if requirements else [], requested_fields=requested_fields or [],
                unsupported_filters=unsupported_filters or [])


@lru_cache(maxsize=1)
def facts_by_id():
    return {f["fact_id"]: f for f in (json.loads(s) for s in
            (ROOT / "notebooks/data/30_structured_benefit_db/facts.jsonl").read_text().splitlines())}

@lru_cache(maxsize=1)
def documents():
    return [json.loads(s) for s in (ROOT / "notebooks/data/30_structured_benefit_db/documents.jsonl").read_text().splitlines()]


def target_matches(target, text):
    """Existing aliases only; prevent the observed 기차/전기차 false witness."""
    target, text = P.compact(target), P.compact(text)
    if not target:
        return False
    if target == "기차":
        text = text.replace("전기차", "")
    return target in text


def original_scope(question):
    catalog = {d["document_id"]: d["identity"]["card_name"] for d in documents()}
    exact = P.explicit_scope(question, catalog)
    compact = P.compact(question)
    issuer = None
    if re.search(r"(?<![a-z])(?:ibk|기업)(?:카드|은행)?", compact):
        issuer = "ibk"
    elif re.search(r"(?<![a-z])bc(?:바로)?(?:카드)?|비씨(?:바로)?(?:카드)?", compact):
        issuer = "BC"
    issuer_keys = sorted(k for k in catalog if k.split("/")[0] == issuer) if issuer else None
    keys = sorted(set(exact) & set(issuer_keys)) if exact and issuer_keys is not None else exact or issuer_keys
    unclear = "스카이패스카드" in compact and exact is None
    # A named fabricated product must not silently turn into a broad recommendation.
    unknown_product = bool(re.search(r"[A-Za-z가-힣]+카드\s*[Xx]-\d+", question)) and exact is None
    return dict(card_keys=keys, issuer=issuer, exact_products=exact or [],
                clarify=unclear or unknown_product, reason="ambiguous_or_unknown_product" if unclear or unknown_product else None)


def frequency_matches(text, minimum, period):
    if minimum is None:
        return True
    text = re.sub(r"\s+", "", text)
    pattern = {"month": r"(?:월|매월)(\d+)회", "day": r"(?:일|하루)(\d+)회", "any": r"(\d+)회"}[period]
    values = [int(s) for s in re.findall(pattern, text)]
    # Mixed frequency tiers are unknown, not the most favourable value.
    return len(set(values)) == 1 and values[0] >= minimum


def evaluate_fact(fact, requirement):
    r = Requirement.model_validate(requirement).model_dump(mode="json")
    fields = fact["fields"]
    wanted = P.E.DB._expanded_targets(r["targets"])
    if not any(target_matches(t, fields["target"]) for t in wanted):
        return False
    if any(target_matches(t, fields["target"]) for t in P.E.DB._expanded_targets(r["exclude_targets"])):
        return False
    # Delegate numeric/period/action interpretation to the unchanged typed facts.
    adjusted = {**r, "targets": [], "exclude_targets": []}
    derived = dict(fact["derived"])
    # The stored cap period ignored the cap text for some facts (e.g. H08).
    # Interpret an unambiguous explicit period at read time, without DB writes.
    if derived.get("cap_period_state") != "known":
        explicit = P.E.explicit_cap_period(fields["cap"])
        if explicit:
            derived.update(cap_period=explicit, cap_period_state="known")
    evaluated = {**fact, "derived": derived}
    if P.E.DB._evaluate_fact(evaluated, adjusted).status != "true":
        return False
    # The legacy evaluator's exception check depends on wanted targets.
    if any(target_matches(t, fact["derived"].get("exceptions_normalized", "")) for t in wanted):
        return False
    return frequency_matches(fields["frequency"], r["min_frequency"], r["frequency_period"])


def query(value, scope=None, *, database=None):
    """Run bound SELECTs on the original immutable DB; UDFs never write to it.

    One EXISTS condition per requirement per card, allowing distinct benefit
    rows within the same card. Return all witnesses, never an SQL-order top20.
    """
    started = time.perf_counter()
    value = QueryPlan.model_validate(value).model_dump(mode="json")
    known_keys = {d["document_id"] for d in documents()}
    if scope is not None and not set(scope) <= known_keys:
        raise ValueError("Unknown original card scope")
    path = database or ROOT / "notebooks/data/30_structured_benefit_db/structured.sqlite"
    db = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    facts = facts_by_id()
    db.create_function("matches_requirement", 2, lambda fid, req: int(evaluate_fact(facts[fid], json.loads(req))), deterministic=True)
    logs, by_group = [], []
    try:
        for group_index, group in enumerate(value["groups"]):
            clauses, params = [], []
            if scope is not None:
                clauses.append("d.document_id IN (" + ",".join("?" for _ in scope) + ")" if scope else "0")
                params.extend(scope)
            if value["unsupported_filters"]:
                clauses.append("0")
            for r in group:
                clauses.append("EXISTS (SELECT 1 FROM facts f WHERE f.document_id=d.document_id AND matches_requirement(f.fact_id, ?)=1)")
                params.append(P.F.canonical(r))
            sql = "SELECT d.document_id FROM documents d WHERE " + " AND ".join(clauses) + " ORDER BY d.document_id"
            cards = [row[0] for row in db.execute(sql, params)]
            logs.append(dict(sql=sql, parameters=params, eligible_cards=cards))
            matches = {}
            for card in cards:
                matches[card] = [[row[0] for row in db.execute(
                    "SELECT fact_id FROM facts WHERE document_id=? AND matches_requirement(fact_id, ?)=1 ORDER BY ordinal",
                    (card, P.F.canonical(r)))] for r in group]
            by_group.append(matches)
        # No filter is a real empty structured result; it does not select every card.
        if not value["groups"]:
            sql = "SELECT document_id FROM documents WHERE 0"
            db.execute(sql).fetchall()
            logs.append(dict(sql=sql, parameters=[], eligible_cards=[]))
    finally:
        db.close()
    requirement_ids = {}
    matched = {}
    for index, group in enumerate(by_group):
        for key, groups in group.items():
            requirement_ids.setdefault(key, {})[str(index)] = groups
            matched.setdefault(key, set()).update(fid for rows in groups for fid in rows)
    return dict(db_attempted=True, eligible_cards=sorted(matched), matching_fact_ids={k: sorted(v) for k, v in matched.items()},
                requirement_fact_ids=requirement_ids, query_log=logs, plan=value,
                db_wall_seconds=time.perf_counter() - started)


def route(value, original, *, parser_failed=False):
    base = dict(route="U", scope=original["card_keys"], eligible_cards=[], matching_fact_ids={}, db_attempted=False)
    if original["clarify"]:
        return {**base, "route": "clarify", "reason": original["reason"]}
    if parser_failed:
        return {**base, "reason": "parser_failure"}
    try:
        result = query(value, scope=original["card_keys"])
    except (ValueError, TypeError) as exc:
        return {**base, "reason": "invalid_schema", "error": str(exc)}
    # DB/IO errors intentionally propagate as technical failures, not zero results.
    cards = result["eligible_cards"]
    return {**base, **result, "route": "SEQ" if cards else "U", "scope": cards or original["card_keys"],
            "reason": "matched" if cards else "no_results"}


def serialize_fact(fact):
    line_ids = sorted({e["line_id"] for rows in fact["field_evidence"].values() for e in rows})
    source = dict(document="data/raw/" + fact["document_id"] + ".pdf", normalized_json=fact["source_relative_path"],
                  source_pdf_sha256=fact["source_pdf_sha256"],
                  pages=sorted({int(line[1:5]) for line in line_ids}), line_ids=line_ids)
    # Insertion order is frozen. json sort_keys is intentionally not used here.
    record = {"card_key": fact["document_id"], "card_name": fact["identity"]["card_name"], "fact_id": fact["fact_id"],
              **{name: fact["fields"][name] for name in FIELDS}, "source": source}
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))
