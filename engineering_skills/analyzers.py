"""Pinned, allowlisted static checks. Documents never execute."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES = {}
GROUP_CODES = {}
for row in (ROOT / "rules.tsv").read_text(encoding="utf-8").splitlines():
    group, code, label = row.split("\t")
    skill = group + "_" + label.replace("-", "_").replace(".", "_")
    if skill in RULES:
        raise ValueError(f"duplicate skill {skill}")
    engine = "SQLFluff" if group == "sql" else "Ruff"
    RULES[skill] = (group, code, f"{engine} {code}: {label.replace('-', ' ').replace('.', ' ')}")
    GROUP_CODES.setdefault(group, []).append(code)

# Each review runs its selected rules in a single analyzer process.
REVIEWS = {
    "review_python_correctness": ("bugs exceptions time", "Review Python for likely runtime bugs, exception handling and time errors."),
    "review_async_services": ("async logging exceptions", "Review async service code for blocking calls, logging and exception handling."),
    "review_test_quality": ("pytest", "Review pytest fixtures, assertions, markers and test structure."),
    "review_python_maintenance": ("modernize simplify efficiency", "Review outdated constructs, redundant control flow and inefficient operations."),
    "review_python_api": ("typing interfaces", "Review type annotations, unused parameters and API naming."),
    "review_sql_quality": ("sql", "Review ANSI SQL for correctness, ambiguity, joins, aliases and layout."),
}


def describe():
    return {**{k: v[2] for k, v in RULES.items()}, **{k: v[1] for k, v in REVIEWS.items()}}


def analyze(skill, source):
    if skill in REVIEWS:
        groups = REVIEWS[skill][0].split()
        codes = [code for group in groups for code in GROUP_CODES[group]]
        sql = groups == ["sql"]
    else:
        group, code, _ = RULES[skill]
        codes, sql = [code], group == "sql"

    if sql and re.search(r"(?im)^\s*--\s*sqlfluff\s*:", source):
        return {"error": "invalid_input", "message": "Inline SQLFluff configuration is unsupported."}
    if sql:
        command = [sys.executable, "-I", str(ROOT / "_sql_worker.py"), ",".join(codes)]
    else:
        command = [sys.executable, "-I", "-m", "ruff", "check", "--isolated", "--no-cache",
                   "--ignore-noqa", "--target-version", "py312", "--select", ",".join(codes),
                   "--output-format", "json", "-"]
    try:
        # Ignore project/user configuration and keep temporary state out of the workspace.
        with tempfile.TemporaryDirectory() as directory:
            env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH") if key in os.environ}
            env.update(HOME=directory, USERPROFILE=directory, TMPDIR=directory, TEMP=directory, TMP=directory)
            proc = subprocess.run(command, input=source.encode("utf-8"), capture_output=True,
                                  timeout=15, cwd=directory, env=env)
        if proc.returncode not in ((0,) if sql else (0, 1)):
            return {"error": "analyzer_unavailable", "message": "Check installed analyzer dependencies and versions."}
        data = json.loads(proc.stdout)
        if sql:
            count, findings, invalid = data["count"], data["findings"], data["parse_error"]
        else:
            count = len(data)
            invalid = any(v["code"] in ("invalid-syntax", "E999", None) for v in data)
            findings = [{"code": v["code"], "line": v["location"]["row"], "column": v["location"]["column"],
                         "message": v["message"][:400]} for v in data[:50]]
        return {"skill": skill, "result": {"status": "invalid_source" if invalid else "findings" if count else "clean",
                "count": count, "findings": findings, "omitted": count - len(findings)}}
    except subprocess.TimeoutExpired:
        return {"error": "analyzer_timeout", "message": "Analyze a smaller document."}
    except (OSError, ValueError, KeyError, TypeError):
        return {"error": "analyzer_unavailable", "message": "Analyzer failed to produce a supported result."}
