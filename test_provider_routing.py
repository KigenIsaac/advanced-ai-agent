"""Regression tests for locally hosted, multi-provider API routing."""

from types import SimpleNamespace

import pytest

from llm_providers import get_provider_adapter
from llm_providers.openai import OpenAICompatibleProvider
from main import (
    parse_args,
    resolve_provider_api_keys,
    resolve_runtime_settings,
)


@pytest.mark.parametrize(
    ("provider", "model", "url"),
    [
        ("deepseek", "deepseek-flash", "https://api.deepseek.com"),
        ("qwen", "qwen3.7-plus", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        ("kimi", "kimi-k2.5", "https://api.moonshot.ai/v1"),
    ],
)
def test_provider_uses_its_own_default_endpoint(monkeypatch, provider, model, url):
    monkeypatch.delenv("AGNES_BASE_URL", raising=False)
    monkeypatch.delenv("AGNES_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    monkeypatch.delenv("MOONSHOT_BASE_URL", raising=False)
    settings = resolve_runtime_settings(parse_args(["--provider", provider]))
    assert settings["provider"] == provider
    assert settings["model"] == model
    assert settings["base_url"] == url


@pytest.mark.parametrize("provider", ["qwen", "kimi"])
def test_new_providers_use_existing_openai_compatible_adapter(provider):
    adapter = get_provider_adapter(SimpleNamespace(provider=provider))
    assert isinstance(adapter, OpenAICompatibleProvider)


def test_qwen_prefers_workspace_specific_endpoint(monkeypatch):
    url = "https://ws123.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    monkeypatch.setenv("QWEN_BASE_URL", url)
    monkeypatch.setenv("AGNES_BASE_URL", "https://wrong.example/v1")
    settings = resolve_runtime_settings(parse_args(["--provider", "qwen"]))
    assert settings["base_url"] == url
    settings = resolve_runtime_settings(
        parse_args(["--provider", "qwen", "--base-url", "https://custom.example/v1"])
    )
    assert settings["base_url"] == "https://custom.example/v1"


def test_provider_api_keys_are_not_mixed(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-only")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-only")
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-only")
    monkeypatch.setenv("AGNES_API_KEY", "generic-fallback")
    assert resolve_provider_api_keys("deepseek") == ["deepseek-only"]
    assert resolve_provider_api_keys("qwen") == ["qwen-only"]
    assert resolve_provider_api_keys("kimi") == ["kimi-only"]
    assert resolve_provider_api_keys("openrouter") == ["generic-fallback"]
    assert resolve_provider_api_keys("deepseek", repeated=["explicit"]) == ["explicit"]
    assert resolve_provider_api_keys("qwen", many="one,two") == ["one", "two"]


def test_missing_specific_key_can_fall_back(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("AGNES_API_KEY", "fallback")
    assert resolve_provider_api_keys("qwen") == ["fallback"]
