import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeagent.workspace import Workspace
from engineering_skills import assurance, catalog, run
from main import ToolRegistry


def witnesses():
    result = {}
    fixture = Path(__file__).parent / "fixtures" / "assurance_witnesses.tsv"
    for line in fixture.read_text().splitlines():
        name, fields, passed, failed = line.split("\t")

        def record(sample):
            return dict(
                zip(fields.split(","), json.loads("[" + sample + "]"), strict=True)
            )

        result["project_" + name] = (record(passed), record(failed))
    return result


WITNESSES = witnesses()


@pytest.mark.parametrize("skill", WITNESSES)
def test_every_check_accepts_and_rejects_evidence(skill):
    positive, negative = WITNESSES[skill]
    assert run(skill, json.dumps(positive))["result"]["status"] == "pass"
    assert run(skill, json.dumps(negative))["result"]["status"] == "fail"
    assert run(skill, "{}")["error"] == "invalid_input"


def test_all_new_skills_are_individually_discoverable():
    assert len(WITNESSES) == 250 and set(WITNESSES) == set(assurance.POLICIES)
    keys = set()
    for track, expected in [
        ("scale", 63),
        ("accuracy", 63),
        ("safety", 63),
        ("reliability", 73),
    ]:
        page = catalog("project", query="project_" + track + "_", limit=50)
        track_keys = set(page["skills"])
        while page["next_offset"] is not None:
            page = catalog(
                "project",
                query="project_" + track + "_",
                limit=50,
                offset=page["next_offset"],
            )
            track_keys.update(page["skills"])
        assert len(track_keys) == expected
        keys.update(track_keys)
    assert keys == set(assurance.DESCRIPTIONS)


def check(name, data):
    return run("project_" + name, json.dumps(data))["result"]


@pytest.mark.parametrize(
    "name,doc,expected",
    [
        (
            "scale_queue_trace",
            {
                "initial": 2,
                "capacity": 5,
                "windows": [
                    {"arrivals": 5, "service_slots": 2},
                    {"arrivals": 0, "service_slots": 8},
                ],
            },
            {"peak": 5, "completed": 5, "dropped": 2, "backlog_after_windows": [3, 0]},
        ),
        (
            "scale_load_knee",
            {
                "max_p99_ms": 100,
                "max_error_ratio": 0.01,
                "steps": [
                    {"rps": 1, "p99_ms": 100, "requests": 100, "errors": 1},
                    {"rps": 2, "p99_ms": 90, "requests": 100, "errors": 2},
                ],
            },
            {"last_passing_rps_before_breach": 1, "first_breaching_rps": 2},
        ),
        (
            "accuracy_journal_trace",
            {
                "postings": [
                    {"transaction": "1", "currency": "KES", "minor_units": 170},
                    {"transaction": "1", "currency": "KES", "minor_units": -170},
                    {"transaction": "1", "currency": "USD", "minor_units": 2},
                ]
            },
            {
                "groups_checked": 2,
                "unbalanced": [
                    {"transaction": "1", "currency": "USD", "net_minor_units": 2}
                ],
            },
        ),
        (
            "accuracy_classifier_slices",
            {"cases": [{"slice": "a", "expected": True, "predicted": False}]},
            {
                "a": {
                    "tp": 0,
                    "tn": 0,
                    "fp": 0,
                    "fn": 1,
                    "precision": None,
                    "recall": 0.0,
                }
            },
        ),
        (
            "safety_approval_trace",
            {
                "required": 2,
                "transactions": [
                    {"id": "loan1", "maker": "A", "approvers": ["A", "B", "B"]}
                ],
            },
            {
                "checked": 1,
                "findings": [{"id": "loan1", "self_approval": True, "independent": 1}],
            },
        ),
        (
            "safety_isolation_matrix",
            {
                "cases": [
                    {
                        "role": "member",
                        "resource": "other tenant",
                        "expected": False,
                        "allowed": True,
                    }
                ]
            },
            {
                "checked": 1,
                "mismatches": [
                    {
                        "case": 0,
                        "role": "member",
                        "resource": "other tenant",
                        "issue": "overgrant",
                    }
                ],
            },
        ),
        (
            "reliability_outage_union",
            {
                "window_sec": 100,
                "outages": [
                    {"start_sec": 20, "end_sec": 40},
                    {"start_sec": 15, "end_sec": 22},
                    {"start_sec": 4, "end_sec": 10},
                ],
            },
            {"downtime_sec": 31, "availability_ratio": 0.69},
        ),
        (
            "reliability_retry_trace",
            {
                "attempts": [
                    {"id": "x", "attempt": 1, "success": False},
                    {"id": "y", "attempt": 1, "success": False},
                    {"id": "x", "attempt": 2, "success": True},
                ]
            },
            {"operations": 2, "amplification": 1.5, "without_success": ["y"]},
        ),
    ],
)
def test_trace_diagnostics(name, doc, expected):
    assert check(name, doc) == expected


