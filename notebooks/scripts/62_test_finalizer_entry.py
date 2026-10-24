"""The offline final gate must not run with Python assertions disabled."""
from pathlib import Path
import subprocess
import sys


SCRIPT=Path(__file__).with_name('62_finalize_evaluation.py')


def test_optimized_interpreter_is_rejected_before_any_evaluation(tmp_path):
    result=subprocess.run([sys.executable,'-O','-B',str(SCRIPT),'--help'],cwd=tmp_path,
        capture_output=True,text=True,check=False)
    assert result.returncode!=0
    assert 'Assertions must be enabled' in result.stderr
    assert not list(tmp_path.iterdir())


def test_unoptimized_cli_help_does_not_evaluate_or_write(tmp_path):
    result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--help'],cwd=tmp_path,
        capture_output=True,text=True,check=False)
    assert result.returncode==0
    assert '--check' in result.stdout
    assert not list(tmp_path.iterdir())


def test_finalizer_binding_rejects_changed_gate_or_input():
    import importlib.util
    import pytest
    spec=importlib.util.spec_from_file_location('entry62',SCRIPT)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    receipt={'validator_code_sha256':{'gate.py':'code'},'input_files_sha256':{'answer.json':'raw'}}
    value.require_exact_binding(receipt,{'gate.py':'code'},{'answer.json':'raw'})
    with pytest.raises(RuntimeError):value.require_exact_binding(receipt,{'gate.py':'changed'},{'answer.json':'raw'})
    with pytest.raises(RuntimeError):value.require_exact_binding(receipt,{'gate.py':'code'},{'answer.json':'changed'})
    with pytest.raises(RuntimeError):value.require_exact_binding(receipt,{'gate.py':'code'},{})
