"""TDD spec for ``litemiro.llm.litellm_client.LiteLLMClient``.

The adapter is deliberately thin — retry / JSON repair / fallback
sit inside ``ActionSelector`` already. The suite uses ``monkeypatch``
to swap ``litellm.acompletion`` for an inspectable async stub so we
never make real network calls during the unit gate.
"""

from __future__ import annotations

from typing import Any

import litellm
import pytest

from litemiro.interfaces import LLMClient
from litemiro.llm.litellm_client import LiteLLMClient


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _Stub:
    """Records calls made to ``litellm.acompletion`` and returns canned text."""

    def __init__(self, content: str | None = "ok") -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self.content)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> _Stub:
    s = _Stub()
    monkeypatch.setattr(litellm, "acompletion", s)
    return s


class TestComplete:
    async def test_returns_message_content(self, stub: _Stub) -> None:
        stub.content = "hello world"
        client = LiteLLMClient(api_key="k", base_url="http://x")
        result = await client.complete(system="s", user="u", model="m")
        assert result == "hello world"

    async def test_messages_have_system_and_user(self, stub: _Stub) -> None:
        client = LiteLLMClient(api_key="k", base_url="http://x")
        await client.complete(system="SYS", user="USR", model="m")
        assert stub.calls[0]["messages"] == [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USR"},
        ]

    async def test_model_name_is_forwarded(self, stub: _Stub) -> None:
        client = LiteLLMClient(api_key="k", base_url="http://x")
        await client.complete(system="s", user="u", model="openrouter/claude")
        assert stub.calls[0]["model"] == "openrouter/claude"

    async def test_explicit_credentials_forwarded(self, stub: _Stub) -> None:
        client = LiteLLMClient(api_key="explicit-key", base_url="http://explicit")
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["api_key"] == "explicit-key"
        assert stub.calls[0]["base_url"] == "http://explicit"

    async def test_timeout_forwarded(self, stub: _Stub) -> None:
        client = LiteLLMClient(api_key="k", base_url="http://x", timeout_seconds=12.5)
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["timeout"] == 12.5

    async def test_none_content_becomes_empty_string(self, stub: _Stub) -> None:
        stub.content = None
        client = LiteLLMClient(api_key="k", base_url="http://x")
        result = await client.complete(system="s", user="u", model="m")
        assert result == ""


class TestEnvironmentFallback:
    async def test_api_key_falls_back_to_env(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
        monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
        client = LiteLLMClient()
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["api_key"] == "env-key"

    async def test_base_url_falls_back_to_env(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_BASE_URL", "http://env-base")
        client = LiteLLMClient(api_key="k")
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["base_url"] == "http://env-base"

    async def test_base_url_default_is_openrouter(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
        client = LiteLLMClient(api_key="k")
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["base_url"] == "https://openrouter.ai/api/v1"

    async def test_explicit_overrides_env(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
        monkeypatch.setenv("OPENROUTER_BASE_URL", "http://env-base")
        client = LiteLLMClient(api_key="explicit", base_url="http://explicit")
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["api_key"] == "explicit"
        assert stub.calls[0]["base_url"] == "http://explicit"


def test_protocol_is_satisfied() -> None:
    assert isinstance(LiteLLMClient(api_key="k"), LLMClient)
