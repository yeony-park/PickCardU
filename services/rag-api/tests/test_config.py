from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_rag_api.config import Settings, load_settings, validate_settings  # noqa: E402


def configured(environment: str = "test") -> Settings:
    return Settings(
        environment=environment,
        index_runtime_root=Path("runtime"),
        allowed_origins=("http://testserver",),
        openai_api_key=None,
        embedding_model="text-embedding-3-small",
        llm_model="gpt-5.6-luna",
        bge_model_path=Path("bge"),
    )


class ConfigTest(unittest.TestCase):
    def test_production_is_explicitly_unsupported(self) -> None:
        with self.assertRaisesRegex(ValueError, "production deployment is not configured"):
            validate_settings(configured("production"))

    def test_environment_contract_has_no_auth_or_user_database(self) -> None:
        settings = load_settings({"PICKCARDU_ENV": "test", "PICKCARDU_ALLOWED_ORIGINS": "http://testserver"})
        self.assertEqual(settings.environment, "test")
        self.assertFalse(hasattr(settings, "database_path"))
        self.assertFalse(hasattr(settings, "cookie_secure"))


if __name__ == "__main__":
    unittest.main()
