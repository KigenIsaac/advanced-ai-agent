import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import engineering_skills as skills
from main import Agent, ToolRegistry


PYTHON = """from .model import Foo
class Box:
    pass
async def work(*, items=[]):
    try:
        assert items
    except:
        pass
"""
JSON = '{"z":null,"a/b":{"~x":[]},"yes":true,"n":1}'
CSV = 'id,note\r\n01,"a,b"\r\n01,"a,b"\r\n02,\r\n'
TEXT = 'Hi\r\n\tX \rHi\n<<<<<<< branch'
INI = '[DEFAULT]\nColor=blue\n[app]\nEmpty=\nMulti=line1\n line2\n[dev]\n'
PACKAGE = '{"scripts":{"pretest":"prep","test":"go"},"dependencies":{"x":"1"},"devDependencies":{"x":"2"},"peerDependencies":{"y":"3"},"engines":{"node":">=18"},"workspaces":{"packages":["lib/*"]}}'
JUNIT = """<testsuites xmlns="urn:test"><testsuite>
<testcase name="a" classname="C" time=".5"/>
<testcase name="a" classname="C"><failure message="different"/></testcase>
<testcase name="b"><error type="TypeError"/></testcase>
<testcase name="c" time="0"><skipped>offline</skipped></testcase>
</testsuite></testsuites>"""

# Expected wire results, specified independently of catalog discovery.
CASES = {
    "python": (PYTHON, {
        "functions": [["work", 4]], "classes": [["Box", 2]], "imports": ["from .model import Foo"],
        "doc_gaps": [["Box", 2], ["work", 4]], "bare_handlers": [7], "assertions": [[6, "items"]],
        "mutable_defaults": [["work", 4]], "signatures": ["work(*, items=[])"]}),
    "json": (JSON, {
        "pretty": json.dumps(json.loads(JSON), indent=2), "compact": JSON,
        "sort_keys": json.dumps(json.loads(JSON), sort_keys=True, indent=2),
        "null_paths": ["/z"], "empty_paths": ["/a~1b/~0x"], "depth": 2,
        "type_counts": {"object": 2, "array": 1, "null": 1, "boolean": 1, "number": 1},
        "top_keys": ["z", "a/b", "yes", "n"]}),
    "csv": (CSV, {
        "headers": ["id","note"], "shape": {"columns": 2, "rows": 3},
        "missing": {"id": 0, "note": 1}, "cardinality": {"id": 2, "note": 2},
        "duplicate_rows": [{"row": ["01","a,b"], "count": 2}],
        "to_json": [{"id":"01","note":"a,b"},{"id":"01","note":"a,b"},{"id":"02","note":""}],
        "widths": {"id":2,"note":4}, "constant_columns": []}),
    "text": (TEXT, {
        "line_endings": {"CRLF":1,"LF":1,"CR":1}, "normalize_lf":"Hi\n\tX \nHi\n<<<<<<< branch",
        "trailing_space":[2], "indent_tabs":[2], "conflict_markers":[4],
        "sha256":hashlib.sha256(TEXT.encode()).hexdigest(), "duplicate_lines":{"Hi":2},
        "size":{"characters":len(TEXT),"bytes":len(TEXT),"lines":4}}),
    "ini": (INI, {
        "sections":["app","dev"], "defaults":{"Color":"blue"},
        "to_json":{"app":{"Color":"blue","Empty":"","Multi":"line1\nline2"},"dev":{"Color":"blue"}},
        "empty_options":[["app","Empty"]], "reused_keys":{"Color":2}, "section_sizes":{"app":3,"dev":1},
        "multiline":[["app","Multi"]], "empty_sections":[]}),
    "package": (PACKAGE, {
        "scripts":{"pretest":"prep","test":"go"}, "runtime_deps":{"x":"1"}, "dev_deps":{"x":"2"},
        "peer_deps":{"y":"3"}, "engines":{"node":">=18"}, "dep_overlap":["x"],
        "script_hooks":{"test":["pretest"]}, "workspaces":["lib/*"]}),
    "junit": (JUNIT, {
        "summary":{"passed":1,"failure":1,"error":1,"skipped":1},
        "failures":[{"name":"a","class":"C","message":"different"}],
        "errors":[{"name":"b","class":"","type":"TypeError"}], "skips":[{"name":"c","class":"","reason":"offline"}],
        "durations":[{"name":"a","class":"C","seconds":0.5},{"name":"c","class":"","seconds":0}],
        "class_counts":{"C":2,"":2}, "repeats":[{"name":"a","class":"C","count":2}],
        "untimed":[{"name":"a","class":"C"},{"name":"b","class":""}]}),
}
CASES["python"][1]["review"] = {k: CASES["python"][1][k] for k in ['mutable_defaults', 'bare_handlers', 'doc_gaps']}
CASES["csv"][1]["quality_report"] = {k: CASES["csv"][1][k] for k in ['shape', 'missing', 'duplicate_rows', 'constant_columns']}
CASES["json"][1]["structure_report"] = {k: CASES["json"][1][k] for k in ['depth', 'type_counts', 'null_paths', 'empty_paths']}
CASES["text"][1]["patch_preflight"] = {k: CASES["text"][1][k] for k in ['conflict_markers', 'trailing_space', 'indent_tabs', 'line_endings']}
CASES["ini"][1]["config_review"] = {k: CASES["ini"][1][k] for k in ['empty_sections', 'empty_options', 'reused_keys', 'multiline']}
CASES["package"][1]["install_review"] = {k: CASES["package"][1][k] for k in ['engines', 'dep_overlap', 'script_hooks', 'workspaces']}
CASES["junit"][1]["triage"] = {k: CASES["junit"][1][k] for k in ['summary', 'failures', 'errors', 'skips']}
CASES["package"][1]["install_review"]["install_scripts"] = {}
EXAMPLES = [(f"{group}_{name}", source, expected) for group, (source, cases) in CASES.items()
            for name, expected in cases.items()]


