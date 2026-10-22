"""One explicitly approved DNS-failure reservation; frozen56 stays unchanged.

The notebook alone cannot amend a frozen accounting contract on resume. This
adapter adds the exact authorized unknown receipt only to reservation guards;
actual cost journals and the failed parser row remain untouched. No retry.
"""
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
POLICY=ROOT/'notebooks/data/56_structured_normalization/connection_recovery_policy.json'
FAILED_PATH='notebooks/data/56_structured_normalization/parser_old/api_cache/861b052461560be18c09c279c3252ead86b49cdf2cf0fb0958860b27b069c0d4.json'
FAILED_SHA256='04b98a78732798296fb5ccb93c2ed25b6fb4f52428fe6ade1f38030a91b53e73'


def with_reservations(actual, unknown_paths, allowed):
    if not set(unknown_paths)<=set(allowed) or len(unknown_paths)!=actual['unknown_cost_records']:
        return dict(actual)
    debit=sum((Decimal(allowed[path]) for path in unknown_paths),Decimal(0))
    return {**actual,'unknown_cost_records':0,'known_cost_usd':str(Decimal(actual['known_cost_usd'])+debit)}


def install(R):
    if getattr(R,'connection_recovery_installed',False):return
    policy=R.read(POLICY);policy_sha=R.P.sha(policy)
    old_policy=R.read(ROOT/'notebooks/data/51_query_extraction_scope/budget_recovery_contract.json')
    covered=[*old_policy['covered_unknown_records'],policy['covered_unknown_record']]
    allowed={item['path']:item['upper_usd'] for item in covered}
    original_ledger=R.X.ledger;original_phase=R.phase_price;previous_reserve=R.Cache.reserve

    def guard():
        new=policy['covered_unknown_record']
        if (R.P.sha(R.read(POLICY))!=policy_sha or
                R.P.sha(Path(__file__).read_bytes())!=policy['implementation_sha256'] or
                policy['authorization']!='user_explicit_2026_10_23_reserve_and_resume_remaining443' or
                policy['retry_failed_identity'] is not False or
                policy['remaining_provider_requests']!=443 or
                new['path']!=FAILED_PATH or new['sha256']!=FAILED_SHA256 or new['upper_usd']!='0.0031075'):
            raise RuntimeError('Connection exception differs from explicit authorization')
        for item in covered:
            if R.P.sha(R.read(ROOT/item['path']))!=item['sha256']:
                raise RuntimeError('Covered unknown request journal changed')

    def unknowns(stages):
        result=set()
        for stage in stages:
            for path in (R.P.F.STAGES[stage]/'api_cache').glob('*.json'):
                relative=str(path.relative_to(ROOT))
                row=R.P.PRICE.price_records([(str(stage),relative,R.read(path))])[0]
                if row['cost_usd'] is None:result.add(relative)
        return result

    def upper_ledger():
        guard()
        return with_reservations(original_ledger(),unknowns(R.P.STAGES),allowed)

    def upper_phase(stages):
        return with_reservations(original_phase(stages),unknowns(stages),allowed)

    def reserve(self, normalized):
        guard()
        # Legacy51 owns its own closure; temporarily give it the combined
        # approved upper ledger, leaving the public ledger actual at rest.
        previous_ledger,previous_phase=R.X.ledger,R.phase_price
        try:
            R.X.ledger=upper_ledger;R.phase_price=upper_phase
            return previous_reserve(self,normalized)
        finally:
            R.X.ledger=previous_ledger;R.phase_price=previous_phase

    guard()
    R.Cache.reserve=reserve
    R.X.budget_upper_ledger=upper_ledger
    R.connection_recovery_installed=True
