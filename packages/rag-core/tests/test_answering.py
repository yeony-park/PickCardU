from __future__ import annotations

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

    def test_answer_payload_and_eof_retry_parity(self) -> None:
        responses = FakeResponses(incomplete_json_error(), (self.answer(), {"output_tokens": 17}))
        service = OpenAIService(api_key=None, client=types.SimpleNamespace(responses=responses))
        answer, metadata = service.answer("질문", self.evidence)
        self.assertEqual(answer.answer_text, "답")
        self.assertEqual(metadata["attempt_count"], 2)
        self.assertFalse(metadata["usage_complete"])
        self.assertEqual(metadata["usage_scope"], "successful_attempt_only")
        self.assertEqual(metadata["usage"], {"output_tokens": 17})
        self.assertEqual(len(responses.calls), 2)
        changed = {key for key in responses.calls[0] if responses.calls[0][key] != responses.calls[1][key]}
        self.assertEqual(changed, {"instructions"})
        for call in responses.calls:
            self.assertIs(call["store"], False)
            self.assertEqual(call["tools"], [])
            self.assertEqual(call["max_output_tokens"], 2400)
            self.assertEqual(call["timeout"], 60.0)

    def test_retry_final_failure_metadata_and_first_failure_no_retry(self) -> None:
        wrong = AnswerOutput.model_validate({
            "answer_text": "답", "claims": [{"card_key": "c1", "text": "x", "citations": ["other"]}]
        })
        for outcome, error_type in ((None, LlmUngrounded), (wrong, LlmUngrounded), (RuntimeError("provider"), LlmUnavailable)):
            responses = FakeResponses(incomplete_json_error(), outcome)
            with self.assertRaises(error_type) as caught:
                OpenAIService(api_key=None, client=types.SimpleNamespace(responses=responses)).answer("q", self.evidence)
            self.assertEqual(len(responses.calls), 2)
            self.assertEqual(caught.exception.extra["answer_usage"]["usage_scope"], "unavailable")

        responses = FakeResponses(RuntimeError("provider"))
        with self.assertRaises(LlmUnavailable) as caught:
            OpenAIService(api_key=None, client=types.SimpleNamespace(responses=responses)).answer("q", self.evidence)
        self.assertEqual(len(responses.calls), 1)
        self.assertNotIn("answer_usage", caught.exception.extra)


if __name__ == "__main__":
    unittest.main()

