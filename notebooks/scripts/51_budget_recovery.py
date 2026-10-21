"""Explicit user-authorized HTTP520 recovery without rewriting frozen trials.

The exception debits a full request upper bound, never calls that actual usage,
and covers only the hashed failed request. Further unknown requests still stop.
Kept separate so the frozen parser prompt/code and its 57 original rows remain
unchanged. One process only, as in the approved experiment execution contract.
"""
from pathlib import Path
from decimal import Decimal
import copy
import json

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "notebooks/data/51_query_extraction_scope/budget_recovery_contract.json"


def install(X):
    if getattr(X, "http520_recovery_installed", False):
        return
    P = X.P
    policy = P.F.read_json(POLICY)
    policy_sha256 = P.sha(policy)
    P.STAGES[510] = "51_query_extraction_scope/http520_recovery"
    P.F.STAGES[510] = P.DATA / P.STAGES[510]
    P.A.F.STAGES[510] = P.F.STAGES[510]
    prior_reserve = X.Cache.reserve

    def guard_policy():
        if P.sha(P.F.read_json(POLICY)) != policy_sha256:
            raise RuntimeError("Recovery exception contract changed")
        if P.sha(Path(__file__).read_bytes()) != policy["code_sha256"]:
            raise RuntimeError("Recovery implementation changed after user-authorized freeze")
        if (len(policy['covered_unknown_records']) != 1 or
                any(r['stage'] != 51 for r in policy['covered_unknown_records']) or
                P.sha(P.F.read_json(P.F.STAGES[51]/'execution_manifest.json')) != policy['original_execution_manifest_sha256']):
            raise RuntimeError('Recovery exception is not bound to the original stage51 contract')
        for receipt in policy["covered_unknown_records"]:
            if P.sha(P.F.read_json(ROOT / receipt["path"])) != receipt["sha256"]:
                raise RuntimeError("Covered failed provider record changed")

    def upper_ledger():
        guard_policy()
        actual = X.ledger()
        unknown = []
        for stage in P.STAGES:
            for file in (P.F.STAGES[stage]/"api_cache").glob("*.json"):
                record = P.F.read_json(file)
                priced = P.PRICE.price_records([(str(stage),str(file.relative_to(ROOT)),record)])[0]
                if priced["cost_usd"] is None:
                    unknown.append(str(file.relative_to(ROOT)))
        approved = {r["path"] for r in policy["covered_unknown_records"]}
        if not set(unknown) <= approved:
            return actual
        debit = sum((Decimal(r["upper_usd"]) for r in policy["covered_unknown_records"] if r["path"] in unknown),Decimal(0))
        # This mapping is consumed ONLY inside the old reservation function.
        # Public X.ledger remains actual known cost + unknown count, unchanged.
        return {**actual,"unknown_cost_records":0,"known_cost_usd":str(Decimal(actual["known_cost_usd"])+debit)}

    def reserve(self, normalized):
        guard_policy()
        # Include the approved unknown debit in this stage's allocation as well.
        if self.stage in [51,510]:
            debit = sum((Decimal(r["upper_usd"]) for r in policy["covered_unknown_records"]),Decimal(0))
            records=[(str(stage),str(p),P.F.read_json(p)) for stage in [51,510]
                     for p in (P.F.STAGES[stage]/'api_cache').glob("*.json")]
            own=P.PRICE.aggregate(P.PRICE.price_records(records))
            bound=len(P.F.canonical(self.wire_kwargs(normalized)).encode())+8192
            amount=(Decimal(bound)*Decimal('.25' if bound>272000 else '.125')+
                    Decimal(normalized['max_output_tokens'])*Decimal('.75' if bound>272000 else '.50'))/1000000
            logical_cap=P.F.read_json(P.F.STAGES[51]/'execution_manifest.json')['stage_cost_cap_usd']
            if Decimal(own['known_cost_usd'])+debit+amount > Decimal(logical_cap):
                raise RuntimeError('Stage cap including unknown-request debit reached')
        original_ledger = P.ledger
        try:
            P.ledger = upper_ledger
            return prior_reserve(self,normalized)
        finally:
            P.ledger = original_ledger

    X.Cache.reserve = reserve
    X.http520_recovery_installed = True
    X.budget_upper_ledger = upper_ledger


def retry_once(X, *, allow_network=False):
    """Preserve the original failed repetition; save one diagnostic retry apart."""
    install(X)
    P=X.P
    old=P.F.read_json(P.F.STAGES[51]/"execution_manifest.json")
    questions=P.F.read_json(P.F.STAGES[51]/"questions.json")["queries"]
    q=next(q for q in questions if q["query_id"]=="H17")
    identity="H17:parser:r2"
    manifest=copy.deepcopy(old)
    manifest.update(stage=510,allowed_identities=dict(parser=[identity],answer=[]),
        maximum_parser_requests=1,maximum_answer_requests=0,maximum_embedding_requests=0,
        maximum_physical_requests=1,stage_cost_cap_usd=".02",purpose="one explicitly authorized HTTP520 diagnostic retry",
        charged_logical_stage=51,charged_logical_stage_cap_usd=old['stage_cost_cap_usd'],
        recovery_policy_sha256=P.sha(P.F.read_json(POLICY)))
    manifest["source_sha256"][str(Path(__file__).relative_to(ROOT))]=P.sha(Path(__file__).read_bytes())
    X.frozen(P.F.STAGES[510]/"execution_manifest.json",manifest)
    return X.extract(510,q,2,allow_network=allow_network)
