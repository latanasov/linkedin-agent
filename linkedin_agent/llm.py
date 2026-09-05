"""LLM clients: a small OpenAI-compatible text client (httpx) and the browser-use model."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class ChatCompletionsTextLLM:
    """Minimal OpenAI-style chat-completions client for drafting hooks and comments.

    Works against OpenRouter and against Ollama's compatible endpoint
    (`<host>/v1/chat/completions`), which is how a local model drafts text."""

    def __init__(
        self, url: str, api_key: str, model: str, timeout: float = 60.0, referer: str = ""
    ) -> None:
        if not api_key:
            raise ValueError("no API key for the text model")
        self._url = url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._referer = referer

    async def complete(self, prompt: str, *, temperature: float = 0.7) -> str:
        payload = {
            "model": self._model,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "X-Title": "linkedin-agent"}
        if self._referer:
            headers["HTTP-Referer"] = self._referer
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
        try:
            return str(body["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"Unexpected LLM response shape: {str(body)[:200]}") from e


# Kept for callers that import the old name.
OpenRouterTextLLM = ChatCompletionsTextLLM


def make_text_llm(settings: Settings) -> ChatCompletionsTextLLM:
    if settings.text_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ValueError("LINKEDIN_AGENT_OPENROUTER_API_KEY is not set")
        return ChatCompletionsTextLLM(
            OPENROUTER_URL,
            settings.openrouter_api_key,
            settings.text_llm_model,
            referer="https://github.com/latanasov/linkedin-agent",
        )
    return ChatCompletionsTextLLM(
        settings.ollama_host.rstrip("/") + "/v1/chat/completions",
        "ollama",  # Ollama ignores the key but the header must be present
        settings.text_llm_model,
        timeout=float(settings.ollama_timeout_s),
    )


def make_browser_llm(settings: Settings) -> Any:
    """browser-use model. Imported lazily so tests never need browser_use."""
    if settings.browser_provider == "openrouter":
        from browser_use.llm.openrouter.chat import ChatOpenRouter

        if not settings.openrouter_api_key:
            raise ValueError("LINKEDIN_AGENT_OPENROUTER_API_KEY is not set")
        return ChatOpenRouter(model=settings.browser_llm_model, api_key=settings.openrouter_api_key)
    from browser_use.llm.ollama.chat import ChatOllama

    return ChatOllama(
        model=settings.browser_llm_model,
        host=settings.ollama_host,
        timeout=float(settings.ollama_timeout_s),
    )


async def ollama_models(host: str, timeout: float = 5.0) -> list[str] | None:
    """Model names a local Ollama serves, or None when it does not answer."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(host.rstrip("/") + "/api/tags")
            resp.raise_for_status()
            return [str(m.get("name", "")) for m in resp.json().get("models", [])]
    except Exception:
        return None