@pytest.mark.parametrize(
    "skill,record",
    [
        ("scale_queue_trace", {"capacity": -1, "initial": 0, "windows": []}),
        ("scale_load_knee", {"max_p99_ms": 1, "max_error_ratio": 2, "steps": []}),
        (
            "accuracy_journal_trace",
            {"postings": [{"transaction": "a", "currency": "KES", "minor_units": 0.2}]},
        ),
        (
            "accuracy_classifier_slices",
            {"cases": [{"slice": "A", "expected": 1, "predicted": True}]},
        ),
        ("safety_approval_trace", {"required": 0, "transactions": []}),
        (
            "safety_isolation_matrix",
            {
                "cases": [
                    {"role": "x", "resource": "y", "expected": False, "allowed": "yes"}
                ]
            },
        ),
        (
            "reliability_outage_union",
            {"window_sec": 5, "outages": [{"start_sec": 3, "end_sec": 6}]},
        ),
        (
            "reliability_retry_trace",
            {"attempts": [{"id": "x", "attempt": 2, "success": False}]},
        ),
        (
            "accuracy_prediction_recall_floor",
            {
                "true_positive_count": 6,
                "actual_positive_count": 5,
                "minimum_recall_ratio": 0.98,
            },
        ),
        (
            "accuracy_prediction_slice_gap",
            {
                "lowest_slice_accuracy_ratio": 0.9,
                "highest_slice_accuracy_ratio": 0.7,
                "allowed_gap_ratio": 0.3,
            },
        ),
        (
            "safety_transactions_maker_checker",
            {"self_approved_transactions": 0, "observations": False},
        ),
        (
            "safety_transactions_per_operation_limit",
            {"requested_minor_units": 1.1, "per_operation_limit_minor_units": 2},
        ),
    ],
)
def test_invalid_observations(skill, record):
    assert run("project_" + skill, json.dumps(record))["error"] == "invalid_input"


@pytest.mark.parametrize(
    "name,doc",
    [
        (
            "accuracy_prediction_recall_floor",
            {
                "true_positive_count": 0,
                "actual_positive_count": 0,
                "minimum_recall_ratio": 1,
            },
        ),
        (
            "safety_transactions_maker_checker",
            {"self_approved_transactions": 0, "observations": 0},
        ),
        (
            "accuracy_prediction_mae_ceiling",
            {"absolute_error_sum": 0, "max_mae": 1, "prediction_count": 0},
        ),
    ],
)
def test_no_evidence_is_explicit(name, doc):
    assert check(name, doc)["status"] == "insufficient_evidence"


@pytest.mark.parametrize("track", ["scale", "accuracy", "safety", "reliability"])
def test_review_coverage(track):
    keys = [k for k, p in assurance.POLICIES.items() if p.track == track]
    evidence = {
        keys[0]: WITNESSES[keys[0]][0],
        keys[1]: WITNESSES[keys[1]][1],
        keys[2]: {},
    }
    report = check(track + "_evidence_review", {"evidence": evidence})
    assert report["checked"] == 3 and report["not_evaluated"] == len(keys) - 3
    assert report["outcomes"] == {
        "pass": 1,
        "fail": 1,
        "invalid_input": 1,
        "insufficient_evidence": 0,
    }


def test_dispatch_from_workspace_to_persisted_artifact(tmp_path):
    reg = ToolRegistry(None, SimpleNamespace(ws=Workspace(tmp_path)))
    key = "project_safety_transactions_daily_limit"
    args = {"skill": key, "path": "transfer.json", "output": "report.json"}
    (tmp_path / "transfer.json").write_text(
        json.dumps(
            {
                "daily_limit_minor_units": 100,
                "requested_minor_units": 40,
                "already_spent_minor_units": 80,
            }
        )
    )
    response = asyncio.run(reg.dispatch("skill_run", args))
    assert response["artifact"]["path"] == "report.json"
    assert json.loads((tmp_path / "report.json").read_text()) == {
        "status": "fail",
        "observed": "120",
        "target": "100",
        "operator": "<=",
    }
    assert asyncio.run(reg.dispatch("skill_run", args))["error"] == "output_failed"


def test_large_json_decimal_boundary_is_exact():
    skill = "project_scale_http_peak_service_rate"
    result = run(
        skill, '{"peak_rps":9007199254740992.1,"sustainable_rps":9007199254740992}'
    )
    assert result["result"]["status"] == "fail"


def test_microseconds_and_strict_deadline_equality():
    skill = "project_reliability_messaging_heartbeat_timeout"
    args = {
        "heartbeat_interval_sec": 0.1,
        "heartbeat_jitter_sec": 0.2,
        "consumer_timeout_sec": 0.3,
    }
    result = run(skill, json.dumps(args))["result"]
    assert result["observed"] == "0.3" and result["status"] == "fail"


def test_out_of_range_decimal_exponent_is_recoverable():
    result = run(
        "project_scale_http_peak_service_rate",
        '{"peak_rps":1e9999999999999999999999,"sustainable_rps":10}',
    )
    assert result["error"] == "invalid_input"
