from __future__ import annotations

import json
import types
import unittest

from pydantic import ValidationError

from pickcardu_rag import AnswerOutput, OpenAIService, completed_context, validate_grounding
from pickcardu_rag.errors import LlmUnavailable, LlmUngrounded


class FakeResponses:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        parsed, usage = outcome if isinstance(outcome, tuple) else (outcome, None)
        return types.SimpleNamespace(output_parsed=parsed, usage=usage)


def incomplete_json_error() -> ValidationError:
    try:
        AnswerOutput.model_validate_json('{"answer_text":"응답","claims":[{"card_key":"c1')
    except ValidationError as exc:
        return exc
    raise AssertionError("fixture must be invalid JSON")


class AnsweringTests(unittest.TestCase):
    evidence = [{"card_key": "c1", "card_name": "카드1", "issuer": "발급사", "chunk_id": "k1", "text": "1%"}]

    def answer(self) -> AnswerOutput:
        return AnswerOutput.model_validate({
            "answer_text": "답",
            "recommendations": [{"card_key": "c1", "reason": "근거", "citations": ["k1"]}],
            "claims": [{"card_key": "c1", "text": "혜택", "citations": ["k1"]}],
        })

    @staticmethod
    def generated_answer(citations: list[str] | None = None) -> dict:
        citations = citations or ["e1"]
        return {
            "answer_text": "답",
            "recommendations": [{"reason": "근거", "citations": citations}],
            "claims": [{"text": "혜택", "citations": citations}],
        }

    def test_context_and_grounding_contract(self) -> None:
        messages = []
        for index in range(3):
            messages += [{"role": "user", "content": f"u{index}"}, {"role": "assistant", "content": f"a{index}"}]
        self.assertEqual([item["content"] for item in completed_context(messages, "q")], ["u1", "a1", "u2", "a2", "q"])
        valid = self.answer()
        self.assertIs(validate_grounding(valid, self.evidence), valid)
        wrong = AnswerOutput.model_validate({
            "answer_text": "답", "claims": [{"card_key": "c1", "text": "x", "citations": ["other"]}]
        })
        with self.assertRaises(ValueError):
            validate_grounding(wrong, self.evidence)
        insufficient = AnswerOutput(
            answer_status="insufficient_evidence",
            answer_text="현재 등록된 카드 문서에서는 확인하기 어렵습니다.",
        )
        self.assertIs(validate_grounding(insufficient, self.evidence), insufficient)
        with self.assertRaises(ValidationError):
            AnswerOutput(answer_status="answered", answer_text="근거 없는 답")
        with self.assertRaises(ValidationError):
            AnswerOutput(
                answer_status="insufficient_evidence",
                answer_text="근거 부족",
                claims=[{"card_key": "c1", "text": "x", "citations": ["k1"]}],
            )
        five = AnswerOutput(
            answer_text="최대 다섯 장",
            recommendations=[
                {"card_key": "c1", "reason": f"근거 {index}", "citations": ["k1"]}
                for index in range(5)
            ],
            claims=[{"card_key": "c1", "text": "혜택", "citations": ["k1"]}],
        )
        self.assertEqual(len(five.recommendations), 5)

    def test_grounding_validation_reports_the_specific_relationship_failure(self) -> None:
        cases = (
            (
                "unknown_card_key",
                self.evidence,
                {"card_key": "c2", "text": "혜택", "citations": ["k1"]},
            ),
            (
                "unknown_citation",
                self.evidence,
                {"card_key": "c1", "text": "혜택", "citations": ["missing"]},
            ),
            (
                "cross_card_citation",
                [
                    *self.evidence,
                    {
                        "card_key": "c2",
                        "card_name": "카드2",
                        "issuer": "발급사",
                        "chunk_id": "k2",
                        "text": "2%",
                    },
                ],
                {"card_key": "c1", "text": "혜택", "citations": ["k2"]},
            ),
        )

        for reason, evidence, claim in cases:
            with self.subTest(reason=reason):
                answer = AnswerOutput.model_validate({"answer_text": "답", "claims": [claim]})
                with self.assertRaisesRegex(ValueError, f"grounding_failure={reason}"):
                    validate_grounding(answer, evidence)

    def test_answer_payload_and_eof_retry_parity(self) -> None:
        responses = FakeResponses(incomplete_json_error(), (self.generated_answer(), {"output_tokens": 17}))
        service = OpenAIService(api_key=None, client=types.SimpleNamespace(responses=responses))
        answer, metadata = service.answer("질문", self.evidence)
        self.assertEqual(answer.answer_text, "답")
        self.assertEqual(answer.recommendations[0].card_key, "c1")
        self.assertEqual(answer.recommendations[0].citations, ["k1"])
        self.assertEqual(answer.claims[0].card_key, "c1")
        self.assertEqual(answer.claims[0].citations, ["k1"])
        self.assertEqual(metadata["attempt_count"], 2)
        self.assertFalse(metadata["usage_complete"])
        self.assertEqual(metadata["usage_scope"], "successful_attempt_only")
        self.assertEqual(metadata["attempt_failures"], ["incomplete_json"])
        self.assertEqual(metadata["usage"], {"output_tokens": 17})
        self.assertEqual(len(responses.calls), 2)
        changed = {key for key in responses.calls[0] if responses.calls[0][key] != responses.calls[1][key]}
        self.assertEqual(changed, {"instructions"})
        for call in responses.calls:
            self.assertIs(call["store"], False)
            self.assertEqual(call["tools"], [])
            self.assertEqual(call["max_output_tokens"], 2400)
            self.assertEqual(call["timeout"], 60.0)
            generated_model = call["text_format"]
            generated_model.model_validate(self.generated_answer())
            with self.assertRaises(ValidationError):
                generated_model.model_validate(self.generated_answer(["e2"]))

        payload = json.loads(responses.calls[0]["input"][0]["content"])
        self.assertEqual(payload["evidence"], [{
            "evidence_id": "e1",
            "card_name": "카드1",
            "issuer": "발급사",
            "text": "1%",
        }])

    def test_grounding_mismatch_retries_only_the_answer_generation(self) -> None:
        evidence = [
            *self.evidence,
            {"card_key": "c2", "card_name": "카드2", "issuer": "발급사", "chunk_id": "k2", "text": "2%"},
        ]
        responses = FakeResponses(
            self.generated_answer(["e1", "e2"]),
            (self.generated_answer(["e1"]), {"output_tokens": 17}),
        )
        service = OpenAIService(api_key=None, client=types.SimpleNamespace(responses=responses))

        answer, metadata = service.answer("질문", evidence)

        self.assertEqual(answer.answer_text, "답")
        self.assertEqual(answer.claims[0].card_key, "c1")
        self.assertEqual(answer.claims[0].citations, ["k1"])
        self.assertEqual(len(responses.calls), 2)
        self.assertEqual(metadata["attempt_count"], 2)
        self.assertEqual(metadata["attempt_failures"], ["mixed_card_citations"])
        self.assertFalse(metadata["usage_complete"])
        self.assertEqual(metadata["usage_scope"], "successful_attempt_only")
        self.assertNotEqual(responses.calls[0]["instructions"], responses.calls[1]["instructions"])

    def test_duplicate_card_recommendation_retries_answer_generation(self) -> None:
        duplicate = self.generated_answer()
        duplicate["recommendations"].append({"reason": "중복", "citations": ["e1"]})
        responses = FakeResponses(duplicate, self.generated_answer())

        answer, metadata = OpenAIService(
            api_key=None,
            client=types.SimpleNamespace(responses=responses),
        ).answer("질문", self.evidence)

        self.assertEqual(len(answer.recommendations), 1)
        self.assertEqual(len(responses.calls), 2)
        self.assertEqual(metadata["attempt_failures"], ["duplicate_card_recommendation"])

    def test_retry_final_failure_metadata_and_first_failure_no_retry(self) -> None:
        wrong = self.generated_answer(["e1", "e1"])
        cases = (
            (None, LlmUngrounded, ["incomplete_json", "refused_or_empty"]),
            (wrong, LlmUngrounded, ["incomplete_json", "duplicate_evidence_id"]),
            (RuntimeError("provider"), LlmUnavailable, ["incomplete_json", "provider_error"]),
        )
        for outcome, error_type, expected_failures in cases:
            responses = FakeResponses(incomplete_json_error(), outcome)
            with self.assertRaises(error_type) as caught:
                OpenAIService(api_key=None, client=types.SimpleNamespace(responses=responses)).answer("q", self.evidence)
            self.assertEqual(len(responses.calls), 2)
            self.assertEqual(caught.exception.extra["answer_usage"]["usage_scope"], "unavailable")
            self.assertEqual(caught.exception.extra["answer_usage"]["attempt_failures"], expected_failures)

        responses = FakeResponses(RuntimeError("provider"))
        with self.assertRaises(LlmUnavailable) as caught:
            OpenAIService(api_key=None, client=types.SimpleNamespace(responses=responses)).answer("q", self.evidence)
        self.assertEqual(len(responses.calls), 1)
        self.assertNotIn("answer_usage", caught.exception.extra)


if __name__ == "__main__":
    unittest.main()
