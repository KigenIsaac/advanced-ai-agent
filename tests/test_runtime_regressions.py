"""Acceptance cases for existing entry points. Also runnable on the base revision."""
import asyncio
import json
import shlex
from unittest.mock import AsyncMock

import httpx
import pytest

import main
from codeagent.testing import Testing as Runner
from codeagent.workspace import Workspace, WorkspaceError
from llm_providers.base import ProviderAdapter


@pytest.mark.parametrize("raw", [
    "[]", '"string"', "null", "5", "true", '{"x":NaN}', '{"x":1e999}', "{2: 'text'}",
    {"wrong_type": {1, 2}}, {"cycle_like_value": object()}, "{'x': {1, 2}}",
    {"not_finite": float("inf")}, '{"x":' + "[" * 1200 + "0" + "]" * 1200 + "}",
], ids=["array", "string", "null", "number", "boolean", "nan", "overflow", "nonstring-key",
        "set-value", "opaque-value", "literal-set", "native-infinity", "excess-depth"])
def test_invalid_arguments_are_recoverable(raw):
    parsed, error = main._parse_tool_arguments("tool", raw)
    assert parsed == {} and error


def test_native_arguments():
    assert main._parse_tool_arguments("tool", {"input": "ok"}) == ({"input": "ok"}, None)


@pytest.mark.parametrize("raw", [None, 7, {"function": "incorrect"}])
def test_malformed_history_call_skipped(raw):
    msg = {"role": "assistant", "tool_calls": [raw]}
    assert main._normalize_message_for_api(msg)["tool_calls"] == []


def assistant(*ids):
    return {"role": "assistant", "content": "progress", "tool_calls": [{"id": i} for i in ids]}


def reply(i):
    return {"role": "tool", "tool_call_id": i, "content": "ok"}


@pytest.mark.parametrize(("source", "expected"), [
    ([None], []),
    ([assistant("a"), reply("a"), reply("a")], [assistant("a"), reply("a")]),
    ([assistant("a"), reply("x"), reply("a")], [assistant("a"), reply("a")]),
    ([assistant("a"), reply("a"), reply("x")], [assistant("a"), reply("a")]),
    ([assistant("a", "b"), reply("a")], [{"role": "assistant", "content": "progress"}]),
    ([assistant("a", "a"), reply("a")], [{"role": "assistant", "content": "progress"}]),
    ([assistant(None)], [{"role": "assistant", "content": "progress"}]),
], ids=["malformed-message", "duplicate-result", "unknown-before", "unknown-after",
        "incomplete-block", "duplicate-call-id", "missing-call-id"])
def test_history_repairs(source, expected):
    assert main._repair_tool_call_history(source) == expected


@pytest.mark.parametrize("value", ["inf", "NaN", "Wed, 21 Oct 2015 07:28:00 GMT"])
def test_retry_header(value):
    response = httpx.Response(429, headers={"retry-after": value})
    expected = 0 if value.endswith("GMT") else 30
    for parser in (ProviderAdapter._parse_retry_after, main.AgnesClient._parse_retry_after):
        assert parser(response) == expected


@pytest.mark.parametrize(("marker", "content", "expected"), [
    ("Cargo.toml", "", "cargo"), ("go.mod", "", "go"),
    ("package.json", '{"devDependencies":{"vitest":"1"}}', "vitest"),
    ("package.json", '{"dependencies":{"jest":"1"}}', "jest"),
    ("package.json", '{"devDependencies":{"mocha":"1"}}', "mocha"),
])
def test_generic_tests_folder_respects_language(tmp_path, marker, content, expected):
    (tmp_path / "tests").mkdir()
    (tmp_path / marker).write_text(content)
    assert Runner(Workspace(tmp_path), None).framework() == expected


def test_top_level_pytest_file(tmp_path, monkeypatch):
    (tmp_path / "sample_test.py").touch()
    monkeypatch.setattr("shutil.which", lambda name: "/bin/pytest")
    assert Runner(Workspace(tmp_path), None).framework() == "pytest"


@pytest.mark.parametrize(("framework", "path", "name", "expected"), [
    ("unittest", None, None, ["python", "-m", "unittest"]),
    ("pytest", "tests/a b.py", None, ["pytest", "-v", "tests/a b.py"]),
    ("pytest", "tests", "login", ["pytest", "-v", "tests", "-k", "login"]),
    ("vitest", "test.js", "hello", ["npx", "vitest", "run", "-t", "hello", "test.js"]),
    ("jest", None, "it's ok", ["npx", "jest", "-t", "it's ok"]),
    ("mocha", None, "a b", ["npx", "mocha", "--grep", "a b"]),
    ("cargo", None, "two words", ["cargo", "test", "two words"]),
    ("go", "./pkg", "^one$", ["go", "test", "-run", "^one$", "./pkg"]),
])
def test_test_command_arguments(framework, path, name, expected):
    assert shlex.split(Runner._command(framework, path, name, None)) == expected


@pytest.mark.parametrize(("framework", "file"), [("jest", "component.spec.tsx"),
                          ("vitest", "unit.test.mjs"), ("vitest", "widget.test.jsx")])
def test_discover_supported_suffix(tmp_path, framework, file):
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {framework: "1"}}))
    (tmp_path / file).touch()
    assert Runner(Workspace(tmp_path), None).discover() == [file]


def test_workspace_under_ignored_parent(tmp_path):
    ws = Workspace(tmp_path / "build" / "myproject")
    ws.write_file("src.py", "pass")
    assert ws.list_files()[0]["path"] == "src.py"


@pytest.mark.parametrize("end", [0, -1])
def test_empty_range(tmp_path, end):
    ws = Workspace(tmp_path)
    ws.write_file("x", "a\nb")
    assert ws.read_file("x", end_line=end)["lines"] == 0


@pytest.mark.parametrize(("old", "count"), [("", 1), ("a", 0), ("a", -2), ("a", True)])
def test_ambiguous_edits_leave_file_intact(tmp_path, old, count):
    ws = Workspace(tmp_path)
    ws.write_file("x", "a")
    with pytest.raises(WorkspaceError):
        ws.edit_file("x", old, "b", count)
    assert (tmp_path / "x").read_text() == "a"


def test_actual_edit_count(tmp_path):
    ws = Workspace(tmp_path)
    ws.write_file("x", "a")
    assert ws.edit_file("x", "a", "b", 5)["replaced"] == 1


@pytest.mark.parametrize("case", ["disabled", "unknown", "non-object"])
def test_registry_refuses_invalid_dispatch(case):
    code = AsyncMock()
    registry = main.ToolRegistry(None, code)
    name = next(iter(main._CODE_TOOLS))["function"]["name"]
    if case == "disabled":
        registry.disable(name)
    if case == "unknown":
        name = "missing"
    args = [] if case == "non-object" else {}
    result = asyncio.run(registry.dispatch(name, args))
    assert isinstance(result, dict) and "error" in result
    code.dispatch.assert_not_awaited()
