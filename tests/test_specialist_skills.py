"""Every registered specialist check must detect its own positive example."""
import asyncio
from functools import lru_cache
import json
import re
import subprocess
import sys
import textwrap

import pytest

import engineering_skills as skills
from engineering_skills import analyzers
from main import ToolRegistry


@lru_cache
def python_examples():
    raw = subprocess.check_output([sys.executable, "-I", "-m", "ruff", "rule", "--all",
                                   "--output-format", "json"])
    return {r["code"]: re.findall(r"```(?:python|pyi)\n(.*?)\n```",
                                  r["explanation"].partition("## Example")[2].partition("## References")[0], re.S)
            for r in json.loads(raw)}


@lru_cache
def sql_examples():
    from sqlfluff.core import FluffConfig, Linter
    config = FluffConfig(ignore_local_config=True, overrides={
        "rules": ",".join(analyzers.GROUP_CODES["sql"]), "dialect": "ansi", "templater": "raw"})
    examples = {}
    for rule in Linter(config=config).get_rulepack().rules:
        doc = type(rule).__doc__.partition("**Anti-pattern**")[2].partition("**Best practice**")[0]
        examples[rule.code] = [textwrap.dedent(x).strip() for x in
                               re.findall(r"\.\. code-block:: sql\s*\n\n((?:        [^\n]*\n|\n)+)", doc)]
    return examples


@pytest.mark.parametrize("name", sorted(analyzers.RULES))
def test_every_rule_detects_documented_finding(name):
    group, code, _ = analyzers.RULES[name]
    samples = sql_examples() if group == "sql" else python_examples()
    for sample in samples[code]:
        result = skills.run(name, sample)
        assert "result" in result, result
        if any(f["code"] == code for f in result["result"]["findings"]):
            break
    else:
        pytest.fail(f"{name} did not detect {code} in any published example")


@pytest.mark.parametrize("name", sorted(analyzers.REVIEWS))
def test_workflow_invokes_selected_rules(name):
    sql = name == "review_sql_quality"
    # Every workflow must return a clean empty document and a real finding.
    assert skills.run(name, "")["result"]["count"] == 0
    group = analyzers.REVIEWS[name][0].split()[0]
    code = analyzers.GROUP_CODES[group][0]
    samples = sql_examples() if sql else python_examples()
    assert any(skills.run(name, sample)["result"]["count"] > 0 for sample in samples[code])


def test_discovery_pages_cover_entire_catalog_without_overlap():
    seen = set()
    for group, expected in skills.catalog()["groups"].items():
        start = 0
        count = 0
        while start is not None:
            page = skills.catalog(group, offset=start, limit=7)
            names = set(page["skills"])
            assert names.isdisjoint(seen)
            seen.update(names)
            count += len(names)
            start = page["next_offset"]
        assert count == expected
    assert seen == set(skills.DESCRIPTIONS)
    assert len(seen) > 300
    assert len({row[1] for row in analyzers.RULES.values()}) == len(analyzers.RULES)


@pytest.mark.parametrize("args", [{"limit": False}, {"limit": 0}, {"limit": 51}, {"offset": -1}])
def test_bad_catalog_pages(args):
    assert skills.catalog("sql", **args)["error"] == "invalid_page"


def test_agent_workspace_rule(tmp_path):
    from codeagent.workspace import Workspace
    from types import SimpleNamespace
    ws = Workspace(tmp_path)
    ws.write_file("app.py", "answer = missing_value\n")
    registry = ToolRegistry(None, SimpleNamespace(ws=ws))
    match = asyncio.run(registry.dispatch("skill_catalog", {"query":"F821"}))["skills"]
    assert list(match) == ["bugs_undefined_name"]
    response = asyncio.run(registry.dispatch("skill_run", {"skill": next(iter(match)), "path":"app.py"}))
    finding, = response["result"]["findings"]
    assert (finding["line"], finding["column"], finding["code"]) == (1, 10, "F821")


def test_truncation_preserves_total():
    result = skills.run("bugs_undefined_name", "print(unknown)\n" * 90)["result"]
    assert (len(result["findings"]), result["count"], result["omitted"]) == (50, 90, 40)


@pytest.mark.parametrize(("name", "text"), [("bugs_undefined_name","def ("),
                                            ("review_sql_quality","SELECT FROM ;")])
def test_parse_failure_distinct_from_clean(name, text):
    assert skills.run(name, text)["result"]["status"] == "invalid_source"


def test_document_cannot_change_sql_configuration():
    assert skills.run("review_sql_quality","-- sqlfluff:templater:jinja\nSELECT 1")["error"] == "invalid_input"


@pytest.mark.parametrize(("exc", "error"), [(OSError(), "analyzer_unavailable"),
                                            (subprocess.TimeoutExpired("ruff", 15), "analyzer_timeout")])
def test_analyzer_failure_is_recoverable(monkeypatch, exc, error):
    def fail(*args, **kwargs):
        raise exc
    monkeypatch.setattr(subprocess, "run", fail)
    assert skills.run("review_test_quality", "pass")["error"] == error
