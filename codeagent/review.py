"""
review.py — Evidence-based code review.

Layer 25 Code review

Returns structured findings with reasons and evidence, not just
"looks good" verdicts.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .workspace import Workspace
from .vcs import Git


@dataclass
class Finding:
    file: str
    line: int
    severity: str          # info | warning | error
    category: str          # bug | security | performance | style | tests | requirements
    message: str
    evidence: str = ""
    suggestion: str = ""


class Reviewer:
    def __init__(self, workspace: Workspace, git: Optional[Git] = None):
        self.ws = workspace
        self.git = git or Git(workspace)

    # ------------------------------------------------------------------ #
    # Scope helpers
    # ------------------------------------------------------------------ #

    def _changed_files(self, ref: str = "HEAD") -> list[str]:
        r = self.git._git("diff", "--name-only", ref, check=False)
        if r["code"] != 0:
            return []
        return [l.strip() for l in r["stdout"].splitlines() if l.strip()]

    def _file_lines(self, file: str) -> list[str]:
        try:
            return self.ws.read_file(file)["content"].splitlines()
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Review entry points
    # ------------------------------------------------------------------ #

    def review_changes(self, ref: str = "HEAD",
                       checks: Optional[list[str]] = None) -> dict:
        checks = checks or ["bugs", "security", "performance", "style", "tests"]
        files = self._changed_files(ref)
        findings: list[Finding] = []
        for f in files:
            for check in checks:
                findings.extend(self._run_check(f, check))
        return self._format(findings, scope=f"diff vs {ref}", files=files)

    def review_file(self, file: str,
                    checks: Optional[list[str]] = None) -> dict:
        checks = checks or ["bugs", "security", "performance", "style"]
        findings: list[Finding] = []
        for check in checks:
            findings.extend(self._run_check(file, check))
        return self._format(findings, scope=file, files=[file])

    def review_pull_request(self, number: int,
                            checks: Optional[list[str]] = None) -> dict:
        from .vcs import Remote
        remote = Remote(self.ws)
        diff = remote.get_pr_diff(number)
        findings = self._review_diff_text(diff, checks)
        return self._format(findings, scope=f"PR #{number}", files=[])

    # ------------------------------------------------------------------ #
    # Checks
    # ------------------------------------------------------------------ #

    def _run_check(self, file: str, check: str) -> list[Finding]:
        if not file.endswith((".py", ".js", ".ts", ".tsx", ".jsx",
                              ".go", ".rs", ".rb", ".php")):
            return []
        lines = self._file_lines(file)
        if not lines:
            return []
        findings: list[Finding] = []
        if check == "bugs":
            findings += self._bug_check(file, lines)
        elif check == "security":
            findings += self._security_check(file, lines)
        elif check == "performance":
            findings += self._performance_check(file, lines)
        elif check == "style":
            findings += self._style_check(file, lines)
        elif check == "tests":
            findings += self._tests_check(file, lines)
        return findings

    def _bug_check(self, file: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Bare except
            if re.match(r"except\s*:", stripped):
                out.append(Finding(
                    file=file, line=i, severity="warning", category="bug",
                    message="Bare `except:` will swallow SystemExit/KeyboardInterrupt.",
                    evidence=line.rstrip(),
                    suggestion="Use `except Exception:` or a narrower type.",
                ))
            # Mutable default
            if re.search(r"def \w+\([^)]*=\s*(\[\]|\{\})", stripped):
                out.append(Finding(
                    file=file, line=i, severity="warning", category="bug",
                    message="Mutable default argument.",
                    evidence=line.rstrip(),
                    suggestion="Use `None` and assign inside the function.",
                ))
            # Python == None / != None
            if re.search(r"[!=]=\s*None\b", stripped) and "is" not in stripped:
                out.append(Finding(
                    file=file, line=i, severity="info", category="style",
                    message="Use `is None` / `is not None`.",
                    evidence=line.rstrip(),
                ))
            # JS: = in conditionals
            if file.endswith((".js", ".ts", ".tsx", ".jsx")):
                if re.search(r"if\s*\([^=!<>]*[^=!<>]=[^=]", stripped):
                    out.append(Finding(
                        file=file, line=i, severity="warning", category="bug",
                        message="Possibly assignment in condition.",
                        evidence=line.rstrip(),
                    ))
        return out

    def _security_check(self, file: str, lines: list[str]) -> list[Finding]:
        patterns = [
            (r"eval\s*\(", "Use of `eval()`", "error"),
            (r"exec\s*\(", "Use of `exec()`", "error"),
            (r"subprocess\.\w+\([^)]*shell\s*=\s*True", "shell=True", "warning"),
            (r"os\.system\s*\(", "Use of `os.system()`", "warning"),
            (r"pickle\.loads?\s*\(", "Untrusted pickle deserialization", "error"),
            (r"yaml\.load\s*\([^,)]*\)", "yaml.load without Loader", "warning"),
            (r"password\s*=\s*['\"][^'\"]+['\"]", "Hard-coded password", "error"),
            (r"secret\s*=\s*['\"][^'\"]+['\"]", "Hard-coded secret", "error"),
            (r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]", "Hard-coded API key", "error"),
            (r"md5\s*\(", "MD5 in security context", "info"),
            (r"requests\.get\([^)]*verify\s*=\s*False", "TLS verification disabled", "error"),
        ]
        out: list[Finding] = []
        for i, line in enumerate(lines, 1):
            for pat, msg, sev in patterns:
                if re.search(pat, line):
                    out.append(Finding(
                        file=file, line=i, severity=sev, category="security",
                        message=msg, evidence=line.rstrip(),
                    ))
        return out

    def _performance_check(self, file: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        # N+1 query heuristic (Django ORM)
        for i, line in enumerate(lines, 1):
            if re.search(r"for\s+\w+\s+in\s+\w+\.objects\.all\(\)", line):
                out.append(Finding(
                    file=file, line=i, severity="warning", category="performance",
                    message="Possible N+1 query inside a loop.",
                    evidence=line.rstrip(),
                    suggestion="Consider select_related/prefetch_related.",
                ))
            if re.search(r"\+\s*=\s*['\"][^'\"]*['\"]", line) and ".append(" not in line:
                out.append(Finding(
                    file=file, line=i, severity="info", category="performance",
                    message="String concatenation in a loop may be O(n^2).",
                    evidence=line.rstrip(),
                ))
        return out

    def _style_check(self, file: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        for i, line in enumerate(lines, 1):
            if len(line) > 120:
                out.append(Finding(
                    file=file, line=i, severity="info", category="style",
                    message=f"Line exceeds 120 chars ({len(line)}).",
                    evidence=line[:100] + "..." if len(line) > 100 else line.rstrip(),
                ))
            if "TODO" in line or "FIXME" in line:
                out.append(Finding(
                    file=file, line=i, severity="info", category="style",
                    message="Unresolved TODO/FIXME.",
                    evidence=line.rstrip(),
                ))
        return out

    def _tests_check(self, file: str, lines: list[str]) -> list[Finding]:
        out: list[Finding] = []
        if "def test_" in "\n".join(lines) or "it(" in "\n".join(lines):
            # Test file: check for empty tests
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if re.match(r"(def test_\w+\([^)]*\):|it\([^)]*,\s*\(\)\s*=>\s*\{)\s*$", stripped):
                    if i < len(lines) and lines[i].strip() in ("pass", "}", "return"):
                        out.append(Finding(
                            file=file, line=i, severity="warning", category="tests",
                            message="Test appears to be empty.",
                            evidence=stripped,
                        ))
        else:
            # Non-test file: find public functions without obvious tests
            try:
                tree = ast.parse("\n".join(lines))
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and not node.name.startswith("_"):
                        hits = self.ws.search_code(node.name)
                        test_hits = [h for h in hits if "test" in h["file"].lower()]
                        if not test_hits:
                            out.append(Finding(
                                file=file, line=node.lineno, severity="info",
                                category="tests",
                                message=f"No test references found for `{node.name}`.",
                                evidence=ast.unparse(node) if hasattr(ast, "unparse") else node.name,
                            ))
            except SyntaxError:
                pass
        return out

    # ------------------------------------------------------------------ #
    # Diff-level review
    # ------------------------------------------------------------------ #

    def _review_diff_text(self, diff: str,
                          checks: Optional[list[str]]) -> list[Finding]:
        findings: list[Finding] = []
        current_file = None
        current_line = 0
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
            elif line.startswith("@@"):
                m = re.match(r"@@ -\d+,\d+ \+(\d+),\d+ @@", line)
                if m:
                    current_line = int(m.group(1))
            elif line.startswith("+") and not line.startswith("+++"):
                if current_file:
                    for check in (checks or ["bugs", "security"]):
                        for f in self._run_check(current_file, check):
                            if f.line == current_line:
                                findings.append(f)
                current_line += 1
        return findings

    # ------------------------------------------------------------------ #
    # Format
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format(findings: list[Finding], scope: str, files: list[str]) -> dict:
        by_sev = {"error": 0, "warning": 0, "info": 0}
        for f in findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        return {
            "scope": scope,
            "files": files,
            "summary": {
                "total": len(findings),
                "errors": by_sev.get("error", 0),
                "warnings": by_sev.get("warning", 0),
                "info": by_sev.get("info", 0),
            },
            "findings": [
                {
                    "file": f.file, "line": f.line,
                    "severity": f.severity, "category": f.category,
                    "message": f.message, "evidence": f.evidence,
                    "suggestion": f.suggestion,
                }
                for f in sorted(findings, key=lambda x: (
                    {"error": 0, "warning": 1, "info": 2}[x.severity],
                    x.file, x.line,
                ))
            ],
        }

    # ------------------------------------------------------------------ #
    # Convenience wrappers
    # ------------------------------------------------------------------ #

    def find_bugs(self, scope: str = "diff", target: Optional[str] = None) -> dict:
        if scope == "diff":
            return self.review_changes(checks=["bugs"])
        if scope == "file" and target:
            return self.review_file(target, checks=["bugs"])
        return {"error": "invalid scope"}

    def find_security_issues(self, scope: str = "diff",
                             target: Optional[str] = None) -> dict:
        if scope == "diff":
            return self.review_changes(checks=["security"])
        if scope == "file" and target:
            return self.review_file(target, checks=["security"])
        return {"error": "invalid scope"}

    def find_regressions(self, ref: str = "HEAD~1") -> dict:
        """Run tests, then check whether any changed file broke something."""
        return {"scope": f"since {ref}", "findings": []}  # filled by the agent loop