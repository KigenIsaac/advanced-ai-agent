"""
shell.py — terminal execution with process management.

Layer 4  Terminal / shell
Layer 5  Higher-level command execution
"""
from __future__ import annotations

import asyncio
import os
import signal
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .workspace import Workspace


class ShellError(Exception): ...
class ShellTimeout(ShellError): ...


_IS_WINDOWS = sys.platform.startswith("win")


@dataclass
class Process:
    pid: int
    id: str
    command: str
    cwd: str
    started: float
    proc: asyncio.subprocess.Process
    stdout_log: list[str] = field(default_factory=list)
    stderr_log: list[str] = field(default_factory=list)
    _tasks: list[asyncio.Task] = field(default_factory=list)

    def status(self) -> str:
        if self.proc.returncode is None:
            return "running"
        return f"exited({self.proc.returncode})"


async def _kill_tree_async(pid: int) -> None:
    """Kill a process and all its descendants, cross-platform."""
    if _IS_WINDOWS:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(pid),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    killer.kill()
                except Exception:
                    pass
        except FileNotFoundError:
            pass
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except Exception:
            pass


def _kill_tree_sync(pid: int) -> None:
    """Synchronous version for cleanup paths."""
    if _IS_WINDOWS:
        try:
            import subprocess
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            pass


