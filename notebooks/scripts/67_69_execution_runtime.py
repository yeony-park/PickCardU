"""Shared bounded API journals for experiments67--69, never an HTML runner.

Notebooks own the matrices, selection and semantic judgments. This helper keeps
raw-before-parse requests, fresh repeat identities, two physical attempts and the
same8 USD ledger across interruption/resume. Provider requests stay serial.
"""
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


K = load('query67_for_execution', '67_query_repair.py')
R = K.load_runtime()
E = load('evaluation66_for_execution', '66_visible_answer_evaluation.py')
LEAVES = {670:'67_structured_retrieval_repair/parser_old',671:'67_structured_retrieval_repair/parser_new',
          68:'68_structured_evidence_generation',680:'68_structured_evidence_generation/parser_extra',
          69:'69_repaired_sequential_confirmation',690:'69_repaired_sequential_confirmation/parser_old',
          691:'69_repaired_sequential_confirmation/parser_new'}
def stage_registries():
    """Include the actual cache-method globals, not just import aliases."""
    modules = [R.P.F, R.P.A.F, R.Q.P.F, R.Q.P.A.F]
    for cls in R.X.Cache.__mro__:
        for name in ('parse', 'reserve', 'normalized'):
            method = cls.__dict__.get(name)
            common = method.__globals__.get('F') if method else None
            if common is not None and all(common is not old for old in modules):
                modules.append(common)
    return modules


for number, leaf in LEAVES.items():
    R.P.STAGES[number] = R.X.STAGES[number] = leaf
    for shared in stage_registries():
        shared.STAGES[number] = R.P.DATA / leaf
for shared in stage_registries():
    # Shared metadata lives above parser_old/parser_new, not in the paid ledger.
    shared.STAGES[67] = R.P.DATA / '67_structured_retrieval_repair'
OLD_PARSER_PROMPT = R.R.NEW_PROMPT
NEW_PARSER_PROMPT = OLD_PARSER_PROMPT + '''
혜택 대상의 입도에 관한 추가 지침:
- targets는 원질문에 실제 있는 업종·서비스·가맹점의 핵심 명사 또는 명시된 부분어를 후보 검색용으로 적습니다.
- 비용 설명을 통째로 붙이지 마세요. 치과 진료비는 치과, 헬스장 회비는 헬스장, 병원비는 병원처럼 대상 자체를 남깁니다. 진료비·회비·표값만 남겨 대상 업종을 잃어도 안 됩니다.
- 반려동물 진료비는 동물처럼 질문에 포함된 명시 범주로 후보를 확보할 수 있습니다. 동물원·용품과 진료비의 관계가 같다는 뜻은 아니며 원질문은 후속 검색·답변에 그대로 남습니다.
- 인터넷 서점은 서점, 음식 배달은 배달처럼 명시 핵심어로 후보를 확보할 수 있습니다. 결제경로와 적용 관계를 새 단어로 지어내지 않습니다.
- 넓은 후보가 필요해도 숫자·행위·제외·동일카드 AND를 임의로 없애지 마세요. 범용 질문의 카드·지출·소비를 억지 혜택 WHERE로 만들지 마세요.
- 기차→철도, 극장→영화와 같은 새 동의어 교체나 오타 보정은 하지 않습니다.
'''
DIRECT_PROMPT = '''
추가 답변 우선순위:
1. 원질문이 직접 요구한 추천·비교·계산·조건을 화면 본문과 카드별 추천 이유에서 먼저 해결하세요. 숨은 claims에만 정답을 넣지 마세요.
2. 묻지 않은 조건을 전부 나열하는 것보다 질문에 필요한 정확한 답을 우선하세요. 물어보지 않은 조건을 생략했다고 무조건 적용된다고 표현하지 마세요.
3. 추가로 말하는 수치·실적·한도·횟수·제외는 같은 카드의 같은 혜택에 연결된 근거만 사용하세요. 다른 혜택의 숫자나 예시를 옮기지 마세요.
4. 카드별로 조건을 구분하고, 입력에서 적용 관계가 확인되지 않으면 확인되지 않는 범위를 정확히 한정하세요. 포인트·캐시백·청구할인을 임의 환산하지 마세요.
'''


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    R.P.F.write_json(Path(path), value)


def transform_prompt(prompt, variant):
    if variant == 'old':
        return prompt
    if variant != 'direct':
        raise ValueError('Unknown answer prompt variant')
    return prompt if prompt.endswith(DIRECT_PROMPT) else prompt + DIRECT_PROMPT


def attempt_identity(identity, attempt):
    if attempt not in (1,2):
        raise ValueError('At most two physical attempts')
    return identity if attempt == 1 else identity + ':retry2'


def phase_code_sha(path, prefix):
    cells = read(path)['cells']
    return R.P.sha([(c['id'], ''.join(c['source'])) for c in cells
                   if c['cell_type'] == 'code' and c['id'].startswith(prefix)])


