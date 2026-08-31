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

    def test_contract_exposes_only_pipeline_routes(self) -> None:
        schema = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
        paths = set(schema["paths"])
        self.assertEqual(paths, {"/v1/health/live", "/v1/health/ready", "/v1/search", "/v1/answer"})
        self.assertFalse(any("auth" in path or "profile" in path or "conversation" in path or "lab" in path for path in paths))
        query = schema["components"]["schemas"]["QueryRequest"]
        self.assertEqual(query["properties"]["top_k"]["default"], 3)


if __name__ == "__main__":
    unittest.main()
