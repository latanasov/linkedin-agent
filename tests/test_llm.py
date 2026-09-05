"""LLM factories: provider switch between OpenRouter and a local Ollama."""

from __future__ import annotations

import pytest

from linkedin_agent.config import Settings
from linkedin_agent.llm import OPENROUTER_URL, ChatCompletionsTextLLM, make_text_llm, ollama_models


def _settings(**kw):
    return Settings(home="/tmp/x", _env_file=None, **kw)  # type: ignore[call-arg]


def test_openrouter_is_the_default_and_needs_a_key():
    s = _settings(openrouter_api_key="k")
    assert s.uses_openrouter and s.llm_ready
    llm = make_text_llm(s)
    assert isinstance(llm, ChatCompletionsTextLLM) and llm._url == OPENROUTER_URL
    assert llm._referer.startswith("https://github.com/")
    assert not _settings(openrouter_api_key="").llm_ready
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        make_text_llm(_settings(openrouter_api_key=""))


def test_ollama_needs_no_key_and_targets_the_local_endpoint():
    s = _settings(
        llm_provider="ollama", text_llm_model="qwen3.5:9b", ollama_host="http://localhost:11434/"
    )
    assert not s.uses_openrouter and s.llm_ready
    llm = make_text_llm(s)
    assert llm._url == "http://localhost:11434/v1/chat/completions"
    assert llm._model == "qwen3.5:9b" and llm._timeout == 600.0 and llm._referer == ""


async def test_text_client_parses_the_completion(monkeypatch):
    import httpx

    seen = {}

    async def fake_post(self, url, json=None, headers=None):
        seen.update(url=url, json=json, headers=headers)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  hello  "}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    llm = ChatCompletionsTextLLM("http://h/v1/chat/completions", "ollama", "m")
    assert await llm.complete("hi", temperature=0.2) == "hello"
    assert seen["json"]["model"] == "m" and seen["json"]["temperature"] == 0.2
    assert (
        seen["headers"]["Authorization"] == "Bearer ollama"
        and "HTTP-Referer" not in seen["headers"]
    )

    async def bad_post(self, url, json=None, headers=None):
        return httpx.Response(200, json={"unexpected": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", bad_post)
    with pytest.raises(RuntimeError, match="Unexpected LLM response"):
        await llm.complete("hi")


async def test_ollama_models_lists_or_none(monkeypatch):
    import httpx

    async def fake_get(self, url):
        assert url.endswith("/api/tags")
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3.5:35b"}, {"name": "gemma4:26b"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await ollama_models("http://localhost:11434") == ["qwen3.5:35b", "gemma4:26b"]

    async def down(self, url):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", down)
    assert await ollama_models("http://localhost:11434") is None


def test_browser_timeouts_default_by_provider():
    """browser-use's own 60s/120s caps are fine for a hosted model and far too short for
    a local one, so the Ollama path raises both."""
    assert _settings().browser_timeouts == (None, None)
    assert _settings(llm_provider="ollama").browser_timeouts == (600, 660)
    assert _settings(llm_provider="ollama", ollama_timeout_s=300).browser_timeouts == (300, 360)


def test_browser_timeouts_can_be_set_explicitly():
    assert _settings(browser_llm_timeout_s=90).browser_timeouts == (90, 150)
    assert _settings(browser_llm_timeout_s=90, browser_step_timeout_s=400).browser_timeouts == (
        90,
        400,
    )
    assert _settings(llm_provider="ollama", browser_step_timeout_s=900).browser_timeouts == (
        None,
        900,
    )