def guard_manifest(manifest):
    E.verify_file_map(manifest['source_sha256'], ROOT)
    if manifest.get('critical_data_sha256'):
        E.verify_file_map(manifest['critical_data_sha256'], ROOT)
    for name, value in manifest.get('notebook_phase_code_sha256', {}).items():
        if phase_code_sha(ROOT/name,value['prefix']) != value['sha256']:
            raise RuntimeError('Notebook phase code changed: '+name)


def freeze_historical_budget():
    """Freeze old journals once; each call rechecks their bytes and membership."""
    path = ROOT/'notebooks/data/67_structured_retrieval_repair/shared_budget_baseline.json'
    if path.exists():
        return read(path)
    if any(list((R.P.F.STAGES[s]/'api_cache').glob('*.json')) for s in LEAVES):
        raise RuntimeError('New requests exist before the historical budget freeze')
    dirs = {str((R.P.F.STAGES[s]/'api_cache').relative_to(ROOT)) for s in R.P.STAGES if s not in LEAVES}
    members = {name: sorted(str(p.relative_to(ROOT)) for p in (ROOT/name).glob('*.json')) for name in sorted(dirs)}
    files = {name:E.file_digest(ROOT/name) for values in members.values() for name in values}
    actual, upper = R.X.ledger(), R.X.budget_upper_ledger()
    if upper['unknown_cost_records'] or not files:
        raise RuntimeError('Historical budget contains an unapproved unknown cost')
    value = dict(status='frozen_historical_journals_and_approved_reservations',files_sha256=files,
        directory_members=members,actual_ledger=actual,historical_budget_debit_usd=upper['known_cost_usd'],
        historical_reserved_usd=str(Decimal(upper['known_cost_usd'])-Decimal(actual['known_cost_usd'])),
        global_cap_usd='8',new_unknown_cost_policy='stop',new_provider_requests_at_freeze=0)
    R.X.frozen(path,value)
    return value


def budget_ledger():
    baseline = read(ROOT/'notebooks/data/67_structured_retrieval_repair/shared_budget_baseline.json')
    E.verify_file_map(baseline['files_sha256'],ROOT)
    for name, expected in baseline['directory_members'].items():
        if sorted(str(p.relative_to(ROOT)) for p in (ROOT/name).glob('*.json')) != expected:
            raise RuntimeError('Historical cost journal membership changed: '+name)
    records = [(str(s),str(p.relative_to(ROOT)),read(p)) for s in LEAVES
               for p in sorted((R.P.F.STAGES[s]/'api_cache').glob('*.json'))]
    priced = R.P.PRICE.aggregate(R.P.PRICE.price_records(records))
    debit = Decimal(baseline['historical_budget_debit_usd'])+Decimal(priced['known_cost_usd'])
    return {**priced,'known_cost_usd':str(debit),'budget_debit_usd':str(debit),
        'new_known_API_cost_usd':priced['known_cost_usd'],
        'historical_reserved_usd':baseline['historical_reserved_usd'],
        'note':'known_cost_usd is the legacy guard compatibility field and includes approved historical reservations; not billed invoice'}


class Cache(R.X.Cache):
    def normalized(self, kwargs):
        result = super().normalized(kwargs)
        if self.kind == 'answer':
            variant = self.manifest['prompt_variant_by_identity'][self.identity]
            result['instructions'] = transform_prompt(result['instructions'],variant)
        return result

    def check_contract(self, kwargs):
        guard_manifest(self.manifest)
        value = self.normalized(kwargs)
        if value['model'] != 'gpt-6-luna' or value['reasoning'] != {'effort':'medium'}:
            raise RuntimeError('Fixed model/reasoning changed')
        if self.identity not in self.manifest['allowed_identities'][self.kind]:
            raise RuntimeError('Identity outside the frozen matrix')
        if self.kind == 'parser':
            expected = self.manifest['allowed_request_by_identity'][self.identity]
            prompt = self.manifest['parser_instructions_sha256']
            schema = self.manifest['parser_schema_sha256']
            if R.P.sha(value['instructions']) != prompt:
                raise RuntimeError('Parser prompt changed')
        else:
            contract = read(R.P.F.STAGES[self.stage]/'answer_payload_contract.json')
            if contract['execution_manifest_sha256'] != R.P.sha(self.manifest):
                raise RuntimeError('Answer payload contract changed')
            expected = contract['input_sha256_by_identity'][self.identity]
            schema = contract['schema_sha256_by_identity'][self.identity]
            if R.P.sha(value['instructions']) not in self.manifest['answer_instructions_sha256_by_identity'][self.identity]:
                raise RuntimeError('Answer prompt changed')
        if R.P.sha(value['input']) != expected or R.P.sha(value['text_format'].model_json_schema()) != schema:
            raise RuntimeError('Frozen request payload/schema changed')
        return value

    def reserve(self, normalized):
        self.check_contract(normalized)
        records = [read(p) for p in self.cache.glob('*.json') if not p.name.startswith('embedding_batch_')]
        token_limit = 3000 if self.kind == 'parser' else 2400
        if sum(r.get('request',{}).get('max_output_tokens') == token_limit for r in records) >= self.manifest['maximum_'+self.kind+'_requests']:
            raise RuntimeError('Request-kind physical cap reached')
        mapping = self.manifest.get('logical_identity_by_physical',{})
        logical = mapping.get(self.identity,self.identity)
        if sum(mapping.get(r.get('experiment_identity'),r.get('experiment_identity')) == logical for r in records) >= 2:
            raise RuntimeError('Logical identity physical cap reached')
        # Same approved old two reservations; new unknown usage still stops.
        # No concurrent API calls while this legacy ledger binding is active.
        legacy = R.X.R.P
        previous = legacy.ledger
        try:
            legacy.ledger = budget_ledger
            return R.X.R.BoundCache.reserve(self,normalized)
        finally:
            legacy.ledger = previous

    def parse(self, **kwargs):
        self.check_contract(kwargs)
        return super().parse(**kwargs)


