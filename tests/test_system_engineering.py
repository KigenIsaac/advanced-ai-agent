import asyncio
import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from codeagent.workspace import Workspace
from engineering_skills import catalog, run, system
from main import ToolRegistry

PLAN = {
    "tasks": [
        {"id": "spec", "hours": 2, "owner": "Dev", "tests": ["T1"]},
        {"id": "db", "hours": 3, "depends": ["spec"], "owner": "DBA", "tests": ["T1"]},
        {"id": "ui", "hours": 1, "depends": ["spec"]},
    ]
}
SCHEMA = {
    "tables": {
        "member": {
            "pk": "id",
            "columns": {"id": "INTEGER", "name": "TEXT"},
            "required": ["name"],
            "unique": ["name"],
        },
        "loan": {
            "pk": "id",
            "columns": {"id": "INTEGER", "member_id": "INTEGER"},
            "references": {"member_id": "member"},
        },
    }
}
MACHINE = {
    "states": ["draft", "approved", "stuck", "orphan"],
    "initial": "draft",
    "terminal": ["approved"],
    "transitions": [
        {"from": "draft", "event": "approve", "to": "approved"},
        {"from": "draft", "event": "hold", "to": "stuck"},
    ],
    "events": ["approve", "hold"],
}
API = {
    "openapi": "3.1.0",
    "security": [{"token": []}],
    "components": {"parameters": {"p": {"in": "path", "name": "id", "required": True}}},
    "paths": {
        "/loans/{id}": {
            "parameters": [{"$ref": "#/components/parameters/p"}],
            "get": {
                "operationId": "getLoan",
                "deprecated": True,
                "responses": {"200": {"description": "ok"}},
            },
            "post": {
                "security": [],
                "requestBody": {"required": True, "content": {"application/json": {}}},
                "responses": {"400": {"description": "bad"}},
            },
        }
    },
}
GET, POST = "GET /loans/{id}", "POST /loans/{id}"


def invoke(name, doc):
    out = run("project_" + name, json.dumps(doc))
    assert "result" in out, out
    return out["result"]


