"""Hand-checked boundary cases; no model or API calls."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('stats42_test', Path(__file__).with_name('42_statistics.py'))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

def test_wilson_all_failures_still_has_nonzero_upper_uncertainty():
    value = S.wilson(0, 4)
    assert value['lower'] == pytest.approx(0, abs=1e-12)
    assert value['upper'] == pytest.approx(.4898908364, abs=1e-8)

def test_wilson_all_passes_does_not_prove_population_perfection():
    value = S.wilson(4, 4)
    assert value['lower'] == pytest.approx(.5101091636, abs=1e-8)
    assert value['upper'] == pytest.approx(1, abs=1e-12)

def test_exact_paired_test_uses_only_discordant_pairs():
    assert S.paired_exact(3, 0) == .25
    assert S.paired_exact(4, 0) == .125
    assert S.paired_exact(0, 4) == .125
    assert S.paired_exact(2, 2) == 1
    assert S.paired_exact(0, 0) == 1

def test_invalid_denominator_is_rejected():
    with pytest.raises(ValueError):
        S.wilson(5, 4)
    with pytest.raises(ValueError):
        S.wilson(0, 0)
    with pytest.raises(ValueError):
        S.paired_exact(-1, 2)
