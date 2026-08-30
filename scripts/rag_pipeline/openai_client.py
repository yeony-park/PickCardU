from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

from common import load_env_key


API_ROOT = "https://api.openai.com/v1"
MAX_GENERATION_OUTPUT_TOKENS = 1600


class OpenAIClient:
    def __init__(self, api_key: str | None = None, timeout: int = 300, max_attempts: int = 3):
        self.api_key = api_key or load_env_key("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required in the environment or .env")
        self.timeout = timeout
        self.max_attempts = max_attempts

    def post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{API_ROOT}{endpoint}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"OpenAI HTTP {error.code}: {body[-2000:]}")
                if error.code not in {408, 409, 429} and error.code < 500:
                    raise last_error from error
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
            if attempt < self.max_attempts:
                time.sleep(min(30.0, 2**attempt + random.random()))
        raise RuntimeError(f"OpenAI request failed: {last_error}")

    def embeddings(self, texts: list[str], model: str = "text-embedding-3-small") -> tuple[list[list[float]], dict[str, Any]]:
        if not texts:
            return [], {}
        response = self.post_json("/embeddings", {"model": model, "input": texts, "encoding_format": "float"})
        values = sorted(response.get("data", []), key=lambda item: item.get("index", 0))
        embeddings = [item.get("embedding", []) for item in values]
        if len(embeddings) != len(texts) or any(not embedding for embedding in embeddings):
            raise RuntimeError("Embeddings API returned an incomplete batch")
        return embeddings, response.get("usage", {})

    def structured_response(
        self,
        developer_text: str,
        user_text: str,
        schema: dict[str, Any],
        model: str = "gpt-5.6-luna",
        reasoning: str = "medium",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = {
            "model": model,
            "reasoning": {"effort": reasoning},
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": developer_text}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "grounded_card_answer",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": MAX_GENERATION_OUTPUT_TOKENS,
            "store": False,
        }
        response = self.post_json("/responses", payload)
        texts = [
            content.get("text", "")
            for item in response.get("output", [])
            if item.get("type") == "message"
            for content in item.get("content", [])
            if content.get("type") == "output_text"
        ]
        if not texts:
            raise RuntimeError("Responses API did not return output_text")
        return json.loads("\n".join(texts)), response.get("usage", {})
