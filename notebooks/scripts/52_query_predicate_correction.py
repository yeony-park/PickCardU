"""Isolated query correction reused by notebooks 52–54; frozen V1 stays intact.

The notebook owns experiments and reports. This adapter owns the same SQL fact
predicate, installation guard and legacy-cost accounting across resumptions.
It never writes an original DB, operating source, or previous experiment row.
"""
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / "notebooks/data/52_four_retrieval_modes"
CORRECTED = ROOT / "notebooks/data/52_corrected_four_retrieval_modes"
TOTAL_STAGE_CAP = Decimal("2")
VERSION = "exception-context-v2"


def evaluate_fact(Q, fact, requirement):
    """Keep explicit filters; a mention in an exception is not a whole-row ban.

    Conditional exclusions and positive exceptions remain in the complete fact
    supplied to answering. No text is removed, rewritten, or declared verified.
    """
    r = Q.Requirement.model_validate(requirement).model_dump(mode="json")
    fields = fact["fields"]
    wanted = Q.P.E.DB._expanded_targets(r["targets"])
    if not any(Q.target_matches(t, fields["target"]) for t in wanted):
        return False
    excluded = Q.P.E.DB._expanded_targets(r["exclude_targets"])
    if any(Q.target_matches(t, fields["target"]) for t in excluded):
        return False
    adjusted = {**r, "targets": [], "exclude_targets": []}
    derived = dict(fact["derived"])
    if derived.get("cap_period_state") != "known":
        explicit = Q.P.E.explicit_cap_period(fields["cap"])
        if explicit:
            derived.update(cap_period=explicit, cap_period_state="known")
    if Q.P.E.DB._evaluate_fact({**fact, "derived": derived}, adjusted).status != "true":
        return False
    return Q.frequency_matches(fields["frequency"], r["min_frequency"], r["frequency_period"])


def legacy_inventory(X):
    return {str(p.relative_to(ROOT)): X.P.sha(p.read_bytes())
            for p in sorted((LEGACY / "api_cache").glob("*.json"))}


def legacy_cost_usd(X):
    records = [("520", str(p.relative_to(ROOT)), X.P.F.read_json(p))
               for p in sorted((LEGACY / "api_cache").glob("*.json"))]
    priced = X.P.PRICE.aggregate(X.P.PRICE.price_records(records))
    if priced["unknown_cost_records"]:
        raise RuntimeError("Original stage52 has an unpriced request")
    return Decimal(priced["known_cost_usd"])


def remaining_stage_cap(X):
    remaining = TOTAL_STAGE_CAP - legacy_cost_usd(X)
    if remaining <= 0:
        raise RuntimeError("Original stage52 exhausted the shared allocation")
    return remaining


def guard_installed(X):
    marker = X.query_predicate_correction
    if X.Q.evaluate_fact is not marker["function"]:
        raise RuntimeError("Corrected predicate was replaced in this process")
    if X.P.sha(Path(__file__).read_bytes()) != marker["implementation_sha256"]:
        raise RuntimeError("Corrected predicate implementation changed")
    if X.P.sha((ROOT / "notebooks/scripts/50_query_contract.py").read_bytes()) != marker["original_source_sha256"]:
        raise RuntimeError("Frozen original predicate source changed")
    if legacy_inventory(X) != marker["legacy_api_inventory"]:
        raise RuntimeError("Original API journal changed after correction freeze")
    if (X.P.F.STAGES[52] != CORRECTED or X.P.F.STAGES[520] != LEGACY
            or sum(p == LEGACY for p in X.P.F.STAGES.values()) != 1):
        raise RuntimeError("Original stage52 accounting was omitted or duplicated")


def install(X):
    if getattr(X, "query_predicate_correction", None):
        guard_installed(X)
        return
    if any((LEGACY / "answers").glob("*.json")):
        raise RuntimeError("Expected the preserved pre-answer original trial")
    original_source = ROOT / "notebooks/scripts/50_query_contract.py"
    if Path(X.Q.evaluate_fact.__code__.co_filename).resolve() != original_source:
        raise RuntimeError("Install must start from the original imported predicate")
    source_hash = X.P.sha(original_source.read_bytes())
    Q = X.Q

    def corrected(fact, requirement):
        return evaluate_fact(Q, fact, requirement)

    Q.evaluate_fact = corrected
    for common in [X.P, Q.P]:
        common.STAGES[520] = LEGACY.name
        common.STAGES[52] = CORRECTED.name
    for common in [X.P.F, X.P.A.F, Q.P.F, Q.P.A.F]:
        common.STAGES[520] = LEGACY
        common.STAGES[52] = CORRECTED
    X.STAGES[52] = CORRECTED.name
    X.query_predicate_correction = dict(version=VERSION, function=corrected,
        implementation_sha256=X.P.sha(Path(__file__).read_bytes()), original_source_sha256=source_hash,
        legacy_api_inventory=legacy_inventory(X), legacy_known_cost_usd=str(legacy_cost_usd(X)),
        remaining_stage_cap_usd=str(remaining_stage_cap(X)))
    previous_reserve = X.Cache.reserve

    def reserve(self, normalized):
        guard_installed(X)
        return previous_reserve(self, normalized)

    X.Cache.reserve = reserve
    guard_installed(X)
