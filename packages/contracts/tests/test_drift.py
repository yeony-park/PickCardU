from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml


CONTRACTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONTRACTS))

from generate import render  # noqa: E402


class ContractDriftTest(unittest.TestCase):
    def test_generated_contracts_match_fastapi_openapi(self) -> None:
        yaml_text, types_text = render()
        self.assertEqual((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"), yaml_text)
        self.assertEqual((CONTRACTS / "generated/api.ts").read_text(encoding="utf-8"), types_text)

    def test_public_and_internal_boundaries_are_explicit(self) -> None:
        yaml_text = (CONTRACTS / "openapi.yaml").read_text(encoding="utf-8")
        self.assertIn("/v1/auth/login", yaml_text)
        self.assertIn("/internal/v1/lab/runs", yaml_text)
        self.assertNotIn("/api/v1", yaml_text)

    def test_chat_and_lab_error_contracts_are_typed(self) -> None:
        schema = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
        chat = schema["paths"]["/v1/conversations/{conversation_id}/messages"]["post"]["responses"]
        self.assertTrue({"401", "403", "404", "409", "503"} <= set(chat))
        self.assertEqual(chat["503"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/ChatFailureResponse")
        preview = schema["components"]["schemas"]["ChatFailureResponse"]["properties"]["retrieval_preview"]
        self.assertIn("#/components/schemas/RetrievalPreview", str(preview))
        lab = schema["paths"]["/internal/v1/lab/runs"]["post"]["responses"]
        self.assertTrue({"401", "403", "404", "409", "503"} <= set(lab))
        types = (CONTRACTS / "generated/api.ts").read_text(encoding="utf-8")
        self.assertIn('"ChatFailureResponse"', types)
        self.assertIn('"503": components["schemas"]["ChatFailureResponse"]', types)


if __name__ == "__main__":
    unittest.main()
