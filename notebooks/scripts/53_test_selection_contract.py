"""Offline regression checks for the notebook's source-bound paired decision.

Execute the actual evaluation cell on synthetic journals; no API or model load.
"""
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import nbformat
import pytest


_spec=importlib.util.spec_from_file_location('question_evaluator',Path(__file__).with_name('53_question_evaluation.py'))
E=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(E)


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def journal(tmp_path):
    root, stage = tmp_path, tmp_path / "experiment"
    pdf = root / "data/raw/test/card.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"source fixture")
    queries = [dict(query_id=f"Q{i:02}", question=f"question{i}") for i in range(12)]
    manifest = dict(decision="fixed gate", source_input_sha256={"data/raw/test/card.pdf": digest(pdf.read_bytes())},
                    source_page_counts={"test/card": 1})
    answers, judgments, retrievals = [], [], []
    for q in queries:
        for pf in ["benefit", "parent"]:
            for repeat in [1, 2]:
                for mode in ["A", "B"]:
                    identity = f"{q['query_id']}:{pf}:{mode}:r{repeat}"
                    raw = dict(identity=identity, status="ok", answer={"text": "fixture"})
                    write(stage / "answers" / (identity + ".json"), raw)
                    row = dict(**raw, query_id=q["query_id"], profile=pf, mode=mode, repeat=repeat,
                               identical_AB_input=False, reuse_source_sha256=None)
                    answers.append(row)
                    retrievals.append(dict(identity=identity, result=dict(cards=[dict(card_key="test/card")], evidence=[])))
                    judgments.append(dict(identity=identity, answer_sha256=digest(row),
                        question_sha256=digest(q["question"]), verdict="PARTIAL", reason="fixture",
                        important_error=False, new_important_error_vs_A=False, condition_loss_vs_A=False, scope_error=False,
                        source_support=[dict(card_key="test/card", path="data/raw/test/card.pdf",
                            sha256=digest(pdf.read_bytes()), page=1, observation="fixture", relation="retrieved_card")]))
    completed = dict(answers=answers, retrievals=retrievals, manifest_sha256=digest(manifest))
    source = next(c.source for c in nbformat.read(Path(__file__).parents[1] / "53_sequential_fact_supplement.ipynb", 4).cells
                  if c.cell_type == "code" and "judgment_path=PATH" in c.source)

    def evaluate():
        rubric_by_query={q['query_id']:dict(question=q['question'],must_answer=['requested fact'],context_anchor=['extra context']) for q in queries}
        rubrics=dict(records=list(rubric_by_query.values()))
        for j in judgments:
            j.update(question_verdict=j['verdict'],material_omission=False,rubric_sha256=digest(rubric_by_query[j['identity'].split(':')[0]]),
                must_answer_coverage=dict(covered=['requested fact'] if j['verdict']=='PASS' else [],missing=[] if j['verdict']=='PASS' else ['requested fact'],not_applicable=[]),
                annotation_coverage=dict(covered=[],missing=['extra context'],not_applicable=[]))
        write(stage / "execution_results.json", completed)
        write(stage / "source_judgments.json", dict(records=judgments))
        read = lambda p: json.loads(p.read_text())
        ns = dict(PATH=stage, ROOT=root, P=SimpleNamespace(sha=digest, F=SimpleNamespace(read_json=read)),
                  X=SimpleNamespace(PROFILES=["benefit", "parent"], frozen=write), queries=queries,
                  manifest=manifest, json=json, gold_by_query={q["query_id"]: [] for q in queries},E=E,rubrics=rubrics,rubric_by_query=rubric_by_query)
        exec(compile(source, "53 evaluation cell", "exec"), ns)
        return read(stage / "evaluation_summary.json")["decisions"]

    return SimpleNamespace(answers=answers, judgments=judgments, completed=completed,
                           stage=stage, evaluate=evaluate)


def test_same_repeat_sharing_uses_raw_checkpoint_not_augmented_result(journal):
    a, b = journal.answers[:2]
    b["identical_AB_input"] = True
    b["reuse_source_sha256"] = digest(json.loads((journal.stage / "answers" / (a["identity"] + ".json")).read_text()))
    journal.judgments[1]["answer_sha256"] = digest(b)
    result = journal.evaluate()
    assert result[0]["selected"] == "A"
    assert result[0]["question_wins_losses_ties"] == dict(win=0, loss=0, tie=12)
    assert result[0]["logical_denominator_per_arm"] == 24


def test_one_question_gain_selects_B_without_dropping_other_questions(journal):
    journal.judgments[1]["verdict"] = "PASS"
    result = journal.evaluate()
    assert result[0]["selected"] == "B"
    assert result[0]["question_wins_losses_ties"] == dict(win=1, loss=0, tie=11)
    assert result[1]["selected"] == "A"


@pytest.mark.parametrize("guard", ["important_error", "condition_loss_vs_A", "scope_error", "loss"])
def test_gain_cannot_hide_a_guardrail_failure(journal, guard):
    journal.judgments[1]["verdict"] = "PASS"
    if guard == "loss":
        journal.judgments[8]["verdict"] = "PASS"  # different question, A only
    else:
        journal.judgments[3][guard] = True
    assert journal.evaluate()[0]["selected"] == "A"


@pytest.mark.parametrize("mutation", ["missing", "answer_hash", "source_hash", "page", "unrelated"])
def test_incomplete_or_unbound_judgment_is_rejected(journal, mutation):
    j = journal.judgments[0]
    if mutation == "missing":
        journal.judgments.pop()
    elif mutation == "answer_hash":
        j["answer_sha256"] = "stale"
    elif mutation == "source_hash":
        j["source_support"][0]["sha256"] = "stale"
    elif mutation == "page":
        j["source_support"][0]["page"] = 2
    else:
        j["verdict"] = "PASS"
        j["source_support"][0]["relation"] = "missing_expected_card"
    with pytest.raises((AssertionError, KeyError)):
        journal.evaluate()


def test_different_new_error_is_not_hidden_by_an_existing_A_error(journal):
    journal.judgments[9]['verdict'] = 'PASS'  # another question improves
    journal.judgments[0]['important_error'] = True
    journal.judgments[1]['important_error'] = True
    journal.judgments[1]['new_important_error_vs_A'] = True
    assert journal.evaluate()[0]['selected'] == 'A'
