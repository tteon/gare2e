"""Request preservation at the synchronous and asynchronous provider boundary."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterator
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from seocho.store.llm import (
    LLMResponse,
    OpenAICompatibleBackend,
    VLLMBackend,
    complete_with_task_hints,
)
from seocho.tracing import disable_tracing


def _response(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        model=kwargs["model"],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5),
    )


@pytest.fixture(autouse=True)
def clients(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Mock]]:
    disable_tracing()
    creates = {
        "sync": Mock(side_effect=_response),
        "async": AsyncMock(side_effect=_response),
    }
    module = ModuleType("openai")
    module.OpenAI = Mock(
        return_value=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=creates["sync"]))
        )
    )
    module.AsyncOpenAI = Mock(
        return_value=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=creates["async"]))
        )
    )
    monkeypatch.setitem(sys.modules, "openai", module)
    yield creates
    disable_tracing()


@pytest.fixture(params=["sync", "async"])
def completion(
    request: pytest.FixtureRequest, clients: dict[str, Mock]
) -> tuple[Callable[..., LLMResponse], Mock]:
    def invoke(backend: OpenAICompatibleBackend, **kwargs: Any) -> LLMResponse:
        if request.param == "async":
            return asyncio.run(backend.acomplete(**kwargs))
        return backend.complete(**kwargs)

    return invoke, clients[request.param]


@pytest.fixture
def controls() -> dict[str, Any]:
    return {
        "system": "test-system",
        "user": "test-user",
        "temperature": 0.25,
        "max_tokens": 97,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "TestResult",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            },
        },
        "reasoning_mode": True,
        "task_hint": "graph_cot",
        "mode": "pipeline",
        "model": "test-override",
        "provider_options": {
            "thinking": {"type": "enabled"},
            "prompt_cache_key": "test-cache",
            "cache_salt": "test-salt",
        },
    }


@pytest.mark.parametrize("backend_type", [OpenAICompatibleBackend, VLLMBackend])
@pytest.mark.parametrize("policy", ["", "STRICT", "other", None, True])
def test_rejects_invalid_request_policy(
    backend_type: type[OpenAICompatibleBackend], policy: Any
) -> None:
    with pytest.raises(ValueError, match="request_policy"):
        backend_type(model="test-model", api_key="test-key", request_policy=policy)


@pytest.mark.parametrize("provider", ["deepseek", "openai", "vllm"])
def test_strict_success_retains_generation_controls(
    completion: tuple[Callable[..., LLMResponse], Mock],
    controls: dict[str, Any],
    provider: str,
) -> None:
    invoke, create = completion
    if provider == "vllm":
        backend = VLLMBackend(
            model="test-model", api_key="test-key", request_policy="strict"
        )
    else:
        backend = OpenAICompatibleBackend(
            provider=provider,
            model="gpt-5-test" if provider == "openai" else "test-model",
            api_key="test-key",
            request_policy="strict",
        )

    result = invoke(backend, **controls)

    assert result.json() == {"ok": True}
    assert result.model == "test-override"
    assert result.usage["total_tokens"] == 5
    expected = {
        "model": "test-override",
        "messages": [
            {"role": "system", "content": "test-system"},
            {"role": "user", "content": "test-user"},
        ],
        "response_format": controls["response_format"],
        "extra_body": {
            "thinking": {"type": "enabled"},
            "prompt_cache_key": "test-cache",
            "cache_salt": "test-salt",
        },
        "max_completion_tokens" if provider == "openai" else "max_tokens": 97,
    }
    if provider == "deepseek":
        expected["reasoning_effort"] = "max"
    elif provider == "vllm":
        expected["temperature"] = 0.25
    create.assert_called_once_with(**expected)


@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        (TypeError, "create() got an unexpected keyword argument 'reasoning_effort'"),
        (TypeError, "create() got an unexpected keyword argument 'extra_body'"),
        (RuntimeError, "response_format is not supported"),
        (ConnectionError, "test connection failure"),
    ],
)
def test_strict_propagates_provider_failures_without_fallback(
    completion: tuple[Callable[..., LLMResponse], Mock],
    controls: dict[str, Any],
    error_type: type[Exception],
    message: str,
) -> None:
    invoke, create = completion
    backend = OpenAICompatibleBackend(
        provider="deepseek", api_key="test-key", request_policy="strict"
    )
    error = error_type(message)
    create.side_effect = [error, _response(model="test-override")]

    with pytest.raises(error_type) as caught:
        invoke(backend, **controls)

    assert caught.value is error
    create.assert_called_once()
    sent = create.call_args.kwargs
    assert sent["reasoning_effort"] == "max"
    assert sent["extra_body"] == controls["provider_options"]
    assert sent["response_format"] == controls["response_format"]
    assert sent["max_tokens"] == 97
    assert sent["messages"][0]["content"] == "test-system"


@pytest.mark.parametrize("provider", ["deepseek", "openai", "vllm"])
def test_strict_json_truncation_never_boosts_budget(
    completion: tuple[Callable[..., LLMResponse], Mock],
    controls: dict[str, Any],
    provider: str,
) -> None:
    invoke, create = completion
    backend = OpenAICompatibleBackend(
        provider=provider,
        model="gpt-5-test" if provider == "openai" else "test-model",
        api_key="test-key",
        request_policy="strict",
    )
    error = RuntimeError(
        "Model did not output valid JSON. The output was truncated before a complete JSON"
    )
    create.side_effect = [error, _response(model="test-override")]

    with pytest.raises(RuntimeError) as caught:
        invoke(backend, **controls)

    assert caught.value is error
    create.assert_called_once()
    sent = create.call_args.kwargs
    token_key = "max_completion_tokens" if provider == "openai" else "max_tokens"
    assert sent[token_key] == 97
    assert sent["response_format"] == controls["response_format"]
    assert sent["extra_body"] == controls["provider_options"]
    assert sent["messages"][0]["content"] == "test-system"


@pytest.mark.parametrize("policy_kwargs", [{}, {"request_policy": "compatible"}])
def test_compatible_keeps_provider_and_prompt_fallbacks(
    completion: tuple[Callable[..., LLMResponse], Mock],
    controls: dict[str, Any],
    policy_kwargs: dict[str, str],
) -> None:
    invoke, create = completion
    backend = OpenAICompatibleBackend(
        provider="deepseek", api_key="test-key", **policy_kwargs
    )

    def reject_controls(**kwargs: Any) -> SimpleNamespace:
        if any(
            key in kwargs
            for key in ("extra_body", "reasoning_effort", "response_format")
        ):
            raise RuntimeError("unsupported request controls")
        return _response(**kwargs)

    create.side_effect = reject_controls
    result = invoke(backend, **controls)

    assert result.json() == {"ok": True}
    assert create.call_count == 4
    sent = [call.kwargs for call in create.call_args_list]
    assert sent[0]["extra_body"] == controls["provider_options"]
    assert sent[0]["reasoning_effort"] == "max"
    assert sent[0]["response_format"] == controls["response_format"]
    assert "extra_body" not in sent[1]
    assert "reasoning_effort" not in sent[1]
    assert sent[1]["response_format"] == controls["response_format"]
    assert "response_format" not in sent[2]
    assert sent[2]["extra_body"] == controls["provider_options"]
    assert sent[-1]["messages"][0]["content"] == (
        "test-system\n\nReturn ONLY valid JSON."
    )
    assert all(call["max_tokens"] == 97 for call in sent)


def test_compatible_json_truncation_still_boosts_budget(
    completion: tuple[Callable[..., LLMResponse], Mock], controls: dict[str, Any]
) -> None:
    invoke, create = completion
    backend = OpenAICompatibleBackend(provider="deepseek", api_key="test-key")
    controls["max_tokens"] = 8192
    create.side_effect = [
        RuntimeError("Model did not output valid JSON"),
        _response(model="test-override"),
    ]

    result = invoke(backend, **controls)

    assert result.json() == {"ok": True}
    assert create.call_count == 2
    first, retry = [call.kwargs for call in create.call_args_list]
    assert first["max_tokens"] == 8192
    assert retry == {**first, "max_tokens": 16384}


@pytest.mark.parametrize(
    "offender",
    [
        "reasoning_mode",
        "task_hint",
        "mode",
        "model",
        "provider_options",
        "max_tokens",
        None,
    ],
)
def test_task_hint_helper_never_drops_controls_for_strict_backend(
    clients: dict[str, Mock], controls: dict[str, Any], offender: str | None
) -> None:
    backend = OpenAICompatibleBackend(
        provider="deepseek", api_key="test-key", request_policy="strict"
    )
    message = "unexpected keyword argument"
    if offender is not None:
        message += f" '{offender}'"
    error = TypeError(message)
    clients["sync"].side_effect = error

    with pytest.raises(TypeError) as caught:
        complete_with_task_hints(backend, **controls)

    assert caught.value is error
    clients["sync"].assert_called_once()
    sent = clients["sync"].call_args.kwargs
    assert sent["model"] == "test-override"
    assert sent["max_tokens"] == 97
    assert sent["reasoning_effort"] == "max"
    assert sent["extra_body"] == controls["provider_options"]
    assert sent["response_format"] == controls["response_format"]
    assert sent["messages"][0]["content"] == "test-system"


def test_task_hint_helper_keeps_legacy_compatibility(controls: dict[str, Any]) -> None:
    class LegacyBackend:
        def complete(
            self, *, system: str, user: str, temperature: float
        ) -> LLMResponse:
            return LLMResponse(text="test-result", model="legacy-model")

    controls.pop("response_format")
    result = complete_with_task_hints(LegacyBackend(), **controls)

    assert result.text == "test-result"
    assert result.model == "legacy-model"
