"""
shell.py — terminal execution with process management.

Layer 4  Terminal / shell
Layer 5  Higher-level command execution
"""
from __future__ import annotations

import asyncio
import codecs
from terminal_stream import background_finished, output_sink
import os
import signal
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
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
    stdout_log: str = ""
    stderr_log: str = ""
    finished: Optional[asyncio.Task] = None

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
            os.killpg(pid, signal.SIGKILL)
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

        sink = output_sink.get()
        captured = {"stdout": [], "stderr": []}
        lengths = {"stdout": 0, "stderr": 0}

        async def pump(stream, channel):
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                block = await stream.read(8192)
                text = decoder.decode(block, final=not block)
                remaining = self.max_output_bytes - lengths[channel]
                if remaining > 0 and text:
                    captured[channel].append(text[:remaining])
                lengths[channel] += len(text)
                if sink and text:
                    sink(channel, text)
                if not block:
                    break

        async def write_input():
            if proc.stdin:
                try:
                    proc.stdin.write(stdin.encode())
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

        tasks = [asyncio.create_task(coro) for coro in (
            pump(proc.stdout, "stdout"), pump(proc.stderr, "stderr"), write_input(), proc.wait()
        )]
        combined = asyncio.gather(*tasks)
        try:
            await asyncio.wait_for(asyncio.shield(combined), timeout=timeout)
        except BaseException as exc:
            await _kill_tree_async(proc.pid)
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=3)
            except asyncio.TimeoutError:
                pass
            finally:
                if combined.done() and not combined.cancelled():
                    combined.exception()
            if isinstance(exc, asyncio.TimeoutError):
                raise ShellTimeout(f"Command timed out after {timeout}s: {command}") from None
            raise
        duration = time.time() - started
        stdout = "".join(captured["stdout"])
        stderr = "".join(captured["stderr"])
        result = {
            "command": command,
            "cwd": workdir,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration": round(duration, 3),
            "truncated": max(lengths.values()) > self.max_output_bytes,
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
        p.finished = asyncio.create_task(self._follow(p))
        return proc_id

    async def _follow(self, process):
        sink = output_sink.get()
        finished = background_finished.get()

        async def pump(stream, channel):
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            attr = channel + "_log"
            while True:
                block = await stream.read(8192)
                text = decoder.decode(block, final=not block)
                if text:
                    setattr(process, attr, (getattr(process, attr) + text)[-self.max_output_bytes:])
                    if sink:
                        sink(channel, text)
                if not block:
                    return

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(pump(process.proc.stdout, "stdout"))
                group.create_task(pump(process.proc.stderr, "stderr"))
                group.create_task(process.proc.wait())
        finally:
            if finished:
                await finished(self.get_process(process.id))

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
            "stdout": p.stdout_log,
            "stderr": p.stderr_log,
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
            await asyncio.wait_for(asyncio.shield(p.finished), timeout=timeout)
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
        tasks = [p.finished for p in self._procs.values() if p.finished]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=3)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ #
    # Layer 5 — command discovery helpers
    # ------------------------------------------------------------------ #

    def which(self, program: str) -> Optional[str]:
        return shutil.which(program)

    def has(self, program: str) -> bool:
        return self.which(program) is not None