def parser_kwargs(question, prompt):
    return dict(model='gpt-6-luna',instructions=prompt,
        input=[dict(role='user',content=question['question'])],text_format=R.Q.QueryPlan,
        tools=[],store=False,max_output_tokens=3000)


def validate_parser_checkpoint(row, question, manifest, base):
    if row['question_sha256'] != R.P.sha(question['question']) or row['manifest_sha256'] != R.P.sha(manifest):
        raise RuntimeError('Saved parser checkpoint question/manifest changed')
    for key, physical in zip(row['request_keys'],row['physical_identities'],strict=True):
        raw = read(base/'api_cache'/(key+'.json'));request=raw['request']
        if (raw.get('experiment_identity') != physical
                or R.P.sha(request['input']) != manifest['allowed_request_by_identity'][physical]
                or R.P.sha(request['instructions']) != manifest['parser_instructions_sha256']
                or R.P.sha(request['schema']) != manifest['parser_schema_sha256']):
            raise RuntimeError('Saved parser checkpoint differs from physical request')
    if row.get('complete'):
        if R.P.sha(row) != read(base/'plans'/(row['identity']+'.seal.json'))['row_sha256']:
            raise RuntimeError('Saved parser checkpoint seal changed')
        if row['status']=='ok':
            last=read(base/'api_cache'/(row['request_keys'][-1]+'.json'))
            if last['status']!='ok' or last['parsed']!=row['plan']:
                raise RuntimeError('Saved parser plan differs from raw response')


def extract_query(stage, question, repeat, *, allow_network=False):
    base=R.P.F.STAGES[stage];manifest=read(base/'execution_manifest.json');guard_manifest(manifest)
    logical=f"{question['query_id']}:parser:r{repeat}";target=base/'plans'/(logical+'.json')
    prior=read(target) if target.exists() else None
    if prior:
        validate_parser_checkpoint(prior,question,manifest,base)
        if budget_ledger()['unknown_cost_records']:
            raise RuntimeError('Unknown new API usage; stop before another request')
        if prior.get('complete'):
            return prior
    prompt=OLD_PARSER_PROMPT if manifest['parser_variant']=='old' else NEW_PARSER_PROMPT
    kwargs=parser_kwargs(question,prompt)
    keys=list(prior['request_keys']) if prior else [];physical_ids=list(prior['physical_identities']) if prior else []
    events=list(prior['events']) if prior else [];previous_wall=prior.get('parser_ready_wall_seconds',0) if prior else 0
    started=time.perf_counter();cpu=time.process_time()
    for attempt in range(len(keys)+1,3):
        physical=attempt_identity(logical,attempt);api=Cache(stage,'parser',physical,allow_network=allow_network)
        value=error=None
        try:
            response=api.parse(**kwargs)
            value=response.output_parsed.model_dump(mode='json') if response.output_parsed else None
        except Exception as exc:
            if not api.events:
                raise
            error=f'{type(exc).__name__}: {exc}'
        key=api.key(kwargs);keys.append(key);physical_ids.append(physical);events.extend(api.events)
        row=dict(identity=logical,query_id=question['query_id'],question=question['question'],repeat=repeat,
            question_sha256=R.P.sha(question['question']),manifest_sha256=R.P.sha(manifest),
            status='ok' if value is not None else 'failed',plan=value,error=error,
            request_keys=keys,physical_identities=physical_ids,events=events,
            complete=value is not None or attempt==2,parser_ready_wall_seconds=previous_wall+time.perf_counter()-started,
            parser_segment_cpu_seconds=time.process_time()-cpu,resumed_partial=bool(prior),new_semantic_retry=False)
        write(target,row)
        if budget_ledger()['unknown_cost_records']:
            raise RuntimeError('Unknown new API usage; stop before another request')
        if row['complete']:
            R.X.frozen(target.with_suffix('.seal.json'),dict(row_sha256=R.P.sha(row)))
            return row
    raise RuntimeError('Incomplete parser attempts need inspection')
