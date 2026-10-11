"""Catch after-freeze relabeling even when the attacker recomputes digests."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('binding42_test', Path(__file__).with_name('42_input_binding.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def save(path, value):
    result = {**value, 'sha256': digest(value)}
    path.write_text(json.dumps(result,sort_keys=True))
    return result

def fixture(path):
    protocol = save(path/'protocol.json', {'sources': {}})
    catalog = save(path/'catalog.json', {'catalog': {'issuer/card': 'Example'}, 'protocol_sha256': protocol['sha256']})
    questions = save(path/'questions.json', {'queries':[{'query_id':'E01','question':'coffee'}],
                      'catalog_sha256': catalog['sha256'], 'protocol_sha256': protocol['sha256']})
    dataset = save(path/'dataset.json', {'queries':[{'query_id':'E01','gold_cards':['issuer/card']}],
                    'questions_sha256': questions['sha256'], 'protocol_sha256': protocol['sha256']})
    receipt = save(path/'freeze_receipt.json', {'dataset_sha256': dataset['sha256'], 'questions_sha256': questions['sha256'],
                    'protocol_sha256': protocol['sha256'], 'paid_api_files_at_freeze':0})
    return digest((path/'freeze_receipt.json').read_bytes())

def test_recomputed_dataset_digest_cannot_change_frozen_gold(tmp_path):
    pin = fixture(tmp_path)
    value = json.loads((tmp_path/'dataset.json').read_text());value.pop('sha256')
    value['queries'][0]['gold_cards'] = ['other/card']
    save(tmp_path/'dataset.json',value)
    with pytest.raises(RuntimeError,match='dataset.*receipt'):
        M.verify_inputs(tmp_path,pin)

def test_recomputed_receipt_digest_cannot_hide_relabeling(tmp_path):
    pin = fixture(tmp_path)
    value = json.loads((tmp_path/'freeze_receipt.json').read_text());value.pop('sha256')
    value['dataset_sha256'] = 'fake new label hash'
    save(tmp_path/'freeze_receipt.json',value)
    with pytest.raises(RuntimeError,match='receipt pin'):
        M.verify_inputs(tmp_path,pin)

def test_valid_binding_returns_only_non_gold_execution_inputs(tmp_path):
    pin = fixture(tmp_path)
    value = M.verify_inputs(tmp_path,pin)
    assert set(value) == {'questions','protocol','catalog','dataset_sha256','receipt_sha256'}
    assert 'gold_cards' not in json.dumps(value)
