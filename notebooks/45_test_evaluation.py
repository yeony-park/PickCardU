import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('evaluation_test45',Path(__file__).with_name('45_evaluate_results.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)


@pytest.mark.parametrize('outcomes,want',[
    (['win','loss','win'],'A'),(['tie','tie'],'A'),
    (['win','tie'],'B_development_candidate'),(['win','uncertain'],'A')])
def test_selection_never_hides_a_loss_or_uncertainty_in_the_average(outcomes,want):
    assert M.decide(outcomes)==want
