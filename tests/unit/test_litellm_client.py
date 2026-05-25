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
from litemiro.models import LLMResponse


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, *, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(self, content: str | None, usage: _FakeUsage | dict[str, Any] | None) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = usage


class _Stub:
    """Records calls made to ``litellm.acompletion`` and returns canned text."""

    def __init__(
        self,
        content: str | None = "ok",
        usage: _FakeUsage | dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.usage: _FakeUsage | dict[str, Any] | None = (
            usage if usage is not None else _FakeUsage(prompt_tokens=0, completion_tokens=0)
        )
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self.content, self.usage)


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
        assert isinstance(result, LLMResponse)
        assert result.content == "hello world"

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
        assert result.content == ""


class TestUsageExtraction:
    """Token usage flows from litellm's ``usage`` field into ``LLMResponse``.

    The Phase 2 round runner reads tokens from ``LLMMeta.tokens_used``
    which is populated by ``ActionSelector`` from ``LLMResponse``; if
    this adapter loses usage on the floor every JSONL event reports
    zero spend, breaking the cost-tracking column.
    """

    async def test_usage_object_is_extracted(self, stub: _Stub) -> None:
        stub.usage = _FakeUsage(prompt_tokens=123, completion_tokens=45)
        client = LiteLLMClient(api_key="k", base_url="http://x")
        result = await client.complete(system="s", user="u", model="m")
        assert result.prompt_tokens == 123
        assert result.completion_tokens == 45

    async def test_usage_dict_is_extracted(self, stub: _Stub) -> None:
        # Some providers / older litellm versions surface usage as a
        # plain dict — the adapter has to tolerate both shapes.
        stub.usage = {"prompt_tokens": 90, "completion_tokens": 11}
        client = LiteLLMClient(api_key="k", base_url="http://x")
        result = await client.complete(system="s", user="u", model="m")
        assert result.prompt_tokens == 90
        assert result.completion_tokens == 11

    async def test_missing_usage_collapses_to_zero(self, stub: _Stub) -> None:
        stub.usage = None
        client = LiteLLMClient(api_key="k", base_url="http://x")
        result = await client.complete(system="s", user="u", model="m")
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0

    async def test_partial_usage_keeps_present_field(self, stub: _Stub) -> None:
        stub.usage = {"prompt_tokens": 42}
        client = LiteLLMClient(api_key="k", base_url="http://x")
        result = await client.complete(system="s", user="u", model="m")
        assert result.prompt_tokens == 42
        assert result.completion_tokens == 0

    async def test_garbage_usage_collapses_to_zero(self, stub: _Stub) -> None:
        # If the provider sends a string or negative number we'd rather
        # report zero than raise — the round must not die over usage.
        stub.usage = {"prompt_tokens": "not a number", "completion_tokens": -3}
        client = LiteLLMClient(api_key="k", base_url="http://x")
        result = await client.complete(system="s", user="u", model="m")
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0


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


class TestMaxOutputTokens:
    """``max_tokens`` is forwarded so OpenRouter doesn't pre-bill the full
    context window. Silent by default — only flips on when explicitly
    requested or when the env var is set."""

    async def test_omitted_when_unset(self, stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LITEMIRO_MAX_OUTPUT_TOKENS", raising=False)
        client = LiteLLMClient(api_key="k", base_url="http://x")
        await client.complete(system="s", user="u", model="m")
        assert "max_tokens" not in stub.calls[0]

    async def test_explicit_arg_forwarded(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LITEMIRO_MAX_OUTPUT_TOKENS", raising=False)
        client = LiteLLMClient(api_key="k", base_url="http://x", max_output_tokens=4096)
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["max_tokens"] == 4096

    async def test_env_var_picked_up(self, stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LITEMIRO_MAX_OUTPUT_TOKENS", "2048")
        client = LiteLLMClient(api_key="k", base_url="http://x")
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["max_tokens"] == 2048

    async def test_explicit_overrides_env(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LITEMIRO_MAX_OUTPUT_TOKENS", "999")
        client = LiteLLMClient(api_key="k", base_url="http://x", max_output_tokens=1234)
        await client.complete(system="s", user="u", model="m")
        assert stub.calls[0]["max_tokens"] == 1234

    async def test_garbage_env_falls_through(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Non-numeric or non-positive values are ignored rather than
        # raising — we'd rather let the backend pick a ceiling than
        # crash the round on a typo'd env var.
        monkeypatch.setenv("LITEMIRO_MAX_OUTPUT_TOKENS", "not a number")
        client = LiteLLMClient(api_key="k", base_url="http://x")
        await client.complete(system="s", user="u", model="m")
        assert "max_tokens" not in stub.calls[0]

    async def test_zero_env_falls_through(
        self, stub: _Stub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LITEMIRO_MAX_OUTPUT_TOKENS", "0")
        client = LiteLLMClient(api_key="k", base_url="http://x")
        await client.complete(system="s", user="u", model="m")
        assert "max_tokens" not in stub.calls[0]


def test_protocol_is_satisfied() -> None:
    assert isinstance(LiteLLMClient(api_key="k"), LLMClient)


class TestLiteLLMSmoke:
    """Goes through ``litellm.acompletion``'s real code path via its
    built-in ``mock_response`` short-circuit. The monkeypatched suite
    above only checks our own logic — this test catches SDK kwarg or
    response-shape drift in litellm itself, which would otherwise only
    surface at runtime against a real provider.
    """

    async def test_response_shape_matches_adapter_assumptions(self) -> None:
        from litemiro.llm.litellm_client import _extract_usage  # noqa: PLC0415

        response = await litellm.acompletion(
            model="openrouter/anthropic/claude-3.5-sonnet",
            messages=[{"role": "user", "content": "hi"}],
            mock_response="hello",
        )
        # The two response paths LiteLLMClient.complete() reads:
        assert response.choices[0].message.content == "hello"
        prompt, completion = _extract_usage(response)
        assert prompt >= 0
        assert completion >= 0
