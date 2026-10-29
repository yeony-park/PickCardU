"""A copied/stale/tampered review must not change the published diagnosis."""
import importlib.util
from pathlib import Path
import sys
import shutil
import json
import pytest

spec=importlib.util.spec_from_file_location('review43_binding_tests',Path(__file__).with_name('43_review_binding.py'))
V=importlib.util.module_from_spec(spec);sys.modules[spec.name]=V;spec.loader.exec_module(V)
SOURCE=Path(__file__).parents[1]/'data/43_offline_validation'

def test_current_source_review_binding_is_valid():
    assert V.verify_reviews(SOURCE)['source_review_complete'] is True

@pytest.mark.parametrize('kind',['verdict','contract','packet','receipt_recomputed'])
def test_modified_source_review_inputs_are_rejected(tmp_path,kind):
    for file in SOURCE.glob('*.json'):
        shutil.copy2(file,tmp_path/file.name)
    name={'verdict':'main_review.json','contract':'review_contract.json','packet':'review_packets.json',
          'receipt_recomputed':'review_receipt.json'}[kind]
    value=json.loads((tmp_path/name).read_text())
    if kind=='verdict':value['records'][0]['verdict']='FAIL'
    elif kind=='contract':value['requirements'][0]['min_percent']=99
    elif kind=='packet':value['records'][0]['question']='changed'
    else:
        value['source_review_complete']=False
        value['sha256']=V.digest({k:v for k,v in value.items() if k!='sha256'})
    (tmp_path/name).write_text(json.dumps(value,ensure_ascii=False))
    with pytest.raises(RuntimeError):V.verify_reviews(tmp_path)