PLAN_CASES = {
    "build_waves": [["spec"], ["db", "ui"]],
    "earliest_schedule": {
        "spec": {"start": 0, "finish": 2},
        "db": {"start": 2, "finish": 5},
        "ui": {"start": 2, "finish": 3},
    },
    "critical_path": {"path": ["spec", "db"], "hours": 5},
    "task_slack": {"spec": 0, "db": 0, "ui": 2},
    "dependency_impact": {"spec": ["db", "ui"], "db": [], "ui": []},
    "effort_by_owner": {"owners": {"Dev": 2, "DBA": 3}, "unassigned_hours": 1},
    "acceptance_trace": {"T1": ["spec", "db"]},
    "handoff_map": [
        {"from_task": "spec", "to_task": "db", "from_owner": "Dev", "to_owner": "DBA"}
    ],
    "release_gate": {"ready": False, "gaps": {"ui": ["owner", "tests"]}},
}
API_CASES = {
    "route_inventory": [
        {"route": GET, "operation_id": "getLoan", "tags": []},
        {"route": POST, "operation_id": None, "tags": []},
    ],
    "operation_id_gaps": [POST],
    "path_binding_gaps": [],
    "response_test_matrix": [
        {"route": GET, "status": "200", "media_types": []},
        {"route": POST, "status": "400", "media_types": []},
    ],
    "error_contract_gaps": [GET],
    "request_media_matrix": [
        {"route": POST, "required": True, "media_types": ["application/json"]}
    ],
    "auth_contract": [
        {"route": GET, "alternatives": [{"token": []}], "anonymous": False},
        {"route": POST, "alternatives": [], "anonymous": True},
    ],
    "deprecation_inventory": [
        {"route": GET, "operation_deprecated": True, "parameters": []}
    ],
    "required_input_cases": [
        {"route": GET, "omit": "id", "location": "path"},
        {"route": POST, "omit": "id", "location": "path"},
        {"route": POST, "omit": "requestBody", "location": "body"},
    ],
}
STATE_CASES = {
    "state_reachability": ["orphan"],
    "dead_end_states": ["stuck"],
    "completion_traps": ["stuck"],
    "event_permissions": {"approve": ["draft"], "hold": ["draft"]},
    "transition_test_cases": [
        {"setup": [], "from": "draft", "event": "approve", "expected": "approved"},
        {"setup": [], "from": "draft", "event": "hold", "expected": "stuck"},
    ],
    "invalid_event_matrix": {
        "draft": [],
        "approved": ["approve", "hold"],
        "stuck": ["approve", "hold"],
    },
    "state_replay": {
        "accepted": False,
        "index": 1,
        "event": "hold",
        "visited": ["draft", "approved"],
    },
    "terminal_scenarios": {"approved": ["approve"]},
    "state_table": {
        "draft": {"approve": "approved", "hold": "stuck"},
        **{s: {"approve": None, "hold": None} for s in ["approved", "stuck", "orphan"]},
    },
}
CHECKS = [
    (k, doc, v)
    for doc, cases in [(PLAN, PLAN_CASES), (API, API_CASES), (MACHINE, STATE_CASES)]
    for k, v in cases.items()
]
CAPACITY = [
    (
        "worker_sizing",
        {"arrival_rps": 30, "mean_service_ms": 100, "target_utilization": 0.75},
        {"offered_concurrency": 3, "workers": 4},
    ),
    (
        "queue_recovery",
        {"backlog_items": 80, "arrival_rps": 5, "processing_rps": 9},
        {"drainable": True, "seconds": 20},
    ),
    (
        "retention_storage",
        {
            "events_per_day": 1024,
            "bytes_per_event": 1048576,
            "retention_days": 2,
            "copies": 2,
        },
        {"replicated_bytes": 2**32, "gib": 4},
    ),
    (
        "transfer_window",
        {"payload_bytes": 1000000, "link_mbps": 4, "target_utilization": 0.5},
        {"seconds": 4},
    ),
    (
        "error_budget",
        {"window_days": 1, "target_percent": 99, "outage_minutes": 10},
        {"allowed_minutes": 14.4, "remaining_minutes": 4.4},
    ),
    (
        "retry_envelope",
        {
            "attempts": 4,
            "timeout_ms": 100,
            "initial_backoff_ms": 10,
            "max_backoff_ms": 15,
        },
        {"waits_ms": [10, 15, 15], "worst_case_ms": 440},
    ),
    (
        "db_pool_budget",
        {"max_connections": 19, "reserved_connections": 3, "peak_replicas": 3},
        {"per_replica": 5, "unallocated": 1, "can_connect_all": True},
    ),
    (
        "cache_origin_load",
        {"request_rps": 80, "hit_percent": 75},
        {"origin_rps": 20, "served_from_cache_rps": 60},
    ),
    (
        "rollout_capacity",
        {
            "replicas": 5,
            "unavailable_replicas": 2,
            "per_replica_rps": 40,
            "peak_rps": 130,
        },
        {"available_rps": 120, "spare_rps": -10, "meets_peak": False},
    ),
]


@pytest.mark.parametrize(
    "name,doc,expected", CHECKS + CAPACITY, ids=[x[0] for x in CHECKS + CAPACITY]
)
def test_design_outputs(name, doc, expected):
    result = invoke(name, doc)
    assert result == expected


def test_relational_tools_generate_executable_queries():
    con = sqlite3.connect(":memory:")
    con.executescript(invoke("schema_ddl", SCHEMA))
    indexes = invoke("fk_indexes", SCHEMA)
    assert len(indexes) == 1
    con.execute(indexes[0])
    assert con.execute("pragma index_list(loan)").fetchone()
    inserts = invoke("insert_templates", SCHEMA)
    con.execute(inserts["member"], {"id": 1, "name": "Isaac"})
    con.execute(inserts["loan"], {"id": 2, "member_id": 1})
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(inserts["loan"], {"id": 3, "member_id": 99})
    con.execute(
        invoke("update_templates", SCHEMA)["member"], {"id": 1, "name": "Kigen"}
    )
    assert con.execute(
        invoke("lookup_templates", SCHEMA)["member"], (1,)
    ).fetchone() == (1, "Kigen")
    for query in invoke("unique_checks", SCHEMA) + invoke("orphan_checks", SCHEMA):
        assert con.execute(query).fetchall() == []
    for query in invoke("null_checks", SCHEMA):
        assert con.execute(query).fetchone() == (0,)
    assert invoke("data_dictionary", SCHEMA)["loan"][1]["references"] == "member"
    con.commit()
    con.execute("pragma foreign_keys=off")
    con.execute("insert into loan values(8,88)")
    assert con.execute(invoke("orphan_checks", SCHEMA)[0]).fetchall() == [(8, 88)]
    con.close()