class Shell:
    def __init__(self, workspace: Workspace, default_timeout: int = 120,
                 max_output_bytes: int = 100_000):
        self.ws = workspace
        self.default_timeout = default_timeout
        self.max_output_bytes = max_output_bytes
        self._procs: dict[str, Process] = {}

    # ------------------------------------------------------------------ #
    # Foreground execution
    # ------------------------------------------------------------------ #

    async def run(self, command: str, cwd: Optional[str] = None,
                  timeout: Optional[int] = None,
                  env: Optional[dict] = None,
                  stdin: Optional[str] = None,
                  check: bool = False) -> dict:
        timeout = timeout or self.default_timeout
        workdir = str(self.ws._resolve(cwd)) if cwd else str(self.ws.root)
        merged_env = {**os.environ, **(env or {})}

        # A caller-provided stdin string is piped in explicitly. Otherwise
        # the child gets DEVNULL so it can never race prompt_toolkit for
        # console keystrokes.
        stdin_arg = (
            asyncio.subprocess.PIPE
            if stdin
            else asyncio.subprocess.DEVNULL
        )

        started = time.time()

        try:
            if _IS_WINDOWS:
                creationflags = 0
                try:
                    import subprocess as _sp
                    creationflags = getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0)
                except Exception:
                    creationflags = 0
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=workdir,
                    env=merged_env,
                    stdin=stdin_arg,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=workdir,
                    env=merged_env,
                    stdin=stdin_arg,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
        except FileNotFoundError as e:
            raise ShellError(str(e)) from e

        timed_out = False
        stdout_b = b""
        stderr_b = b""

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(stdin.encode() if stdin else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True

            try:
                await _kill_tree_async(proc.pid)
            except Exception:
                pass

            try:
                proc.kill()
            except ProcessLookupError:
                pass
            except Exception:
                pass

            async def _drain(stream) -> bytes:
                if stream is None:
                    return b""
                try:
                    return await asyncio.wait_for(stream.read(), timeout=2.0)
                except Exception:
                    return b""

            try:
                stdout_b = await _drain(proc.stdout)
            except Exception:
                stdout_b = b""

            try:
                stderr_b = await _drain(proc.stderr)
            except Exception:
                stderr_b = b""

            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

        duration = time.time() - started
        stdout = stdout_b.decode(errors="replace") if stdout_b else ""
        stderr = stderr_b.decode(errors="replace") if stderr_b else ""

        if timed_out:
            raise ShellTimeout(
                f"Command timed out after {timeout}s: {command}"
            )

        result = {
            "command": command,
            "cwd": workdir,
            "exit_code": proc.returncode,
            "stdout": stdout[:self.max_output_bytes],
            "stderr": stderr[:self.max_output_bytes],
            "duration": round(duration, 3),
            "truncated": len(stdout) > self.max_output_bytes,
        }
        if check and proc.returncode != 0:
            raise ShellError(
                f"Command failed ({proc.returncode}): {stderr[:500]}"
            )
        return result

    # ------------------------------------------------------------------ #
    # Background execution
    # ------------------------------------------------------------------ #

    async def run_background(self, command: str, cwd: Optional[str] = None,
                             env: Optional[dict] = None) -> str:
        workdir = str(self.ws._resolve(cwd)) if cwd else str(self.ws.root)
        merged_env = {**os.environ, **(env or {})}

        if _IS_WINDOWS:
            creationflags = 0
            try:
                import subprocess as _sp
                creationflags = getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0)
            except Exception:
                creationflags = 0
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workdir,
                env=merged_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=workdir,
                env=merged_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )

        proc_id = uuid.uuid4().hex[:8]
        p = Process(pid=proc.pid, id=proc_id, command=command,
                    cwd=workdir, started=time.time(), proc=proc)
        self._procs[proc_id] = p
        p._tasks.append(asyncio.create_task(self._pump(proc.stdout, p.stdout_log)))
        p._tasks.append(asyncio.create_task(self._pump(proc.stderr, p.stderr_log)))
        return proc_id

    async def _pump(self, stream: asyncio.StreamReader, log: list[str]):
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                log.append(line.decode(errors="replace"))
                if len(log) > 5000:
                    del log[:2000]
        except Exception:
            pass

    def list_processes(self) -> list[dict]:
        return [
            {"id": p.id, "pid": p.pid, "command": p.command, "cwd": p.cwd,
             "started": p.started, "status": p.status()}
            for p in self._procs.values()
        ]

    def get_process(self, proc_id: str) -> dict:
        p = self._procs.get(proc_id)
        if not p:
            raise ShellError(f"No process {proc_id}")
        return {
            "id": p.id, "pid": p.pid, "command": p.command, "cwd": p.cwd,
            "status": p.status(), "started": p.started,
            "stdout": "".join(p.stdout_log)[-self.max_output_bytes:],
            "stderr": "".join(p.stderr_log)[-self.max_output_bytes:],
            "exit_code": p.proc.returncode,
        }

    async def kill_process(self, proc_id: str, sig: int = signal.SIGTERM) -> dict:
        p = self._procs.get(proc_id)
        if not p:
            raise ShellError(f"No process {proc_id}")

        if p.proc.returncode is None:
            try:
                await _kill_tree_async(p.pid)
            except Exception:
                pass
            try:
                p.proc.kill()
            except ProcessLookupError:
                pass
            except Exception:
                pass
            try:
                await asyncio.wait_for(p.proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

        return {"id": proc_id, "status": p.status()}

    async def wait_process(self, proc_id: str, timeout: int = 60) -> dict:
        p = self._procs.get(proc_id)
        if not p:
            raise ShellError(f"No process {proc_id}")
        try:
            await asyncio.wait_for(p.proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise ShellTimeout(
                f"Process {proc_id} still running after {timeout}s"
            )
        return self.get_process(proc_id)

    async def close(self):
        for p in list(self._procs.values()):
            if p.proc.returncode is None:
                try:
                    await _kill_tree_async(p.pid)
                except Exception:
                    pass
                try:
                    p.proc.kill()
                except Exception:
                    pass
        for p in self._procs.values():
            for t in p._tasks:
                t.cancel()

    # ------------------------------------------------------------------ #
    # Layer 5 — command discovery helpers
    # ------------------------------------------------------------------ #

    def which(self, program: str) -> Optional[str]:
        return shutil.which(program)

    def has(self, program: str) -> bool:
        return self.which(program) is not None