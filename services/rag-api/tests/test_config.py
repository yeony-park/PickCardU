from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_rag_api.config import Settings, validate_settings  # noqa: E402
from pickcardu_rag_api.main import create_app  # noqa: E402


def production(**overrides):
    values = dict(
        environment="production",
        public_operation_approved=True,
        database_path=Path("db.sqlite3"),
        index_runtime_root=Path("runtime"),
        allowed_origins=("https://cards.example",),
        cookie_secure=True,
        allow_missing_origin_for_tests=False,
        openai_api_key=None,
        embedding_model="text-embedding-3-small",
        llm_model="gpt-5.6-luna",
        bge_model_path=Path("bge"),
    )
    values.update(overrides)
    return Settings(**values)


class ConfigTest(unittest.TestCase):
    def test_production_is_explicitly_unsupported(self) -> None:
        with self.assertRaisesRegex(ValueError, "local_internal and single_process"):
            validate_settings(production())
        with self.assertRaisesRegex(ValueError, "local_internal and single_process"):
            create_app(production())

    def test_development_remains_available(self) -> None:
        self.assertEqual(validate_settings(production(environment="development")).environment, "development")


if __name__ == "__main__":
    unittest.main()
