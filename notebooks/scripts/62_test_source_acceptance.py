import importlib.util
from pathlib import Path
import pytest


def module():
    spec=importlib.util.spec_from_file_location('sourcegate62',Path(__file__).with_name('62_check_source_acceptance.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_completion_cannot_omit_a_review_or_pdf_image_dependency():
    m=module()
    for missing in ('review.json','card.pdf','image.png'):
        expected={'review.json':'a','card.pdf':'b','image.png':'c'}
        actual={k:v for k,v in expected.items()if k!=missing}
        with pytest.raises(ValueError):m.require_coverage(actual,expected)


def test_changed_file_is_not_accepted_by_a_different_completion_hash():
    with pytest.raises(ValueError):module().require_coverage({'review.json':'changed'},{'review.json':'original'})


def test_manual_record_and_result_must_match_the_index():
    m=module();record={'identity':'q:p:U:r1','result_sha256':'result','reason':'reviewed'}
    index={'identity':record['identity'],'manual_record_sha256':m.digest(record),'result_sha256':'result'}
    m.require_record(index,record,'result')
    for changed in [{**record,'reason':'changed'},{**record,'result_sha256':'other'}]:
        with pytest.raises(ValueError):m.require_record(index,changed,'result')


def test_only_accepted_status_can_enter_paid_notebook_wrappers():
    with pytest.raises(ValueError):module().require_status('ready_for_independent_acceptance')


def test_entry_check_rejects_partial_completion_even_when_listed_hashes_pass(monkeypatch,tmp_path):
    from types import SimpleNamespace
    m=module();needed={'review.json':'a','card.pdf':'b','image.png':'c'}
    monkeypatch.setattr(m,'collect_dependencies',lambda stage,nb:dict(files_sha256=needed,notebook_code_sha256='code'))
    completion=dict(status='completed_after_pooled_source_and_independent_review',
        files_sha256={'review.json':'a'},next_notebook_code_sha256='code')
    stage=SimpleNamespace(B61=tmp_path,read=lambda p:completion,guard59=lambda:None,
        R=SimpleNamespace(guard_files=lambda files:None))
    with pytest.raises(ValueError):m.check(stage,tmp_path/'62.ipynb')


def test_paid_functions_are_never_called_when_entry_check_rejects(monkeypatch):
    from types import SimpleNamespace
    m=module();calls=[]
    def reject(*a,**kw):raise ValueError('changed original proof')
    monkeypatch.setattr(m,'check',reject)
    stage=SimpleNamespace(prepare62=lambda n:calls.append('prepare'),run62=lambda *a,**kw:calls.append('paid'))
    with pytest.raises(ValueError):m.prepare(stage,Path('62.ipynb'))
    with pytest.raises(ValueError):m.run(stage,Path('62.ipynb'),[],{},allow_network=True)
    assert calls==[]