def test_saved_artifacts_form_a_working_component(tmp_path):
    ws = Workspace(tmp_path)
    registry = ToolRegistry(None, SimpleNamespace(ws=ws))

    def dispatch(skill, **kwargs):
        return asyncio.run(
            registry.dispatch("skill_run", {"skill": "project_" + skill, **kwargs})
        )

    for doc, outputs in [
        (
            MACHINE,
            {"state_reducer": "state_machine.py", "state_test_module": "test_state.py"},
        ),
        (
            SCHEMA,
            {
                "schema_ddl": "schema.sql",
                "schema_smoke_tests": "test_db.py",
                "repository_module": "repo.py",
            },
        ),
    ]:
        ws.write_file("design.json", json.dumps(doc))
        for name, output in outputs.items():
            response = dispatch(name, path="design.json", output=output)
            assert response["artifact"]["path"] == output
    code = """import sqlite3
import repo
from state_machine import INITIAL, transition

connection = sqlite3.connect(':memory:')
connection.executescript(open('schema.sql').read())
assert repo.insert(connection,'member',dict(id=1,name='Kigen')) == 1
assert repo.update(connection,'member',dict(id=1,name='Isaac')) == 1
assert repo.lookup(connection,'member',1) == (1,'Isaac')
assert transition(INITIAL,'approve') == 'approved'
"""
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, timeout=10)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passed" in result.stdout
    assert (
        dispatch("schema_ddl", path="design.json", output="schema.sql")["error"]
        == "output_failed"
    )
    assert (
        dispatch("schema_ddl", path="design.json", output="../escaped.sql")["error"]
        == "output_failed"
    )
    registry.disable("workspace")
    assert (
        dispatch("schema_ddl", text=json.dumps(SCHEMA), output="second.sql")["error"]
        == "workspace_unavailable"
    )


@pytest.mark.parametrize(
    "name,doc",
    [
        ("build_waves", {"tasks": [{"id": "x", "hours": 1, "depends": ["x"]}]}),
        ("critical_path", {"tasks": [{"id": "a", "hours": -1}]}),
        ("schema_ddl", {"tables": {"x": {"pk": "id", "columns": {"id": "unknown"}}}}),
        ("state_table", {**MACHINE, "transitions": MACHINE["transitions"] * 2}),
        (
            "route_inventory",
            {"openapi": "3.0.0", "paths": {"/": {"$ref": "#/paths/~1"}}},
        ),
        (
            "worker_sizing",
            {"arrival_rps": 100, "mean_service_ms": 20, "target_utilization": 0},
        ),
        (
            "db_pool_budget",
            {"reserved_connections": 2, "max_connections": 1, "peak_replicas": 1},
        ),
        ("route_inventory", {"openapi": "3.1.0", "paths": []}),
    ],
)
def test_invalid_engineering_spec(name, doc):
    assert run("project_" + name, json.dumps(doc))["error"] == "invalid_input"


def test_all_system_skills_accounted_for():
    artifacts = {
        "state_reducer",
        "state_test_module",
        "schema_ddl",
        "schema_smoke_tests",
        "repository_module",
        "fk_indexes",
        "insert_templates",
        "update_templates",
        "lookup_templates",
        "unique_checks",
        "orphan_checks",
        "null_checks",
        "data_dictionary",
    }
    assert set(system.TOOLS) == {
        "project_" + k for k in artifacts.union(x[0] for x in CHECKS + CAPACITY)
    }
    assert catalog()["groups"]["project"] >= 50


def test_contract_binding_mismatch():
    doc = {
        "openapi": "3.0.3",
        "paths": {
            "/{a}/{b}": {
                "get": {
                    "parameters": [
                        {"name": "b", "in": "path", "required": False},
                        {"name": "c", "in": "path", "required": True},
                    ]
                }
            }
        },
    }
    assert [x["issue"] for x in invoke("path_binding_gaps", doc)] == [
        "missing",
        "optional",
        "unused",
    ]
