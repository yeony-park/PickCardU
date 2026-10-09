from __future__ import annotations

import sys
import subprocess
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
    def test_payload_budget_overrides_and_invalid_values_stop_startup(self):
        for invalid in ('0', '-1', 'abc', '1.5', ''):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                load_settings({'PICKCARDU_ANSWER_PAYLOAD_BYTES': invalid})
        default = load_settings({})
        self.assertEqual(default.answer_payload_bytes, 64000)
        custom = load_settings({'PICKCARDU_ANSWER_PAYLOAD_BYTES': '96000'})
        self.assertEqual(custom.answer_payload_bytes, 96000)

    def test_chat_models_import_without_starting_app_or_creating_database(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; sys.path[:0] = ['services/rag-api/src', 'packages/rag-core/src']; "
            "import pickcardu_rag_api.chat_models; assert 'pickcardu_rag_api.main' not in sys.modules"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_is_explicitly_unsupported(self) -> None:
        with self.assertRaisesRegex(ValueError, "production deployment is not configured"):
            validate_settings(configured("production"))

    def test_environment_contract_has_separate_chat_database_without_account_auth(self) -> None:
        settings = load_settings({"PICKCARDU_ENV": "test", "PICKCARDU_ALLOWED_ORIGINS": "http://testserver"})
        self.assertEqual(settings.environment, "test")
        self.assertFalse(hasattr(settings, "database_path"))
        self.assertFalse(hasattr(settings, "cookie_secure"))
        self.assertEqual(settings.chat_db_path, ROOT / 'data/chat/runtime/chat.sqlite')
        overridden = load_settings({'PICKCARDU_CHAT_DB_PATH': '/tmp/pickcardu-test-chat.sqlite'})
        self.assertEqual(overridden.chat_db_path, Path('/tmp/pickcardu-test-chat.sqlite'))


if __name__ == "__main__":
    unittest.main()
