"""Regression examples for the approved screen-only evaluation policy."""
import copy
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("visible66", Path(__file__).with_name("66_visible_answer_evaluation.py"))
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


@pytest.fixture
def case(tmp_path, monkeypatch):
    rubric = {"query_id": "T01", "question": "커피 할인 카드 추천해줘", "question_type": "recommendation",
              "requirements": [{"id": "coffee_card", "description": "커피 할인 카드 추천"}]}
    job = {"identity": "T01:benefit:SEQ:r1", "query_id": "T01", "question": rubric["question"],
           "profile": "benefit", "mode": "SEQ", "repeat": 1,
           "result": {"cards": [{"card_key": "issuer/A"}], "evidence": [
               {"chunk_id": "c1", "card_key": "issuer/A", "card_name": "A카드", "issuer": "발급사",
                "text": "커피 10% 할인. 전월실적 30만원."}]}}
    answer = {"identity": job["identity"], "status": "ok", "input_sha256": "fixed-input",
              "answer": {"answer_text": "A카드는 커피 10% 할인이 있습니다.",
                         "recommendations": [{"card_key": "issuer/A", "reason": "커피 할인 카드입니다."}],
                         "claims": [{"text": "화면에는 없는 주장"}]}}
    ref = {"card_key": "issuer/A", "condition_id": "issuer/A:coffee.rate"}
    monkeypatch.setattr(E, "ROOT", tmp_path)
    roles = E.expected_roles(66)
    files = {}
    for role, name in roles.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"fixture_role":"' + role + '"}')
        files[name] = E.file_digest(path)
    context = {"stage": 66, "artifact_roles": roles, "files_sha256": files,
               "source_refs_sha256_by_query": {"T01": E.digest([("issuer/A", "issuer/A:coffee.rate")])},
               "input_receipts_by_identity": {job["identity"]: {"status": "verified_actual_saved_input", "answer_row_sha256": E.digest(answer), "job_result_sha256": E.digest(job["result"]), "input_sha256": answer["input_sha256"]}}}
    review = {**E.binding(job, answer, rubric, context), "reviewed_by": "main Codex", "reason": "실제 화면과 원문 대조",
              "requirements": [{"id": "coffee_card", "status": "satisfied", "reason": "카드 추천과 혜택 확인",
                                "quotes": [{"surface": "answer_text", "quote": "커피 10% 할인"}]}],
              "surface_audits": [
                  {"surface": surface, "status": "correct", "input_status": "supported", "basis": "source",
                   "source_refs": [ref], "input_quotes": [{"chunk_id": "c1", "quote": "커피 10% 할인"}],
                   "errors": [], "reason": "동일 카드 커피 할인 사실 일치",
                   "spans": [{"start": 0, "end": len(E.visible_surfaces(answer["answer"])[surface]),
                              "quote": E.visible_surfaces(answer["answer"])[surface], "kind": "factual",
                              "card_keys": ["issuer/A"], "source_refs": [ref], "status": "correct",
                              "input_status": "supported", "input_quotes": [{"chunk_id": "c1", "quote": "커피 10% 할인"}],
                              "reason": "단일 카드의 사실 문장 전부 대조"}]}
                  for surface in E.visible_surfaces(answer["answer"])],
              "optional_details": {"notes": "미요청 실적은 생략됨"},
              "claims_diagnostic": {"notes": "별도 진단", "error_codes": []}}
    return job, answer, rubric, review, {("issuer/A", "issuer/A:coffee.rate")}, context


def test_unasked_spend_omission_does_not_fail_visible_answer(case):
    job, answer, rubric, review, refs, context = case
    assert E.score_review(job, answer, rubric, review, refs, context=context)["passed"] is True


def test_hidden_claim_error_does_not_change_screen_verdict(case):
    job, answer, rubric, review, refs, context = case
    review["claims_diagnostic"]["error_codes"] = ["hidden_wrong_rate"]
    assert E.score_review(job, answer, rubric, review, refs, context=context)["passed"] is True


def test_actual_wrong_assertion_fails_even_when_request_is_answered(case):
    job, answer, rubric, review, refs, context = case
    review["surface_audits"][0].update(status="incorrect", errors=[
        {"code": "wrong_rate", "quote": "10%", "reason": "원문 비율과 다름"}])
    review["surface_audits"][0]["spans"][0]["status"] = "incorrect"
    result = E.score_review(job, answer, rubric, review, refs, context=context)
    assert result["direct_request_satisfied"] is True
    assert result["passed"] is False


