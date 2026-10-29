"""Faithful cached answer retries and measured output-budget comparisons."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from pydantic import ValidationError

spec = importlib.util.spec_from_file_location('answer_followup_common', Path(__file__).with_name('34_followup_common.py'))
F = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = F
spec.loader.exec_module(F)


def eof_error():
    return ValidationError.from_exception_data('CachedJSONFailure', [
        {'type': 'json_invalid', 'loc': (), 'input': None,
         'ctx': {'error': 'EOF while parsing a value: cached validation failure'}}])


class ResponseCache:
    """Exact-payload readthrough; raw status/usage persisted before SDK parsing."""
    def __init__(self, *, cache=None, old_cache=None, policy='2400', allow_network=True,
                 revalidate_failed=False):
        if policy not in ['2400', '4800', 'adaptive', 'legacy']:
            raise ValueError('Unknown answer budget policy')
        self.cache = Path(cache or F.STAGES[35] / 'api_cache')
        self.old_cache = Path(old_cache or F.OLD[30] / 'api_cache')
        self.cache.mkdir(parents=True, exist_ok=True)
        self.responses = self
        self.policy = policy
        self.allow_network = allow_network
        self.revalidate_failed = revalidate_failed
        self.token_truncated = False
        self.events = []
        self.client = None

    read_fixture = staticmethod(F.read_json)
    write_fixture = staticmethod(F.write_json)

    def normalized(self, kwargs):
        result = dict(kwargs)
        result.update(model=F.MODEL, reasoning={'effort': F.REASONING}, timeout=120, store=False)
        result['max_output_tokens'] = 4800 if self.policy == '4800' or (
            self.policy == 'adaptive' and self.token_truncated) else 2400
        return result

    def key(self, kwargs):
        normalized = self.normalized(kwargs)
        request = {k: v for k, v in normalized.items() if k not in ['text_format', 'timeout']}
        request['schema'] = normalized['text_format'].model_json_schema()
        return F.digest(request)

    def wire_kwargs(self, kwargs):
        from openai.lib._parsing._responses import type_to_text_format_param
        result = self.normalized(kwargs)
        schema = result.pop('text_format')
        result['text'] = {'format': type_to_text_format_param(schema)}
        return result

    def fetch(self, kwargs):
        if not self.allow_network:
            raise RuntimeError('Offline cache replay forbids provider calls')
        if self.client is None:
            from dotenv import load_dotenv
            from openai import OpenAI
            import os
            load_dotenv(F.ROOT / '.env', override=False)
            if not os.getenv('OPENAI_API_KEY'):
                raise RuntimeError('OPENAI_API_KEY unavailable')
            self.client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], max_retries=0, timeout=120)
        # Same SDK text-format conversion and parse_response helper as parse(),
        # separated solely so raw output survives a parsing ValidationError.
        return self.client.responses.create(**self.wire_kwargs(kwargs))

    @staticmethod
    def parse_raw(response, schema):
        from openai.lib._parsing._responses import parse_response
        return parse_response(text_format=schema, input_tools=[], response=response)

    @staticmethod
    def failure_reason(raw):
        details = raw.get('incomplete_details') or {}
        if raw.get('status') == 'incomplete':
            return details.get('reason') or 'incomplete_unknown'
        if any(content.get('type') == 'refusal' for item in raw.get('output', [])
               for content in item.get('content', [])):
            return 'refusal'
        return None

    def deliver(self, stored, schema):
        raw = stored.get('raw_response') or {}
        reason = self.failure_reason(raw)
        self.token_truncated = reason == 'max_output_tokens'
        if stored['status'] == 'ok':
            return SimpleNamespace(output_parsed=schema.model_validate(stored['parsed']), usage=stored.get('usage', {}))
        if self.policy == 'legacy':
            raise RuntimeError('Previous API failure: ' + stored.get('error', ''))
        if raw:
            from openai.types.responses import Response
            response = self.parse_raw(Response.model_validate(raw), schema)
            if self.policy == 'adaptive' and self.token_truncated and response.output_parsed is None:
                # Explicit experimental handling of CONFIRMED truncation only;
                # the unchanged service then uses its original second prompt.
                raise eof_error()
            return response
        error = stored.get('error', '')
        if error.startswith('ValidationError:') and 'EOF while parsing' in error:
            raise eof_error()
        if error == 'RuntimeError: Refused/incomplete/empty structured response':
            return SimpleNamespace(output_parsed=None, usage={})
        raise RuntimeError('Cached provider failure without reconstructable raw response: ' + error)

    def parse(self, **kwargs):
        normalized = self.normalized(kwargs)
        schema = normalized['text_format']
        key = self.key(kwargs)
        path, old = self.cache / (key + '.json'), self.old_cache / (key + '.json')
        found = (old if old.exists() else path) if self.policy == 'legacy' else (path if path.exists() else old)
        started = time.perf_counter()
        revalidation = False
        if found.exists():
            stored = F.read_json(found)
            if stored['status'] == 'started':
                raise RuntimeError('Interrupted provider request needs explicit recovery')
            revalidation = (self.revalidate_failed and self.policy != 'legacy' and found == old
                            and stored['status'] == 'failed' and not stored.get('raw_response'))
            if not revalidation:
                event = {'key': key, 'cache_hit': True, 'cache_origin': 'prior30' if found == old else 'followup35',
                         'status': stored['status'], 'usage': stored.get('usage', {}),
                         'failure_reason': self.failure_reason(stored.get('raw_response') or {}),
                         'max_output_tokens': normalized['max_output_tokens']}
                self.events.append(event)
                return self.deliver(stored, schema)
        if not self.allow_network:
            raise RuntimeError('Offline replay cache miss')
        request = {k: v for k, v in normalized.items() if k not in ['text_format', 'timeout']}
        request['schema'] = schema.model_json_schema()
        record = {'status': 'started', 'request': request, 'started_at': time.time(),
                  'revalidated_legacy_failure': revalidation}
        F.write_json(path, record)
        event = {'key': key, 'cache_hit': False, 'cache_origin': 'provider',
                 'max_output_tokens': normalized['max_output_tokens'], 'usage': {},
                 'revalidated_legacy_failure': revalidation}
        self.events.append(event)
        try:
            response = self.fetch(normalized)
            raw = response.model_dump(mode='json')
            record.update(raw_response=raw, usage=raw.get('usage') or {},
                          failure_reason=self.failure_reason(raw))
            self.token_truncated = record['failure_reason'] == 'max_output_tokens'
            # Persist BEFORE parsing, including usage of failed JSON output.
            F.write_json(path, record)
            parsed = self.parse_raw(response, schema)
            if self.policy == 'adaptive' and self.token_truncated and parsed.output_parsed is None:
                raise eof_error()
            if parsed.output_parsed is None:
                record.update(status='failed', error='RuntimeError: Refused/incomplete/empty structured response')
            else:
                record.update(status='ok', parsed=parsed.output_parsed.model_dump(mode='json'))
            record.update(wall_seconds=time.perf_counter() - started, finished_at=time.time())
            F.write_json(path, record)
            event.update(status=record['status'], usage=record['usage'], failure_reason=record['failure_reason'],
                         wall_seconds=record['wall_seconds'])
            return parsed
        except Exception as exc:
            record.update(status='failed', error=f'{type(exc).__name__}: {exc}',
                          wall_seconds=time.perf_counter() - started, finished_at=time.time())
            F.write_json(path, record)
            event.update(status='failed', usage=record.get('usage', {}),
                         failure_reason=record.get('failure_reason'), wall_seconds=record['wall_seconds'])
            raise


def load_tools():
    E = F.load_module('prior_answer_comparison', F.ROOT / 'notebooks/scripts/32_search_experiment.py')
    scoring = F.load_module('prior_answer_scoring', F.ROOT / 'notebooks/scripts/32_score_answers.py')
    F.initialize(35)
    return E, scoring


def answer_row(case, retrieval, *, policy, E, scoring, allow_network=True,
               revalidate_failed=True, cache=None):
    from pickcardu_rag.answering import OpenAIService
    api = ResponseCache(policy=policy, allow_network=allow_network,
                        revalidate_failed=revalidate_failed, cache=cache)
    provider = OpenAIService(api_key=None, client=api, llm_model=F.MODEL, answer_payload_bytes=64000)
    started = time.perf_counter()
    try:
        if retrieval['status'] != 'ok':
            raise RuntimeError('Retrieval failed')
        evidence = retrieval['result']['evidence']
        if evidence:
            parsed, usage = provider.answer(case['question'], evidence)
            answer = parsed.model_dump(mode='json')
            usage['original_service_max_output_tokens_per_attempt'] = usage.pop('max_output_tokens_per_attempt', 2400)
            usage['actual_attempt_budgets'] = [event['max_output_tokens'] for event in api.events]
        else:
            answer = {'answer_status': 'insufficient_evidence', 'answer_text': '검색된 근거가 없습니다.',
                      'recommendations': [], 'claims': []}
            usage = {'provider_called': False}
        ownership = int(E.citation_ownership(answer, evidence))
        recommended = {r['card_key'] for r in answer['recommendations']}
        row = {'status': 'ok', 'answer': answer, 'usage': usage,
               'metrics': {'citation_ownership': ownership,
                           'answer_anchor_hit': int(bool(recommended & set(case['gold_cards']))) if case['gold_cards'] else None,
                           'negative_abstention': int(answer['answer_status'] == 'insufficient_evidence') if case['negative_control'] else None,
                           'insufficient': int(answer['answer_status'] == 'insufficient_evidence')}}
    except Exception as exc:
        row = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}
    row.update(query_id=case['query_id'], profile=retrieval['profile'], mode=retrieval['mode'],
               budget_policy=policy, events=api.events, split=case['split'], subgroup=case['subgroup'],
               wall_seconds=time.perf_counter() - started,
               retrieval_sha256=F.digest(retrieval), model=F.MODEL, reasoning=F.REASONING)
    row['scores'] = scoring.score_row(case, row, retrieval)
    return row


def inputs():
    freeze = F.read_json(F.OLD[32] / 'dataset_freeze.json')
    bundle = F.read_json(F.STAGES[34] / 'retrieval_results.json')
    selected = bundle['selection']['selected_policy']
    benefit = [r for r in F.read_json(F.OLD[33] / 'retrieval_results.json')['records']
               if r['profile'] == 'card_page_section_benefit']
    parent = [r for r in bundle['records'] if r['policy'] == selected]
    rows = sorted(benefit + parent, key=lambda r: (r['query_id'], r['profile'], r['mode']))
    protocol = {'version': 'faithful-raw-status-answer-budget-v1',
        'bundle_selection_sha256': bundle['selection']['sha256'], 'dataset_sha256': freeze['sha256'],
        'retrieval_sha256': F.digest(rows), 'budget_policies': ['2400', 'adaptive', '4800'],
        'model': F.MODEL, 'reasoning': F.REASONING,
        'cached_failure_replay': 'First replay existing failed answer rows offline at2400 using preserved ValidationError; historical primary unchanged.',
        'fresh_failure_revalidation': 'Raw-less failed2400 payloads are called once at same2400 in new35 cache. Fixed2400 and adaptive reuse that SAME raw first response. Fixed4800 is a DIFFERENT exact request/first response, reused only if its own exact cache exists. New2400 success may reflect response variability, not budget gain. Historical successes reused by exact payload; single-run fixed4800 comparison includes response variability and is not a pure causal budget estimate.',
        'adaptive_rule': 'Only API raw status incomplete+incomplete_details.reason=max_output_tokens allows4800 at original second prompt. Unknown EOF/refusal/content_filter/network do not raise budget. Confirmed empty truncated output maps to validation retry in experimental proxy only.',
        'selection_rule': 'Development20x6: no loss of baseline known-positive pass question/profile/mode, no additional unsupported-percent claim, maximize answer successes, then minimum logical total_tokens over all attempts; tie2400,adaptive,4800. Previously seen13 evaluated AFTER selection, not independent holdout.',
        'code': {name: F.digest((F.ROOT / 'notebooks/scripts' / name).read_bytes())
                 for name in ['35_answer_experiment.py', '34_followup_common.py']},
        'original_answer_sha256': F.digest((F.ROOT / 'packages/rag-core/src/pickcardu_rag/answering.py').read_bytes()),
        'original_score_sha256': F.digest((F.ROOT / 'notebooks/scripts/32_score_answers.py').read_bytes()),
        'openai_sdk': '3.28.0',
        'official_source': 'https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses',
        'production_adopted': False,
    }
    protocol['sha256'] = F.digest(protocol)
    path = F.STAGES[35] / 'protocol.json'
    if path.exists() and F.read_json(path) != protocol:
        raise RuntimeError('Answer protocol changed after freeze')
    F.write_json(path, protocol)
    return freeze, rows, protocol


def offline_replay(cases, E, scoring):
    primary = {(r['query_id'], r['profile'], r['mode']): r for r in
               F.read_json(F.OLD[33] / 'answer_results.json')['records']}
    records = []
    for retrieval in F.read_json(F.OLD[33] / 'retrieval_results.json')['records']:
        key = (retrieval['query_id'], retrieval['profile'], retrieval['mode'])
        if primary[key]['status'] == 'ok' or retrieval['status'] != 'ok':
            continue
        record = answer_row(cases[key[0]], retrieval, policy='2400', E=E, scoring=scoring,
                            allow_network=False, revalidate_failed=False,
                            cache=F.STAGES[35] / 'offline_api_cache')
        record['historical_primary_status'] = primary[key]['status']
        records.append(record)
    out = {'records': records, 'new_provider_requests': 0,
           'note': 'Only historical failed answers with successful retrieval; same2400, original retry prompt and exact prior cache.'}
    F.write_json(F.STAGES[35] / 'offline_retry_replay.json', out)
    return out


def run_grid(retrievals, cases, E, scoring, protocol):
    outputs = []
    for retrieval in retrievals:
        case = cases[retrieval['query_id']]
        for policy in protocol['budget_policies']:
            key = f'{case["query_id"]}_{retrieval["profile"]}_{retrieval["mode"]}_{policy}'
            path = F.STAGES[35] / 'answers' / (key + '.json')
            if path.exists():
                record = F.read_json(path)
                if record['protocol_sha256'] != protocol['sha256']:
                    raise RuntimeError('Stale output-budget result')
            else:
                record = answer_row(case, retrieval, policy=policy, E=E, scoring=scoring)
                record['protocol_sha256'] = protocol['sha256']
                F.write_json(path, record)
                print('answer budget', key, record['status'],
                      sum(not e['cache_hit'] for e in record['events']), round(record['wall_seconds'], 2), flush=True)
            outputs.append(record)
    return outputs


def select_budget(rows, protocol):
    policies = protocol['budget_policies']
    baseline = {(r['query_id'], r['profile'], r['mode']): r for r in rows if r['budget_policy'] == '2400'}
    candidates = []
    for policy in policies:
        subset = [r for r in rows if r['budget_policy'] == policy]
        losses = [f'{r["query_id"]}:{r["profile"]}:{r["mode"]}' for r in subset
                  if baseline[(r['query_id'], r['profile'], r['mode'])]['scores'].get('known_positive_complete') == 1
                  and r['scores'].get('known_positive_complete') != 1]
        unsupported = sum(r['scores'].get('unsupported_percent_claims', 0) for r in subset)
        base_unsupported = sum(r['scores'].get('unsupported_percent_claims', 0) for r in baseline.values())
        total = sum(e.get('usage', {}).get('total_tokens', 0) for r in subset for e in r['events'])
        unknown_usage = sum(not e.get('usage') for r in subset for e in r['events'])
        candidates.append({'budget_policy': policy, 'successes': sum(r['status'] == 'ok' for r in subset),
                           'known_positive_losses': losses, 'unsupported_percent_claims': unsupported,
                           'baseline_unsupported': base_unsupported, 'logical_known_total_tokens': total,
                           'unknown_usage_attempts': unknown_usage,
                           'passes': not losses and unsupported <= base_unsupported})
    eligible = [r for r in candidates if r['passes']]
    # Missing historical failure usage is a limitation, not an estimated cost.
    order = {'2400': 0, 'adaptive': 1, '4800': 2}
    eligible.sort(key=lambda r: (-r['successes'], r['logical_known_total_tokens'], order[r['budget_policy']]))
    result = {'selected_budget_policy': eligible[0]['budget_policy'], 'candidates': candidates,
              'protocol_sha256': protocol['sha256'], 'selection_split': 'development',
              'selected_before_remaining13': True, 'production_adopted': False,
              'resource_limit': 'Unknown historical failure usage prevents an exact cross-policy total cost comparison; tokens are known subtotal.'}
    result['sha256'] = F.digest(result)
    path = F.STAGES[35] / 'selection.json'
    if path.exists() and F.read_json(path) != result:
        raise RuntimeError('Answer budget selection changed after freeze')
    F.write_json(path, result)
    print('selected answer budget', F.canonical(result), flush=True)
    return result


def run(stage='all'):
    E, scoring = load_tools()
    freeze, retrievals, protocol = inputs()
    cases = {case['query_id']: case for case in freeze['queries']}
    offline_replay(cases, E, scoring)
    if stage == 'offline':
        print(F.verify_preservation(35), flush=True)
        return
    started = time.perf_counter()
    development = [r for r in retrievals if cases[r['query_id']]['split'] == 'development']
    remaining = [r for r in retrievals if cases[r['query_id']]['split'] != 'development']
    rows = run_grid(development, cases, E, scoring, protocol)
    selection = select_budget(rows, protocol)
    rows.extend(run_grid(remaining, cases, E, scoring, protocol))
    result = {'records': rows, 'selection': selection, 'protocol_sha256': protocol['sha256'],
              'wall_seconds': time.perf_counter() - started, 'logical_rows': len(rows),
              'new_provider_requests': len({e['key'] for r in rows for e in r['events'] if not e['cache_hit']}),
              'scope': '20 developmentx6x3 policies then frozen selection then13 previously seen verification questions; single cache-shared response per exact request',
              'billed_cost': '미측정'}
    F.write_json(F.STAGES[35] / 'answer_results.json', result)
    print('answer preservation', F.canonical(F.verify_preservation(35)), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['offline', 'all'], default='all', nargs='?')
    run(parser.parse_args().stage)
