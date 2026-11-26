import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import engineering_skills as skills
from engineering_skills import languages
from codeagent.workspace import Workspace
from main import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "languages"


def sample(group, mode, selector):
    if mode == "node":
        if selector == "file_scoped_namespace_declaration":
            return "namespace Example; class Demo {}"
        ext = {"rust": "rs", "go": "go", "csharp": "cs"}[group]
        return (FIXTURES / ("features." + ext)).read_text(encoding="utf8")
    if mode == "call":
        if group == "rust":
            return f"fn test() {{ {selector}(); }}"
        if group == "go":
            return f"package sample\nfunc test() {{ {selector}() }}"
        if group == "csharp":
            return f"class Demo {{ void Test() {{ {selector}(); }} }}"
        return f"function test() {{ {selector}(); }}"
    if mode == "macro":
        return f"fn main() {{ {selector}!(); }}"
    if mode == "export":
        return f"export const {selector} = () => 1;"
    if mode == "directive":
        return f'function handler() {{ "{selector}"; }}'
    if mode == "component":
        return f'import Alias from "{selector}"; export default () => <Alias />;'
    if mode == "import":
        return f'import "{selector}";'
    if mode == "config":
        return f"export default {{ {selector}: true }};"
    if mode == "member":
        return "Page.getInitialProps = async () => ({});"
    if mode == "cargo_report":
        if selector in {"warning", "error"}:
            return json.dumps({"reason": "compiler-message", "message": {"level": selector, "message": "diagnostic",
                               "spans": [{"is_primary": True, "file_name": "src/lib.rs", "line_start": 3}]}})
        return json.dumps({"reason": selector, "success": True, "filenames": ["target/example"]})
    if mode == "go_test":
        return json.dumps({"Action": "fail", "Package": "demo"} if selector == "package_fail"
                          else {"Action": selector, "Package": "demo", "Test": "TestBuild"})
    if mode == "trx":
        return f'<TestRun xmlns="urn:trx"><Counters total="1"/><UnitTestResult testName="T" outcome="{selector}"/></TestRun>'
    if mode == "package":
        return '{"scripts":{"build":"next build","dev":"next dev","start":"next start"},"dependencies":{"next":"16"}}'
    if mode == "cargo":
        return f"[{selector}]\nname = 'demo'"
    if mode == "gomod":
        return f"{selector} value"
    if mode == "msbuild":
        tag = selector.split()[0]
        return f'<Project><PropertyGroup><{tag}>sample</{tag}></PropertyGroup></Project>'
    raise AssertionError(mode)


@pytest.mark.parametrize("name", sorted(languages.RULES))
def test_every_skill_has_observable_output_through_dispatch(name):
    group, mode, selector = languages.RULES[name]
    text = sample(group, mode, selector)
    registry = ToolRegistry(None, None)
    reply = asyncio.run(registry.dispatch("skill_run", {"skill": name, "text": text}))
    result = reply["result"]
    if mode == "cargo_report":
        assert len(result) == 1
        if selector in {"warning", "error"}:
            assert result[0]["locations"][0]["line"] == 3
    elif mode == "go_test":
        assert result[0]["Package"] == "demo"
    elif mode == "trx":
        assert result[0].get("total") == "1" or result[0]["testName"] == "T"
    elif mode == "package":
        assert result == ({"dependencies": {"next": "16"}} if selector == "versions"
                          else {"script": selector, "command": "next " + selector})
    elif mode == "cargo":
        assert result == {"section": selector, "present": True, "value": {"name": "demo"}}
    elif mode == "msbuild":
        assert result[0]["value"] == "sample"
    elif mode == "gomod":
        assert result == [{"line": 1, "value": "value"}]
    else:
        assert not result["parse_errors"]
        assert result["count"] and result["matches"][0]["line"] >= 1


@pytest.mark.parametrize("group", ["rust", "go", "nextjs", "csharp"])
def test_catalog_pagination_and_counts(group):
    first = skills.catalog(group=group, limit=50)
    second = skills.catalog(group=group, offset=first["next_offset"], limit=50)
    names = set(first["skills"]) | set(second["skills"])
    assert len(names) == first["total"] == skills.catalog()["groups"][group] == 100
    assert second["next_offset"] is None
    assert all(skills.catalog(query=name, limit=1)["skills"].keys() == {name} for name in names)


def test_real_world_call_shapes_and_decoys():
    cases = [
        ("rust_iterator_collection", "fn f(){ v.iter().collect::<Vec<_>>(); /* x.collect(); */ }"),
        ("rust_shared_ownership", "fn f(){ std::sync::Arc::<u8>::new(1); Box::new(2); }"),
        ("go_error_matching", 'package p\nfunc f(){ errors.Is(a,b); mine.Is(a,b); _ = "errors.Is()" }'),
        ("nextjs_pathname_access", "import {usePathname as path} from 'next/navigation'; const p = path();"),
        ("nextjs_route_post", "const n = <number>x; export function POST() {}"),
        ("nextjs_route_get", "const h = () => 1; export {h as GET}; function GET() {}"),
        ("nextjs_client_boundaries", '"use client"; function f() { log(); "use client"; }'),
        ("csharp_query_projection", "class C {void F(){ xs.Select<int>(f); /* x.Select(); */ }}"),
    ]
    for name, text in cases:
        result = skills.run(name, text)["result"]
        assert result["count"] == 1 and not result["parse_errors"], name


def test_workspace_path_errors_and_output_limits(tmp_path):
    ws = Workspace(tmp_path)
    (tmp_path / "build.rs").write_text("fn main(){\n" + "build().unwrap();\n" * 60 + "}")
    registry = ToolRegistry(None, SimpleNamespace(ws=ws))
    result = asyncio.run(registry.dispatch("skill_run", {"skill": "rust_explicit_unwraps", "path": "build.rs"}))["result"]
    assert result["count"] == 60 and result["omitted"] == 10 and result["matches"][0]["line"] == 2
    assert skills.run("rust_dependencies", "[broken")["error"] == "invalid_input"
    assert skills.run("csharp_package_references", "<Project")["error"] == "invalid_input"
    assert skills.run("go_functions", "package p\nfunc incomplete(")["result"]["parse_errors"]
    mod = 'module example.org/demo\nrequire (\n example.org/lib v1.2.3 // indirect\n)\n'
    assert skills.run("go_dependencies", mod)["result"] == [{"line": 3, "value": "example.org/lib v1.2.3"}]


def test_report_filters_distinguish_package_failures_and_compiler_levels():
    go = '\n'.join(json.dumps(item) for item in [
        {"Action": "output", "Output": "FAIL"},
        {"Action": "fail", "Package": "example", "Test": "TestRequest"},
        {"Action": "fail", "Package": "example"},
    ])
    assert skills.run("go_failed_tests", go)["result"] == [{"Package": "example", "Test": "TestRequest"}]
    assert skills.run("go_failed_packages", go)["result"] == [{"Package": "example"}]
    warning = '{"reason":"compiler-message","message":{"level":"warning","message":"unused variable"}}'
    assert skills.run("rust_compiler_errors", warning)["result"] == []
    assert skills.run("rust_compiler_errors", "truncated json")["error"] == "invalid_input"
    assert skills.run("csharp_failed_tests", "<Project/>")["error"] == "invalid_input"