def test_source_correct_but_input_unsupported_remains_separate(case):
    job, answer, rubric, review, refs, context = case
    review["surface_audits"][0].update(input_status="unsupported", input_quotes=[])
    review["surface_audits"][0]["spans"][0].update(input_status="unsupported", input_quotes=[])
    result = E.score_review(job, answer, rubric, review, refs, context=context)
    assert result["passed"] is True
    assert result["input_fully_supported"] is False


def test_missing_direct_requirement_fails(case):
    job, answer, rubric, review, refs, context = case
    review["requirements"][0].update(status="missing", quotes=[])
    assert E.score_review(job, answer, rubric, review, refs, context=context)["passed"] is False


def test_hidden_claim_cannot_supply_missing_visible_answer(case):
    job, answer, rubric, review, refs, context = case
    review["requirements"][0]["quotes"] = [{"surface": "claims.0", "quote": "화면에는 없는 주장"}]
    with pytest.raises(ValueError, match="visible"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def test_changed_recommendation_invalidates_review_binding(case):
    job, answer, rubric, review, refs, context = case
    answer["answer"]["recommendations"][0]["reason"] = "다른 내용"
    with pytest.raises(ValueError, match="binding|input"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def test_every_displayed_recommendation_requires_review(case):
    job, answer, rubric, review, refs, context = case
    review["surface_audits"].pop()
    with pytest.raises(ValueError, match="surface"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def test_quote_from_another_card_cannot_ground_recommendation(case):
    job, answer, rubric, review, refs, context = case
    job["result"]["evidence"][0]["card_key"] = "issuer/B"
    context["input_receipts_by_identity"][job["identity"]].update(answer_row_sha256=E.digest(answer), job_result_sha256=E.digest(job["result"]))
    review.update(E.binding(job, answer, rubric, context))
    with pytest.raises(ValueError, match="card"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def test_claim_validation_failure_counts_as_technical_failure(case):
    job, answer, rubric, review, refs, context = case
    answer.update(status="failed", answer=None)
    context["input_receipts_by_identity"][job["identity"]].update(answer_row_sha256=E.digest(answer))
    review.update(E.binding(job, answer, rubric, context), surface_audits=[])
    review["requirements"][0].update(status="missing", quotes=[])
    result = E.score_review(job, answer, rubric, review, refs, context=context)
    assert result["technical_success"] is False
    assert result["passed"] is False


def test_partial_grid_cannot_be_reported_as_completed(case):
    job, answer, rubric, review, refs, context = case
    row = E.score_review(job, answer, rubric, review, refs, context=context)
    with pytest.raises(ValueError, match="matrix"):
        E.aggregate([row], query_ids=["T01", "T02"], profiles=["benefit"], modes=["SEQ"], repeats=[1], context=context)


def test_empty_preservation_map_is_not_a_verified_inventory(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        E.verify_file_map({}, tmp_path)


def test_changed_file_invalidates_preservation_check(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("original")
    inventory = {"source.txt": E.file_digest(path)}
    assert E.verify_file_map(inventory, tmp_path) == 1
    path.write_text("changed")
    with pytest.raises(ValueError, match="source.txt"):
        E.verify_file_map(inventory, tmp_path)


def test_policy_or_evaluator_change_invalidates_context(case):
    job, answer, rubric, review, refs, context = case
    name = next(iter(context["files_sha256"]))
    context["files_sha256"][name] = "0" * 64
    with pytest.raises(ValueError, match="Preserved file changed"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def test_unreviewed_second_visible_statement_is_rejected(case):
    job, answer, rubric, review, refs, context = case
    answer["answer"]["answer_text"] += " B카드는 월 2만원까지 할인됩니다."
    context["input_receipts_by_identity"][job["identity"]]["answer_row_sha256"] = E.digest(answer)
    review.update(E.binding(job, answer, rubric, context))
    with pytest.raises(ValueError, match="cover the full"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def test_numeric_assertion_cannot_be_hidden_in_nonfactual_span(case):
    job, answer, rubric, review, refs, context = case
    review["surface_audits"][0]["spans"][0]["kind"] = "nonfactual"
    with pytest.raises(ValueError, match="numeric assertion"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def saved_provider_case(tmp_path, monkeypatch, case_data):
    from pickcardu_rag.answering import serialize_answer_payload

    job, answer, _, _, _, _ = case_data
    monkeypatch.setattr(E, "ROOT", tmp_path)
    base = tmp_path / "saved"
    (base / "api_cache").mkdir(parents=True)
    (base / "payload_contracts").mkdir()
    actual = [{"role": "user", "content": serialize_answer_payload(job["question"], job["result"]["evidence"])}]
    answer.update(input_sha256=E.digest(actual), evidence_sha256=E.digest(job["result"]["evidence"]),
                  retrieval_sha256=E.digest(job["result"]), request_keys=["request"])
    contract = {"input_sha256_by_identity": {job["identity"]: answer["input_sha256"]}}
    answer["payload_contract_sha256"] = E.digest(contract)
    (base / "payload_contracts" / (job["identity"] + ".json")).write_text(E.json.dumps(contract))
    path = base / "api_cache/request.json"
    path.write_text(E.json.dumps({"request": {"input": actual}}))
    return job, answer, base, path


def test_actual_provider_input_is_bound_to_saved_evidence(tmp_path, monkeypatch, case):
    job, answer, base, _ = saved_provider_case(tmp_path, monkeypatch, case)
    proof = E.verify_saved_input(job, answer, base)
    assert proof["status"] == "verified_actual_saved_input"
    assert proof["physical_request_count"] == 1


def test_changed_provider_payload_does_not_become_grounded(tmp_path, monkeypatch, case):
    job, answer, base, path = saved_provider_case(tmp_path, monkeypatch, case)
    raw = E.read(path)
    raw["request"]["input"][0]["content"] = "다른 근거"
    path.write_text(E.json.dumps(raw))
    with pytest.raises(ValueError, match="transmitted"):
        E.verify_saved_input(job, answer, base)


def test_context_role_cannot_substitute_an_unrelated_hashed_file(case):
    job, answer, rubric, review, refs, context = case
    context["artifact_roles"]["policy"] = context["artifact_roles"]["evaluator"]
    review.update(E.binding(job, answer, rubric, context))
    with pytest.raises(ValueError, match="authority role"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def test_question_context_cannot_invent_a_number_not_in_question(case):
    job, answer, rubric, review, refs, context = case
    span = review["surface_audits"][0]["spans"][0]
    span.update(kind="question_context", question_quotes=["커피"])
    with pytest.raises(ValueError, match="invented a numeric"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def test_nonnumeric_benefit_assertion_cannot_hide_as_neutral(case):
    job, answer, rubric, review, refs, context = case
    text = "실적 없이 모든 가맹점에서 할인됩니다."
    answer["answer"]["answer_text"] = text
    context["input_receipts_by_identity"][job["identity"]]["answer_row_sha256"] = E.digest(answer)
    review.update(E.binding(job, answer, rubric, context))
    review["requirements"][0].update(status="missing", quotes=[])
    review["surface_audits"][0]["spans"] = [{"start": 0, "end": len(text), "quote": text,
                                            "kind": "nonfactual", "reason": "숨긴 사실 주장 반례"}]
    with pytest.raises(ValueError, match="allowed neutral"):
        E.score_review(job, answer, rubric, review, refs, context=context)


def page_reference_case(case):
    job, answer, rubric, review, _, context = case
    condition = "issuer/A:pdf_page66.1"
    ref = {"card_key": "issuer/A", "condition_id": condition, "quote": "커피 10% 할인"}
    source = {"records": [{"card_key": "issuer/A", "facts": [{"condition_id": condition,
              "reference_unit": "pdf_page", "source_support": [{"quote": "커피 10% 할인. 전월실적 30만원."}]}]}]}
    name = context["artifact_roles"]["source_registry"]
    path = E.ROOT / name
    path.write_text(E.json.dumps(source))
    context["files_sha256"][name] = E.file_digest(path)
    refs = {("issuer/A", condition)}
    context["source_refs_sha256_by_query"]["T01"] = E.digest(sorted(refs))
    for audit in review["surface_audits"]:
        audit["source_refs"] = [ref]
        for span in audit["spans"]:
            span["source_refs"] = [ref]
    review.update(E.binding(job, answer, rubric, context))
    return job, answer, rubric, review, refs, context


def test_source_page_reference_requires_the_actual_owning_quote(case):
    job, answer, rubric, review, refs, context = page_reference_case(case)
    assert E.score_review(job, answer, rubric, review, refs, context=context)["passed"] is True


def test_source_page_cannot_support_an_unrelated_literal_quote(case):
    job, answer, rubric, review, refs, context = page_reference_case(case)
    review["surface_audits"][0]["spans"][0]["source_refs"][0]["quote"] = "모든 가맹점 무제한"
    with pytest.raises(ValueError, match="owning-page"):
        E.score_review(job, answer, rubric, review, refs, context=context)


@pytest.mark.parametrize("text", [
    "신청 전 공식 상품설명서에서 적용 조건을 다시 확인해 주세요.",
    "신청 전 공식 상품설명서를 재확인해 주세요.",
    "신청 전 공식 상품설명서에서 혜택 적용 방식을 다시 확인해 주세요.",
    "신청 전 공식 상품설명서에서 철도 적용 여부와 최신 조건을 확인해 주세요.",
    "신청 전 공식 상품설명서에서 업종 분류와 세부 조건을 다시 확인해 주세요.",
    "신청·이용 전 공식 상품설명서에서 적용 조건을 다시 확인해 주세요.",
    "신청·결제 전 공식 상품설명서에서 최신 조건을 확인해 주세요.",
])
def test_pure_official_notice_variants_do_not_become_card_claims(case, text):
    job, answer, rubric, review, refs, context = case
    answer["answer"]["answer_text"] += " " + text
    context["input_receipts_by_identity"][job["identity"]]["answer_row_sha256"] = E.digest(answer)
    review.update(E.binding(job, answer, rubric, context))
    audit = review["surface_audits"][0]
    old_end = audit["spans"][-1]["end"]
    audit["spans"].append({"start": old_end, "end": len(answer["answer"]["answer_text"]),
                           "quote": " " + text, "kind": "nonfactual", "reason": "순수 공식문서 확인 권고"})
    assert E.score_review(job, answer, rubric, review, refs, context=context)["passed"] is True


def test_false_benefit_premise_cannot_hide_in_official_notice(case):
    job, answer, rubric, review, refs, context = case
    text = "신청 전 공식 상품설명서에서 실적 없이 모든 가맹점 할인됨을 확인해 주세요."
    answer["answer"]["answer_text"] = text
    context["input_receipts_by_identity"][job["identity"]]["answer_row_sha256"] = E.digest(answer)
    review.update(E.binding(job, answer, rubric, context))
    review["requirements"][0].update(status="missing", quotes=[])
    review["surface_audits"][0]["spans"] = [{"start": 0, "end": len(text), "quote": text,
                                            "kind": "nonfactual", "reason": "거짓 전제를 숨기는 반례"}]
    with pytest.raises(ValueError, match="allowed neutral"):
        E.score_review(job, answer, rubric, review, refs, context=context)


@pytest.mark.parametrize("text", ["개인 소비액을 몰라 계산하기 어려워요.", "개인 소비액을 몰라 계산하기 어렵습니다.", "개인 소비액을 몰라 계산할 수없어요.", "개인 소비액을 몰라 비교하기 어려우니 확인이 필요해요.", "개인 소비액을 몰라 단정하기 어려워요.", "보유하신 카드가 무엇인지와 넷플릭스 결제 금액을 알아야 실제 할인액을 계산할 수 있어요.", "카드가 어떤 상품인지와 넷플릭스 결제 금액을 알아야 실제 할인액을 계산할 수 있어요.", "어떤 카드를 쓰시는지와 넷플릭스 결제 금액을 몰라 실제 할인액을 확정할 수는 없어요.", "보유 카드와 넷플릭스 결제금액을 알려주시지 않아 오늘 할인액을 정확히 계산할 수는 없어요."])
def test_uncertainty_inflections_do_not_change_semantic_verdict(case, text):
    job, answer, rubric, review, refs, context = case
    answer["answer"]["answer_text"] = text
    context["input_receipts_by_identity"][job["identity"]]["answer_row_sha256"] = E.digest(answer)
    review.update(E.binding(job, answer, rubric, context))
    review["requirements"][0]["quotes"] = [{"surface": "recommendations.0.reason", "quote": "커피 할인 카드입니다."}]
    review["surface_audits"][0].update(basis="question_context", source_refs=[], input_quotes=[], input_status="not_applicable", spans=[
        {"start": 0, "end": len(text), "quote": text, "kind": "question_context", "status": "correct",
         "question_quotes": ["카드"], "reason": "개인 소비액이 없는 원질문에서 정확한 절약액만 유보한다."}])
    assert E.score_review(job, answer, rubric, review, refs, context=context)["passed"] is True


def test_same_size_edit_with_restored_mtime_still_invalidates_hash(tmp_path):
    import os

    p = tmp_path / "source.txt"
    p.write_text("original")
    old_stat = p.stat()
    files = {"source.txt": E.file_digest(p)}
    p.write_text("modified")
    os.utime(p, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    with pytest.raises(ValueError, match="source.txt"):
        E.verify_file_map(files, tmp_path)
