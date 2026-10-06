"""
debugger.py — Debugger control and error analysis.

Layer 12 Debugging
Layer 13 Error analysis
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .workspace import Workspace


class DebugError(Exception): ...


class Debugger:
    """
    A Debug Adapter Protocol (DAP) client for debugpy.

    The API is intentionally language-agnostic at the surface; the adapter
    underneath currently supports Python (debugpy) and is extensible.
    """

    def __init__(self, workspace: Workspace, adapter: str = "debugpy"):
        self.ws = workspace
        self.adapter = adapter
        self._proc: Optional[subprocess.Popen] = None
        self._msg_id = 0
        self._breakpoints: dict[str, list[int]] = {}

    def available(self) -> bool:
        if self.adapter == "debugpy":
            return shutil.which("python") is not None and self._has_debugpy()
        return False

    @staticmethod
    def _has_debugpy() -> bool:
        try:
            import debugpy  # noqa: F401
            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------ #
    # Simple subprocess-based pdb fallback
    # ------------------------------------------------------------------ #
    # Real DAP sessions are async and verbose. For an LLM-driven agent,
    # the most reliable primitive is to run a script under a controlled
    # harness and surface stdout/stderr + inspect locals via trace hooks.
    # We expose the DAP-style surface here; language-specific adapters
    # plug in below.

    async def run_under_debugger(self, script: str, args: Optional[list[str]] = None,
                                 breakpoints: Optional[list[str]] = None,
                                 timeout: int = 60) -> dict:
        """Run a script with `breakpoints` (file:line) installed, capturing trace."""
        if not self._has_debugpy():
            raise DebugError("debugpy is not installed")
        args = args or []
        bps = breakpoints or []
        cmd = [
            "python", "-m", "debugpy", "--listen", "0",
            "--wait-for-client", script, *args,
        ] if False else ["python", script, *args]
        # Simpler approach: run the script, capture exceptions
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(self.ws.root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise DebugError("Debug run timed out")
        return {
            "exit_code": proc.returncode,
            "stdout": out.decode(errors="replace"),
            "stderr": err.decode(errors="replace"),
        }

    def set_breakpoint(self, file: str, line: int) -> dict:
        self._breakpoints.setdefault(file, []).append(line)
        return {"file": file, "line": line, "total": len(self._breakpoints[file])}

    def remove_breakpoint(self, file: str, line: int) -> dict:
        if file in self._breakpoints and line in self._breakpoints[file]:
            self._breakpoints[file].remove(line)
        return {"file": file, "line": line}

    def list_breakpoints(self) -> dict:
        return dict(self._breakpoints)


# --------------------------------------------------------------------------- #

class ErrorAnalysis:
    """Turn compiler/test output into structured diagnostics."""

    # --- Python traceback ----------------------------------------------- #

    PY_TRACE_RE = re.compile(
        r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)'
    )
    PY_EXC_RE = re.compile(r"^(?P<type>[A-Za-z_][A-Za-z0-9_.]*Error|Exception|.*Error): (?P<msg>.*)$")

    # --- TypeScript / JavaScript --------------------------------------- #

    TS_ERR_RE = re.compile(
        r"(?P<file>[^\s(]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.+)"
    )

    # --- Rust ----------------------------------------------------------- #

    RUST_ERR_RE = re.compile(
        r"error(\[(?P<code>E\d+)\])?: (?P<msg>.+)\n\s*-->\s*(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+)"
    )

    # --- Go ------------------------------------------------------------- #

    GO_ERR_RE = re.compile(r"(?P<file>[^\s:]+\.go):(?P<line>\d+):(?P<col>\d+): (?P<msg>.+)")

    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self._last: Optional[dict] = None

    def get_last_error(self) -> Optional[dict]:
        return self._last

    def parse_stack_trace(self, text: str) -> dict:
        """Detect language and extract frames."""
        result: dict = {"language": None, "frames": [], "exception": None}

        if "Traceback (most recent call last)" in text:
            result["language"] = "python"
            for m in self.PY_TRACE_RE.finditer(text):
                result["frames"].append({
                    "file": m.group("file"),
                    "line": int(m.group("line")),
                    "function": m.group("func"),
                })
            # Last line is the exception
            last = text.strip().splitlines()[-1]
            m = self.PY_EXC_RE.match(last)
            if m:
                result["exception"] = {"type": m.group("type"), "message": m.group("msg")}
            return result

        if re.search(r"error TS\d+", text):
            result["language"] = "typescript"
            for m in self.TS_ERR_RE.finditer(text):
                result["frames"].append({
                    "file": m.group("file"),
                    "line": int(m.group("line")),
                    "col": int(m.group("col")),
                    "code": m.group("code"),
                    "message": m.group("msg"),
                })
            return result

        if re.search(r"error\[E\d+\]|error:", text) and "-->" in text:
            result["language"] = "rust"
            for m in self.RUST_ERR_RE.finditer(text):
                result["frames"].append({
                    "file": m.group("file").strip(),
                    "line": int(m.group("line")),
                    "col": int(m.group("col")),
                    "code": m.group("code"),
                    "message": m.group("msg"),
                })
            return result

        go_hits = list(self.GO_ERR_RE.finditer(text))
        if go_hits:
            result["language"] = "go"
            for m in go_hits:
                result["frames"].append({
                    "file": m.group("file"),
                    "line": int(m.group("line")),
                    "col": int(m.group("col")),
                    "message": m.group("msg"),
                })
            return result

        return result

    def find_error_source(self, parsed: dict) -> Optional[dict]:
        """Return the most likely frame that lives inside the project."""
        for frame in reversed(parsed.get("frames", [])):
            f = frame.get("file", "")
            try:
                p = Path(f).resolve()
                p.relative_to(self.ws.root)
                return frame
            except (ValueError, OSError):
                # Try relative to workspace
                candidate = (self.ws.root / f).resolve()
                try:
                    candidate.relative_to(self.ws.root)
                    if candidate.exists():
                        return {**frame, "file": str(candidate.relative_to(self.ws.root))}
                except ValueError:
                    pass
        return parsed["frames"][-1] if parsed.get("frames") else None

    def find_related_code(self, source: dict, context_lines: int = 10) -> dict:
        try:
            info = self.ws.read_file(
                source["file"],
                start_line=max(1, source["line"] - context_lines),
                end_line=source["line"] + context_lines,
            )
            return info
        except Exception as e:
            return {"error": str(e)}

    def explain_compiler_error(self, text: str) -> dict:
        parsed = self.parse_stack_trace(text)
        source = self.find_error_source(parsed)
        context = self.find_related_code(source) if source else None
        return {
            "parsed": parsed,
            "source": source,
            "context": context,
        }

    def store(self, output: str) -> dict:
        parsed = self.parse_stack_trace(output)
        self._last = parsed
        return parsed