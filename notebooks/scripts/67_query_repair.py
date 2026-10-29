"""Experiment67: auditable literal candidate recovery over the frozen SQL backend.

The notebook owns the comparison and selected setting. This reusable helper is
also used by68/69 so their matcher cannot silently differ from the audited one.
It changes neither stored plans, DB values, numeric predicates, nor exclusions.
"""
import copy
from functools import lru_cache
import importlib.util
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'literal_candidate_cost_noun_projection67_v1'


@lru_cache(maxsize=1)
def load_runtime():
    name = 'preserved65_for67'
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name('63_65_experiment_runtime.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    scope_spec = importlib.util.spec_from_file_location('preserved_scope65_for67', Path(__file__).with_name('65_scope_repair.py'))
    scope = importlib.util.module_from_spec(scope_spec)
    scope_spec.loader.exec_module(scope)
    scope.install(module)
    return module


def target_candidates(target: str) -> list[str]:
    """Keep the literal target; optionally remove only 진료비/회비 after a noun.

    This is deliberately a narrow development candidate rule. It does not infer
    synonyms, correct typos, strip channel nouns, or collapse 전기요금 to 전기.
    Original questions still drive the subsequent scoped unstructured search.
    """
    result = [target]
    text = unicodedata.normalize('NFKC', target).strip()
    match = re.fullmatch(r'(.+?)\s*(?:진료비|회비)', text)
    if match:
        prefix = match[1].strip()
        if len(re.sub(r'\s+', '', prefix)) >= 2 and prefix not in result:
            result.append(prefix)
    return result


def compile_requirement(requirement: dict) -> dict:
    result = copy.deepcopy(requirement)
    result['targets'] = list(dict.fromkeys(candidate for target in requirement['targets']
                                         for candidate in target_candidates(target)))
    return result


def query(runtime, plan, scope, directory, facts, *, matcher='baseline'):
    """Run bound SQL with the same-card AND and hard filters of the old backend."""
    if matcher not in {'baseline', 'projected'}:
        raise ValueError('Unknown67 matcher')
    if matcher == 'baseline':
        return runtime.M.K.query(runtime.Q, plan, scope, directory, facts, repaired=True)
    proxy = SimpleNamespace(QueryPlan=runtime.Q.QueryPlan,
        evaluate_fact=lambda fact, requirement: runtime.M.K.evaluate_fact(
            runtime.Q, fact, compile_requirement(requirement)))
    result = runtime.M.K.query(proxy, plan, scope, directory, facts, repaired=False)
    result['matcher_version'] = VERSION
    result['target_projection'] = [{'original': target, 'candidate_targets': target_candidates(target)}
        for group in plan['groups'] for requirement in group for target in requirement['targets']
        if len(target_candidates(target)) > 1]
    return result


def route(runtime, plan, original, directory, facts, *, matcher, parser_failed=False):
    base = dict(route='U', scope=original['card_keys'], eligible_cards=[], matching_fact_ids={}, db_attempted=False)
    if original['clarify']:
        return {**base, 'route': 'clarify', 'reason': original['reason']}
    if parser_failed:
        return {**base, 'reason': 'parser_failure'}
    result = query(runtime, plan, original['card_keys'], directory, facts, matcher=matcher)
    cards = result['eligible_cards']
    return {**base, **result, 'route': 'SEQ' if cards else 'U', 'scope': cards or original['card_keys'],
            'reason': 'matched' if cards else 'no_results'}
