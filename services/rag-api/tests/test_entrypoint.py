from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pickcardu_rag_api import __main__ as entrypoint


class EntrypointTests(unittest.TestCase):
    def test_local_env_fills_missing_values_without_overriding_shell(self) -> None:
        loader = getattr(entrypoint, "load_local_environment", None)
        self.assertTrue(callable(loader), "FastAPI entrypoint needs a local .env loader")
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "OPENAI_API_KEY=from-file\nPICKCARDU_LLM_MODEL=from-file-model\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OPENAI_API_KEY": "from-shell"}, clear=True):
                loaded = loader(env_file)

                self.assertTrue(loaded)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "from-shell")
                self.assertEqual(os.environ["PICKCARDU_LLM_MODEL"], "from-file-model")


if __name__ == "__main__":
    unittest.main()
