"""Small reproducible agent-vs-agent coding smoke benchmark.

Run only trusted agent executables, ideally in a disposable VM without personal
credentials or files. Temporary directories are not a security sandbox.
No network or paid API calls are made unless you supply a command and run it.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TASKS = {
    "decimal-ledger": {
        "source": "def total(entries):\n    return sum(float(x) for x in entries)\n",
        "prompt": "Fix solution.py total(entries) to return an exact Decimal sum of decimal strings, including negatives and empty input. Do not use floating point. Preserve the function name. Edit files, not just prose.",
        "checks": """from decimal import Decimal
from solution import total
assert total(["0.1", "0.2"]) == Decimal("0.3")
assert isinstance(total([]), Decimal)
assert total([]) == Decimal("0")
assert total(["9999999999999999.99", "-9999999999999999.98"]) == Decimal("0.01")
assert total(["-2.50", "1.30"]) == Decimal("-1.20")
""",
    },
    "stable-dedup": {
        "source": "def dedup(items):\n    return list(set(items))\n",
        "prompt": "Fix solution.py dedup(items) to preserve first-occurrence order, accept a generator and unhashable lists and dicts, and never mutate input. Edit the implementation.",
        "checks": """from solution import dedup
assert dedup([3, 1, 3, 2, 1]) == [3, 1, 2]
assert dedup(iter([4, 2, 4])) == [4, 2]
x = [[1], [2], [1], {"k": 1}, {"k": 1}]
assert dedup(x) == [[1], [2], {"k": 1}]
assert x == [[1], [2], [1], {"k": 1}, {"k": 1}]
assert dedup([]) == []
""",
    },
    "safe-file-path": {
        "source": "from pathlib import Path\n\ndef resolve_child(root, name):\n    return Path(root) / name\n",
        "prompt": "Fix solution.py resolve_child(root, name). Return an absolute resolved pathlib.Path within root, reject absolute names and traversal or symlink escapes with ValueError, allow legitimate nested paths. Edit the implementation.",
        "checks": """from pathlib import Path
from tempfile import TemporaryDirectory
from solution import resolve_child
with TemporaryDirectory() as directory:
    root = Path(directory) / "root"
    root.mkdir()
    assert resolve_child(root, "a/b.txt") == root.resolve() / "a/b.txt"
    for name in ("../outside", str(root / "abs"), "a/../../outside"):
        try:
            resolve_child(root, name)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe path accepted")
    try:
        (root / "link").symlink_to(Path(directory), target_is_directory=True)
    except OSError:
        pass
    else:
        try:
            resolve_child(root, "link/outside")
        except ValueError:
            pass
        else:
            raise AssertionError("symlink escape accepted")
""",
    },
}


def run_process(argv, cwd, timeout, output):
    started = time.monotonic()
    with output.open("wb") as log:
        p = subprocess.Popen(
            argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=(os.name != "nt"),
        )
        timed_out = False
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                p.kill()
            else:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            p.wait()
    return {"exit_code": p.returncode, "timed_out": timed_out,
            "seconds": round(time.monotonic() - started, 3)}


def ledger_cost(home):
    dbpath = home / "api_budget.sqlite3"
    if not dbpath.exists():
        return None
    with sqlite3.connect(dbpath) as db:
        row = db.execute("SELECT COALESCE(SUM(charged_micro + CASE WHEN pending=1 THEN reserved_micro ELSE 0 END),0) FROM calls").fetchone()
    return row[0] / 1_000_000


def expand_command(command, workspace, home, prompt):
    values = {"{workspace}": str(workspace), "{home}": str(home), "{prompt}": prompt}
    # Replacement only in literal argv strings; never invoke a shell.
    return [values.get(arg, arg) for arg in command]


def run_case(name, command, case_name, out, timeout):
    task = TASKS[case_name]
    with tempfile.TemporaryDirectory(prefix="agentbench-") as d:
        root = Path(d)
        workspace = root / "workspace"
        workspace.mkdir()
        home = root / "home"
        home.mkdir()
        (workspace / "solution.py").write_text(task["source"], encoding="utf-8")
        checker = root / "check.py"
        checker.write_text(
            "import sys\nsys.path.insert(0, " + repr(str(workspace)) + ")\n" + task["checks"],
            encoding="utf-8",
        )
        log = out / f"{name}-{case_name}-agent.log"
        result = run_process(expand_command(command, workspace, home, task["prompt"]),
                             workspace, timeout, log)
        checks = run_process([sys.executable, "-I", str(checker)], workspace, 20,
                             out / f"{name}-{case_name}-check.log")
        return {"agent": name, "task": case_name, **result,
                "passed": checks["exit_code"] == 0 and not checks["timed_out"]
                          and not result["timed_out"] and result["exit_code"] == 0,
                "check_exit_code": checks["exit_code"],
                "estimated_cost_usd": ledger_cost(home)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", action="append", nargs=2, metavar=("NAME", "ARGV_JSON"), required=True,
                        help="Repeat for each agent. Include a standalone {prompt} argv argument.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args(argv)
    if args.timeout <= 0 or not 1 <= args.repeat <= 20:
        parser.error("positive timeout and repeat between 1 and 20 required")
    commands = {}
    for name, raw in args.agent:
        if not name or not all(c.isascii() and (c.isalnum() or c in "_-") for c in name) or name in commands:
            parser.error("agent names must be unique ASCII alphanumeric, underscore or hyphen")
        try:
            command = json.loads(raw)
        except json.JSONDecodeError as exc:
            parser.error(str(exc))
        if not isinstance(command, list) or not all(isinstance(x, str) for x in command) or "{prompt}" not in command:
            parser.error("command must be a JSON string array including standalone {prompt}")
        commands[name] = command
    args.out.mkdir(parents=True, exist_ok=False)
    results = []
    for repeat in range(args.repeat):
        folder = args.out / str(repeat + 1)
        folder.mkdir()
        for name, command in commands.items():
            for task in TASKS:
                result = run_case(name, command, task, folder, args.timeout)
                results.append({"repeat": repeat + 1, **result})
                print(json.dumps(results[-1]), flush=True)
                (args.out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    summaries = {}
    for name in commands:
        rows = [r for r in results if r["agent"] == name]
        costs = [r["estimated_cost_usd"] for r in rows]
        summaries[name] = {
            "passed": sum(r["passed"] for r in rows), "total": len(rows),
            "seconds": round(sum(r["seconds"] for r in rows), 3),
            "estimated_cost_usd": sum(costs) if all(c is not None for c in costs) else None,
        }
    (args.out / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(json.dumps(summaries, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
