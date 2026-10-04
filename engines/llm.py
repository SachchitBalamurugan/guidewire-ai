"""Small language model client.

Default is a local Ollama model (qwen2.5:3b), so suggestions and insights stay
on Noor's machine. Gemini is an optional fallback for operators with a
connection; "rules" runs with no model and leaves the coach on its built-in
templates.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

from config import Settings


class LLM(Protocol):
    name: str

    async def available(self) -> bool: ...

    async def complete_json(self, system: str, user: str, max_tokens: int = 400) -> dict[str, Any] | None: ...


class OllamaLLM:
    def __init__(self, url: str, model: str, timeout_s: float = 25.0):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.name = f"ollama:{model}"
        self._available: bool | None = None

    async def available(self) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.url}/api/tags")
                response.raise_for_status()
                models = [m.get("name", "") for m in response.json().get("models", [])]
                base = self.model.split(":")[0]
                self._available = any(m == self.model or m.split(":")[0] == base for m in models)
        except Exception:
            self._available = False
        return bool(self._available)

    async def complete_json(self, system: str, user: str, max_tokens: int = 400) -> dict[str, Any] | None:
        import httpx

        body = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "keep_alive": "30m",
            "options": {"temperature": 0.3, "num_predict": max_tokens, "num_ctx": 4096},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.post(f"{self.url}/api/chat", json=body)
                response.raise_for_status()
                content = response.json().get("message", {}).get("content", "")
        except Exception:
            return None
        return parse_json(content)


class GeminiLLM:
    def __init__(self, api_key: str, model: str, timeout_s: float = 25.0):
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.name = f"gemini:{model}"

    async def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import google.genai  # noqa: F401
        except ImportError:
            return False
        return True

    async def complete_json(self, system: str, user: str, max_tokens: int = 400) -> dict[str, Any] | None:
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return None

        def _call() -> str:
            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    temperature=0.3,
                    max_output_tokens=max_tokens,
                ),
            )
            return response.text or ""

        try:
            content = await asyncio.wait_for(asyncio.to_thread(_call), self.timeout_s)
        except Exception:
            return None
        return parse_json(content)


class RulesLLM:
    """No model. Every call returns None, so callers use their templates."""

    name = "rules"

    async def available(self) -> bool:
        return True

    async def complete_json(self, system: str, user: str, max_tokens: int = 400) -> dict[str, Any] | None:
        return None


class FakeLLM:
    name = "fake"

    def __init__(self, replies: list[dict[str, Any] | None] | None = None):
        self.replies = list(replies or [])
        self.prompts: list[tuple[str, str]] = []

    async def available(self) -> bool:
        return True

    async def complete_json(self, system: str, user: str, max_tokens: int = 400) -> dict[str, Any] | None:
        self.prompts.append((system, user))
        return self.replies.pop(0) if self.replies else None


def build_llm(settings: Settings) -> LLM:
    if settings.llm_backend == "gemini" and settings.gemini_api_key:
        return GeminiLLM(settings.gemini_api_key, settings.gemini_model, settings.llm_timeout_s)
    if settings.llm_backend == "rules":
        return RulesLLM()
    return OllamaLLM(settings.ollama_url, settings.ollama_model, settings.llm_timeout_s)


def parse_json(content: str) -> dict[str, Any] | None:
    """Small models wrap JSON in prose or code fences now and then."""

    content = (content or "").strip()
    if not content:
        return None
    try:
        value = json.loads(content)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None
