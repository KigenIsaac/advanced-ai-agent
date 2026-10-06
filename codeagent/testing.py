"""
testing.py — Test discovery and execution, plus static analysis.

Layer 14 Testing
Layer 15 Static analysis
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Optional

from .workspace import Workspace
from .shell import Shell


class Testing:
    def __init__(self, workspace: Workspace, shell: Shell):
        self.ws = workspace
        self.sh = shell

    def framework(self) -> Optional[str]:
        root = self.ws.root
        if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() \
                or (root / "setup.cfg").exists() or (root / "tests").is_dir():
            if shutil.which("pytest"):
                return "pytest"
            return "unittest"
        if (root / "package.json").exists():
            try:
                pkg = json.loads((root / "package.json").read_text())
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "jest" in deps:
                    return "jest"
                if "vitest" in deps:
                    return "vitest"
                if "mocha" in deps:
                    return "mocha"
            except Exception:
                pass
        if (root / "Cargo.toml").exists():
            return "cargo"
        if (root / "go.mod").exists():
            return "go"
        return None

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #

    def discover(self, path: str = ".") -> list[str]:
        base = self.ws._resolve(path)
        fw = self.framework()
        patterns = {
            "pytest": ["test_*.py", "*_test.py"],
            "unittest": ["test_*.py", "*_test.py"],
            "jest": ["*.test.js", "*.test.ts", "*.test.jsx", "*.test.tsx",
                     "*.spec.js", "*.spec.ts"],
            "vitest": ["*.test.ts", "*.test.tsx", "*.spec.ts"],
            "mocha": ["*.spec.js", "*.test.js"],
            "cargo": ["*.rs"],
            "go": ["*_test.go"],
        }.get(fw or "", ["test_*.py"])
        out: list[str] = []
        for pat in patterns:
            for f in base.rglob(pat):
                if self.ws._is_ignored(f):
                    continue
                out.append(self.ws._rel(f))
        return sorted(set(out))

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    async def run(self, *, path: Optional[str] = None,
                  test_name: Optional[str] = None,
                  timeout: int = 300, extra: Optional[str] = None) -> dict:
        fw = self.framework()
        cmd = self._command(fw, path, test_name, extra)
        if not cmd:
            return {"error": f"no test framework detected"}
        result = await self.sh.run(cmd, timeout=timeout)
        result["framework"] = fw
        result["failures"] = self._extract_failures(result["stdout"] + result["stderr"], fw)
        return result

    async def run_file(self, file: str) -> dict:
        return await self.run(path=file)

    async def run_case(self, name: str) -> dict:
        return await self.run(test_name=name)

    @staticmethod
    def _command(fw: Optional[str], path: Optional[str],
                 test_name: Optional[str], extra: Optional[str]) -> Optional[str]:
        suffix = f" {extra}" if extra else ""
        if fw in ("pytest", "unittest"):
            target = path or test_name or ""
            return f"pytest -v {target}{suffix}".strip()
        if fw == "jest":
            target = path or (f"-t '{test_name}'" if test_name else "")
            return f"npx jest {target}{suffix}".strip()
        if fw == "vitest":
            target = path or (f"-t '{test_name}'" if test_name else "")
            return f"npx vitest run {target}{suffix}".strip()
        if fw == "mocha":
            return f"npx mocha {path or ''}{suffix}".strip()
        if fw == "cargo":
            return f"cargo test {test_name or ''}{suffix}".strip()
        if fw == "go":
            return f"go test ./...{suffix}".strip()
        return None

    # ------------------------------------------------------------------ #
    # Failure extraction
    # ------------------------------------------------------------------ #

    def _extract_failures(self, output: str, fw: Optional[str]) -> list[dict]:
        failures: list[dict] = []

        if fw in ("pytest", "unittest"):
            for m in re.finditer(
                r"^(FAILED|ERROR)\s+(?P<test>\S+)(?:\s+-\s+(?P<msg>.*))?$",
                output, re.M
            ):
                failures.append({
                    "test": m.group("test"),
                    "status": m.group(1),
                    "message": (m.group("msg") or "").strip(),
                })
            return failures

        if fw in ("jest", "vitest", "mocha"):
            for m in re.finditer(r"^\s*(✕|×|✗)\s+(?P<test>.+)$", output, re.M):
                failures.append({"test": m.group("test").strip(), "status": "FAILED"})
            return failures

        if fw == "cargo":
            for m in re.finditer(r"^test (?P<test>\S+) \.\.\. FAILED", output, re.M):
                failures.append({"test": m.group("test"), "status": "FAILED"})
            return failures

        if fw == "go":
            for m in re.finditer(r"^--- FAIL: (?P<test>\S+)", output, re.M):
                failures.append({"test": m.group("test"), "status": "FAILED"})
            return failures

        return failures

    # ------------------------------------------------------------------ #
    # Coverage
    # ------------------------------------------------------------------ #

    async def coverage(self, source: str = ".", timeout: int = 300) -> dict:
        fw = self.framework()
        if fw in ("pytest", "unittest"):
            if not shutil.which("coverage"):
                return {"error": "coverage not installed"}
            r = await self.sh.run(f"coverage run -m pytest && coverage report -m",
                                  timeout=timeout)
            return r
        if fw in ("jest", "vitest"):
            r = await self.sh.run("npx jest --coverage" if fw == "jest"
                                  else "npx vitest run --coverage", timeout=timeout)
            return r
        return {"error": f"coverage not configured for {fw}"}

    # ------------------------------------------------------------------ #
    # Generation scaffold
    # ------------------------------------------------------------------ #

    def generate_test(self, target_file: str, output_file: Optional[str] = None) -> dict:
        """
        Write a minimal test scaffold. The agent is expected to fill in
        the assertions; this just removes the boilerplate burden.
        """
        p = self.ws._resolve(target_file)
        if not p.exists():
            return {"error": f"{target_file} not found"}
        name = p.stem
        if p.suffix == ".py":
            out = output_file or f"tests/test_{name}.py"
            content = (
                f'"""Tests for {target_file}."""\n'
                f"import pytest\n\n"
                f"def test_{name}_placeholder():\n"
                f"    # TODO: implement\n"
                f"    assert True\n"
            )
        elif p.suffix in (".ts", ".tsx", ".js", ".jsx"):
            out = output_file or f"{p.stem}.test{p.suffix}"
            content = (
                f"describe('{name}', () => {{\n"
                f"  it('placeholder', () => {{\n"
                f"    expect(true).toBe(true);\n"
                f"  }});\n"
                f"}});\n"
            )
        else:
            return {"error": f"unsupported file type {p.suffix}"}
        self.ws.write_file(out, content)
        return {"created": out}


# --------------------------------------------------------------------------- #

class StaticAnalysis:
    def __init__(self, workspace: Workspace, shell: Shell):
        self.ws = workspace
        self.sh = shell

    async def lint(self, path: str = ".") -> dict:
        root = self.ws.root
        # Python
        if any((root / n).exists() for n in
               ("pyproject.toml", "setup.py", "requirements.txt", "setup.cfg")):
            for tool in ("ruff", "flake8", "pylint"):
                if shutil.which(tool):
                    if tool == "ruff":
                        return await self.sh.run("ruff check . --output-format=json")
                    if tool == "flake8":
                        return await self.sh.run("flake8 .")
                    if tool == "pylint":
                        return await self.sh.run("pylint --output-format=json .")
        # Node
        if (root / "package.json").exists():
            try:
                pkg = json.loads((root / "package.json").read_text())
                if "lint" in pkg.get("scripts", {}):
                    return await self.sh.run("npm run lint")
                if (root / "node_modules" / ".bin" / "eslint").exists():
                    return await self.sh.run("npx eslint . --format json")
            except Exception:
                pass
        # Rust
        if (root / "Cargo.toml").exists():
            return await self.sh.run("cargo clippy --message-format=json")
        # Go
        if (root / "go.mod").exists():
            return await self.sh.run("go vet ./...")
        return {"skipped": True}

    async def security_scan(self, path: str = ".") -> dict:
        results = {}
        if shutil.which("bandit"):
            results["bandit"] = (await self.sh.run("bandit -r . -f json"))["stdout"]
        if shutil.which("semgrep"):
            results["semgrep"] = (await self.sh.run("semgrep --json --config=auto ."))["stdout"]
        if shutil.which("trivy"):
            results["trivy"] = (await self.sh.run("trivy fs --format json ."))["stdout"]
        return results or {"skipped": True}

    async def complexity(self, path: str = ".") -> dict:
        if shutil.which("radon"):
            return await self.sh.run("radon cc -j .")
        if shutil.which("lizard"):
            return await self.sh.run("lizard -l python .")
        return {"skipped": True}

    def dead_code(self, path: str = ".") -> dict:
        """Very conservative Python dead-code hint based on name search."""
        import ast
        root = self.ws._resolve(path)
        defined: dict[str, list[dict]] = {}
        for f in root.rglob("*.py"):
            if self.ws._is_ignored(f):
                continue
            try:
                tree = ast.parse(f.read_text(errors="replace"))
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name.startswith("_"):
                        continue
                    defined.setdefault(node.name, []).append(
                        {"file": self.ws._rel(f), "line": node.lineno})
        dead: list[dict] = []
        for name, defs in defined.items():
            hits = self.ws.search_code(name)
            # Only count uses that aren't the definition
            external = [h for h in hits
                        if not any(h["file"] == d["file"] and h["line"] == d["line"]
                                   for d in defs)]
            if not external:
                dead.extend(defs)
        return {"dead": dead}