@pytest.mark.parametrize("name,source,expected", EXAMPLES, ids=[case[0] for case in EXAMPLES])
def test_skill_registry_wire_result(name, source, expected):
    result = asyncio.run(ToolRegistry(None, None).dispatch("skill_run", {"skill": name, "text": source}))
    assert json.loads(json.dumps(result)) == {"skill": name, "result": expected}


def test_every_advertised_skill_is_covered():
    assert set(skills.SKILLS) == {case[0] for case in EXAMPLES}
    assert sum(skills.catalog()["groups"].values()) == len(EXAMPLES)
    assert set(skills.catalog("junit", "timing")["skills"]) == {"junit_untimed"}


@pytest.mark.parametrize("name,source", [
    ("json_depth", '{"k":1,"k":2}'), ("json_depth", "NaN"), ("json_depth", "1e999"),
    ("json_depth","["*65+"0"+"]"*65), ("json_top_keys", "null"), ("json_compact", " "),
    ("csv_shape","a,a\n1,2"), ("csv_shape","a, \n1,2"), ("csv_shape","a,b\n1"),
    ("csv_shape","a\n1,2"), ("csv_shape",'a\n"unterminated'), ("csv_shape",""),
    ("package_scripts", "[]"), ("package_scripts",'{"scripts":null}'), ("package_scripts",'{"scripts":{"a":1}}'),
    ("package_workspaces",'{"workspaces":{}}'), ("package_workspaces",'{"workspaces":[5]}'),
    ("ini_sections","x=3"), ("ini_sections","[app]\n[app]"), ("ini_sections","[app]\na=1\na=2"),
    ("junit_summary","<other/>"), ("junit_summary","<testsuite>"), ("junit_summary","<!DOCTYPE testsuite><testsuite/>"),
    ("junit_durations",'<testsuite><testcase time="inf"/></testsuite>'),
    ("junit_durations",'<testsuite><testcase time="-1"/></testsuite>'),
    ("python_functions","async def ("), ("python_imports", "x+" * 2000 + "1"),
    ("text_size","\ud800"), ("text_size","a"*32769), ("text_size",None)
])
def test_bad_documents_have_structured_errors(name, source):
    assert skills.run(name, source)["error"] == "invalid_input"


def test_output_limit_instead_of_partial_json():
    assert skills.run("text_normalize_lf", "a"*30000)["error"] == "result_too_large"


def test_parallel_callers_are_isolated():
    async def batch():
        registry = ToolRegistry(None, None)
        return await asyncio.gather(*(registry.dispatch("skill_run", {"skill":"json_depth","text":"["*n+"0"+"]"*n})
                                      for n in range(65)))
    assert [row["result"] for row in asyncio.run(batch())] == list(range(65))


