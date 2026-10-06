import pytest

from codeagent.testing import Testing
from codeagent.workspace import PathTraversal, Workspace
from llm_providers.base import ProviderAdapter


def test_workspace_round_trip_and_symbol_discovery(tmp_path):
    ws = Workspace(tmp_path)

    ws.write_file("src/example.py", "class Demo:\n    def run(self):\n        return 42\n")

    result = ws.read_file("src/example.py")
    assert "return 42" in result["content"]

    symbols = ws.find_symbol("run")
    assert len(symbols) == 1
    assert symbols[0]["kind"] == "function"
    assert symbols[0]["file"] == "src/example.py"


def test_workspace_blocks_path_traversal(tmp_path):
    ws = Workspace(tmp_path)

    with pytest.raises(PathTraversal):
        ws.read_file("../outside.txt")


def test_workspace_search_finds_matching_code(tmp_path):
    ws = Workspace(tmp_path)
    ws.write_file("a.py", "answer = 42\n")
    ws.write_file("b.py", "other = 1\n")

    hits = ws.search_code("answer")

    assert len(hits) == 1
    assert hits[0]["file"] == "a.py"


@pytest.mark.parametrize(
    ("framework", "path", "test_name", "expected"),
    [
        ("pytest", None, None, "pytest -v"),
        ("pytest", "tests/test_api.py", None, "pytest -v tests/test_api.py"),
        ("jest", None, "auth works", "npx jest -t 'auth works'"),
        ("vitest", "tests/api.test.ts", None, "npx vitest run tests/api.test.ts"),
        ("cargo", None, "unit_test", "cargo test unit_test"),
        ("go", None, None, "go test ./..."),
    ],
)
def test_testing_builds_expected_commands(framework, path, test_name, expected):
    assert Testing._command(framework, path, test_name, None) == expected


def test_testing_extracts_pytest_failures():
    output = """============================= test session starts =============================
FAILED tests/test_api.py::test_login - AssertionError
ERROR tests/test_db.py::test_connection - RuntimeError
"""
    failures = Testing.__new__(Testing)._extract_failures(output, "pytest")

    assert failures == [
        {
            "test": "tests/test_api.py::test_login",
            "status": "FAILED",
            "message": "AssertionError",
        },
        {
            "test": "tests/test_db.py::test_connection",
            "status": "ERROR",
            "message": "RuntimeError",
        },
    ]


def test_provider_adapter_normalizes_tool_arguments():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": {"query": "gold"}},
                }
            ],
        }
    ]

    normalized = ProviderAdapter.normalize_messages(messages)

    assert normalized[0]["tool_calls"][0]["function"]["arguments"] == '{"query": "gold"}'


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5", 5.0),
        ("0", 0.0),
        ("invalid", 30.0),
        (None, None),
    ],
)
def test_provider_adapter_parses_retry_after(raw, expected):
    import httpx

    response = httpx.Response(
        429,
        headers={} if raw is None else {"retry-after": raw},
    )

    assert ProviderAdapter._parse_retry_after(response) == expected
