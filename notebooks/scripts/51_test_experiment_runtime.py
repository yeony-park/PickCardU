"""Offline request-budget and output-time aggregation boundary regressions."""
import importlib.util
from pathlib import Path
import sys
import pytest

spec = importlib.util.spec_from_file_location("runtime_test51", Path(__file__).with_name("51_experiment_runtime.py"))
X = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = X
spec.loader.exec_module(X)


def test_p95_nearest_rank_and_missing_times():
    s = X.timing_summary([None, *range(1, 21)])
    assert s == dict(n=20, mean=10.5, median=10.5, p95=19.0, maximum=20.0)
    assert X.timing_summary([None])["n"] == 0


def test_notebook_code_change_blocks_execution(tmp_path):
    import nbformat
    notebook = tmp_path / "contract.ipynb"
    n = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("answer=1")])
    nbformat.write(n, notebook)
    manifest = dict(notebook_code_sha256={str(notebook): X.notebook_code_hash(notebook)}, source_sha256={})
    X.guard_manifest(manifest)
    n.cells[0].source = "answer=2"
    nbformat.write(n, notebook)
    with pytest.raises(RuntimeError, match="Notebook code changed"):
        X.guard_manifest(manifest)


def test_unknown_prior_cost_blocks_new_parser(tmp_path, monkeypatch):
    api = object.__new__(X.Cache)
    api.stage, api.kind, api.identity, api.cache = 51, "parser", "T:parser:r1", tmp_path
    api.policy = "2400"
    question_input = [dict(role="user", content="커피 할인 카드")]
    api.manifest = dict(source_sha256={}, notebook_code_sha256={}, maximum_parser_requests=1,
        allowed_identities={"parser": [api.identity]}, maximum_physical_requests=1,
        allowed_request_by_identity={api.identity: X.P.sha(question_input)},
        parser_instructions_sha256=X.P.sha(X.Q.PROMPT), parser_schema_sha256=X.P.sha(X.Q.QueryPlan.model_json_schema()))
    monkeypatch.setattr(X.P, "ledger", lambda: dict(unknown_cost_records=1))
    kwargs = api.normalized(dict(model="gpt-6-luna", input=question_input,
        instructions=X.Q.PROMPT, text_format=X.Q.QueryPlan, max_output_tokens=3000))
    with pytest.raises(RuntimeError, match="Unknown prior request cost"):
        api.reserve(kwargs)


def test_identity_attempt_cap_blocks_extra_parser(tmp_path):
    import json
    api = object.__new__(X.Cache)
    api.stage, api.kind, api.identity, api.cache = 51, "parser", "T:parser:r1", tmp_path
    api.manifest = dict(source_sha256={}, notebook_code_sha256={}, maximum_parser_requests=10)
    (tmp_path / "existing.json").write_text(json.dumps(dict(request={"max_output_tokens":3000}, experiment_identity=api.identity)))
    with pytest.raises(RuntimeError, match="Identity attempt cap"):
        api.reserve(dict(model="gpt-6-luna", reasoning={"effort":"medium"}))


def test_recovery_debits_upper_bound_without_relabelling_actual_usage(monkeypatch,tmp_path):
    from decimal import Decimal
    import json,copy
    recovery = X.load('recovery_budget_test51','51_budget_recovery.py')
    # Isolate this ledger from the legitimately started live API journal.
    policy=copy.deepcopy(X.P.F.read_json(recovery.POLICY))
    failed=X.P.F.read_json(X.ROOT/policy['covered_unknown_records'][0]['path'])
    manifest=X.P.F.read_json(X.P.F.STAGES[51]/'execution_manifest.json')
    cache=tmp_path/'stage51/api_cache';cache.mkdir(parents=True)
    (cache/'failure.json').write_text(json.dumps(failed))
    (tmp_path/'stage51/execution_manifest.json').write_text(json.dumps(manifest))
    policy['covered_unknown_records'][0]['path']='stage51/api_cache/failure.json'
    policy_path=tmp_path/'policy.json';policy_path.write_text(json.dumps(policy))
    monkeypatch.setattr(recovery,'POLICY',policy_path);monkeypatch.setattr(recovery,'ROOT',tmp_path)
    monkeypatch.setattr(X,'ROOT',tmp_path);monkeypatch.setattr(X.P,'DATA',tmp_path)
    monkeypatch.setattr(X.P,'STAGES',{51:'stage51'})
    monkeypatch.setattr(X.P.F,'STAGES',{51:tmp_path/'stage51'})
    reserve = X.Cache.reserve
    try:
        recovery.install(X)
        actual = X.ledger()
        upper = X.budget_upper_ledger()
        assert actual['unknown_cost_records'] == 1
        assert upper['unknown_cost_records'] == 0
        assert Decimal(upper['known_cost_usd'])-Decimal(actual['known_cost_usd']) == Decimal('.003127')
        assert X.ledger() == actual
    finally:
        X.Cache.reserve = reserve
        X.http520_recovery_installed = False


def test_recovery_retry_is_also_charged_to_original_stage_cap(monkeypatch):
    recovery = X.load('recovery_logical_stage_test51','51_budget_recovery.py')
    reserve = X.Cache.reserve
    try:
        recovery.install(X)
        api = object.__new__(X.Cache)
        api.stage = 510
        api.wire_kwargs = lambda kwargs: {}
        api.manifest = {'stage_cost_cap_usd':'.02'}
        monkeypatch.setattr(X.P.PRICE,'aggregate',lambda rows:{'known_cost_usd':'.3999'})
        with pytest.raises(RuntimeError,match='Stage cap including unknown-request debit'):
            api.reserve({'max_output_tokens':3000})
    finally:
        X.Cache.reserve = reserve
        X.http520_recovery_installed = False
