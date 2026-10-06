import argparse
import json

from main import (
    DEFAULT_MODEL,
    _repair_tool_call_history,
    load_agent_config,
    resolve_runtime_settings,
)


def test_load_agent_config_reads_json_settings(tmp_path):
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "agent_name": "My Custom AI",
                "agent_tagline": "Helpful helper",
                "base_url": "https://example.com/v1",
                "model": "custom-model-v1",
                "custom_instructions": "Be concise and friendly.",
                "stream": False,
            }
        ),
        encoding="utf-8",
    )

    config = load_agent_config(str(config_path))

    assert config["agent_name"] == "My Custom AI"
    assert config["model"] == "custom-model-v1"
    assert config["stream"] is False
    assert config["custom_instructions"] == "Be concise and friendly."


def test_resolve_runtime_settings_prefers_cli_over_profile(tmp_path):
    config_path = tmp_path / "agent.json"
    config_path.write_text(
        json.dumps(
            {
                "agent_name": "Profile Bot",
                "model": "profile-model",
                "base_url": "https://profile.example.com/v1",
                "temperature": 0.7,
                "stream": False,
            }
        ),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        config=str(config_path),
        project=None,
        home=None,
        api_key=None,
        api_keys=None,
        base_url="https://cli.example.com/v1",
        model="cli-model",
        temperature=None,
        max_tokens=None,
        max_rounds=None,
        tier=None,
        rpm=None,
        max_retries=None,
        swarm_workers=None,
        swarm=False,
        thinking=None,
        no_stream=False,
        verbose=None,
        show_browser=None,
        no_web=False,
        no_code=False,
        agent_name="CLI Bot",
        agent_tagline="CLI override",
        custom_instructions=None,
    )

    settings = resolve_runtime_settings(args)

    assert settings["agent_name"] == "CLI Bot"
    assert settings["agent_tagline"] == "CLI override"
    assert settings["base_url"] == "https://cli.example.com/v1"
    assert settings["model"] == "cli-model"
    assert settings["temperature"] == 0.7
    assert settings["stream"] is False
    assert settings["custom_instructions"] in (None, "")


def test_resolve_runtime_settings_defaults_to_library_values():
    args = argparse.Namespace(
        config=None,
        project=None,
        home=None,
        api_key=None,
        api_keys=None,
        base_url=None,
        model=None,
        temperature=None,
        max_tokens=None,
        max_rounds=None,
        tier=None,
        rpm=None,
        max_retries=None,
        swarm_workers=None,
        swarm=False,
        thinking=None,
        no_stream=None,
        verbose=None,
        show_browser=None,
        no_web=False,
        no_code=False,
        agent_name=None,
        agent_tagline=None,
        custom_instructions=None,
    )

    settings = resolve_runtime_settings(args)

    assert settings["base_url"]
    assert settings["model"] == DEFAULT_MODEL
    assert settings["agent_name"] == "Agnes Agent"


def test_repair_tool_call_history_removes_orphans():
    orphaned_tool_call = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_123", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
            ],
        },
        {"role": "user", "content": "next"},
    ]

    assert _repair_tool_call_history(orphaned_tool_call) == [
        {"role": "user", "content": "hello"},
        {"role": "user", "content": "next"},
    ]

    orphaned_tool_response = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "tool_call_id": "call_123", "content": "done"},
        {"role": "user", "content": "next"},
    ]

    assert _repair_tool_call_history(orphaned_tool_response) == [
        {"role": "user", "content": "hello"},
        {"role": "user", "content": "next"},
    ]

    valid = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_123", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": "done"},
        {"role": "user", "content": "next"},
    ]

    assert _repair_tool_call_history(valid) == valid
