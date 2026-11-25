import json
import sys

from benchmark_agents import expand_command, main


def test_expand_command_preserves_literal_args(tmp_path):
    assert expand_command(["agent", "{prompt}", "literal {prompt}", "{home}"],
                          tmp_path, tmp_path, "fix this") == [
        "agent", "fix this", "literal {prompt}", str(tmp_path)
    ]


def test_runner_scores_noop_and_reports_unknown_cost(tmp_path):
    out = tmp_path / "results"
    command = json.dumps([sys.executable, "-c", "pass", "{prompt}"])
    assert main(["--agent", "noop", command, "--out", str(out), "--timeout", "10"]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["noop"]["passed"] == 0
    assert summary["noop"]["total"] == 3
    assert summary["noop"]["estimated_cost_usd"] is None
    assert len(list((out / "1").glob("*.log"))) == 6