def test_complete_agent_turn_with_catalog_and_parallel_provider_calls():
    class Client:
        async def stream_chat(self, messages, **options):
            names = [s["function"]["name"] for s in options["tools"]]
            assert names == ["skill_catalog", "skill_run"]
            if messages[-1]["role"] == "user":
                calls = [{"id":"catalog","function":{"name":"skill_catalog","arguments":{"query":"sha256"}}}]
            elif messages[-1].get("tool_call_id") == "catalog":
                assert "text_sha256" in json.loads(messages[-1]["content"])["skills"]
                calls = [{"id":name,"function":{"name":"skill_run","arguments":{"skill":name,"text":"hi"}}}
                         for name in ("text_sha256","text_size")]
            else:
                assert json.loads(messages[-2]["content"])["result"] == hashlib.sha256(b"hi").hexdigest()
                assert json.loads(messages[-1]["content"])["result"]["bytes"] == 2
                yield {"choices":[{"message":{"content":"Verified."}}]}
                return
            yield {"choices":[{"message":{"tool_calls":calls}}]}

    config = SimpleNamespace(max_rounds=4,max_tokens=1000,thinking=False,temperature=0,stream=False,verbose=False)
    agent = Agent(config, Client(), ToolRegistry(None,None), Mock(), "test")
    asyncio.run(agent.send("verify this text"))
    assert agent.messages[-1]["content"] == "Verified."
    assert agent.rounds == 3


def test_workspace_document_round_trip(tmp_path):
    from codeagent.workspace import Workspace
    workspace = Workspace(tmp_path / "project")
    raw = b"a\r\nb\rc\n"
    (workspace.root / "sample.txt").write_bytes(raw)
    registry = ToolRegistry(None, SimpleNamespace(ws=workspace))

    def invoke(**args):
        return asyncio.run(registry.dispatch("skill_run", {"skill":"text_sha256", **args}))

    assert invoke(path="sample.txt")["result"] == hashlib.sha256(raw).hexdigest()
    assert invoke(path="sample.txt", text="both")["error"] == "invalid_arguments"
    assert invoke(path="../outside.txt")["error"] == "invalid_input"
    assert invoke(path="missing")["error"] == "invalid_input"
    (workspace.root / "sample.txt").write_bytes(b"\xff")
    assert invoke(path="sample.txt")["error"] == "invalid_input"
    registry.disable("workspace")
    assert invoke(path="sample.txt")["error"] == "workspace_unavailable"


def test_install_lifecycle_without_base_script():
    doc = '{"scripts":{"postinstall":"generate-assets"}}'
    assert skills.run("package_install_review", doc)["result"]["install_scripts"] == {"postinstall":"generate-assets"}


def test_streamed_tool_fragments_with_interleaved_calls():
    class Client:
        async def stream_chat(self, messages, **options):
            if messages[-1]["role"] == "tool":
                assert [json.loads(m["content"])["result"] for m in messages[-2:]] == [0, 2]
                yield {"choices":[{"delta":{"content":"done"}}]}
                return
            for index, data in [(1,'{"skill":"json_depth",'), (0,'{"skill":"json_depth",'),
                                (0,'"text":"0"}'), (1,'"text":"[[0]]"}')]:
                fn = {"arguments":data}
                if data.startswith("{"):
                    fn["name"] = "skill_run"
                yield {"choices":[{"delta":{"tool_calls":[{"index":index,"id":str(index),"function":fn}]}}]}

    config = SimpleNamespace(max_rounds=3,max_tokens=1000,thinking=False,temperature=0,stream=True,verbose=False)
    agent = Agent(config, Client(), ToolRegistry(None,None), Mock(), "test")
    asyncio.run(agent.send("compare depths"))
    assert agent.messages[-1]["content"] == "done"


def test_randomized_json_compaction_round_trips():
    import random
    rng = random.Random(17)
    for _ in range(300):
        value = {"data":[rng.choice([None, True, "é", "~1/", rng.randint(-10000,10000)]) for _ in range(20)]}
        compact = skills.run("json_compact", json.dumps(value))["result"]
        assert json.loads(compact) == value
        assert skills.run("json_compact", compact)["result"] == compact


def test_real_pytest_report(tmp_path):
    import subprocess
    import sys

    (tmp_path / "test_demo.py").write_text("def test_green(): assert True\ndef test_red(): assert 1 == 2\n")
    process = subprocess.run([sys.executable, "-m", "pytest", "-q", "--junitxml=run.xml"], cwd=tmp_path,
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 1, process.stdout + process.stderr
    from codeagent.workspace import Workspace
    registry = ToolRegistry(None, SimpleNamespace(ws=Workspace(tmp_path)))
    result = asyncio.run(registry.dispatch("skill_run", {"skill":"junit_triage","path":"run.xml"}))["result"]
    assert result["summary"] == {"passed":1,"failure":1}
    assert result["failures"][0]["name"] == "test_red"
