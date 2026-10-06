"""
sandbox.py — Isolated execution environments.

Layer 28 Container / VM management

Supports two backends:
  - `subprocess` (default): a stripped-env child process with a temp cwd.
  - `docker`: run inside a fresh container (requires docker).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .workspace import Workspace


class SandboxError(Exception): ...


@dataclass
class Sandbox:
    id: str
    backend: str
    root: Path
    image: Optional[str] = None
    container_id: Optional[str] = None
    created: float = field(default_factory=lambda: __import__("time").time())


class Sandboxes:
    def __init__(self, workspace: Workspace, default_image: str = "python:3.12-slim"):
        self.ws = workspace
        self.default_image = default_image
        self._sandboxes: dict[str, Sandbox] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def create(self, backend: str = "subprocess",
               image: Optional[str] = None) -> dict:
        sid = uuid.uuid4().hex[:8]
        if backend == "docker":
            if not shutil.which("docker"):
                raise SandboxError("docker not installed")
            return self._create_docker(sid, image or self.default_image)
        return self._create_subprocess(sid)

    def _create_subprocess(self, sid: str) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix=f"codeagent-{sid}-"))
        self._sandboxes[sid] = Sandbox(id=sid, backend="subprocess", root=tmp)
        return {"id": sid, "backend": "subprocess", "root": str(tmp)}

    def _create_docker(self, sid: str, image: str) -> dict:
        import subprocess
        r = subprocess.run(
            ["docker", "run", "-d", "--rm",
             "-v", f"{self.ws.root}:/workspace",
             "-w", "/workspace",
             image, "sleep", "infinity"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise SandboxError(r.stderr)
        cid = r.stdout.strip()
        self._sandboxes[sid] = Sandbox(
            id=sid, backend="docker", root=self.ws.root,
            image=image, container_id=cid,
        )
        return {"id": sid, "backend": "docker", "container_id": cid, "image": image}

    async def destroy(self, sid: str) -> dict:
        sb = self._sandboxes.pop(sid, None)
        if not sb:
            return {"destroyed": False}
        if sb.backend == "docker" and sb.container_id:
            import subprocess
            subprocess.run(["docker", "rm", "-f", sb.container_id],
                           capture_output=True)
        elif sb.backend == "subprocess":
            shutil.rmtree(sb.root, ignore_errors=True)
        return {"destroyed": True}

    def get(self, sid: str) -> Optional[dict]:
        sb = self._sandboxes.get(sid)
        if not sb:
            return None
        return {"id": sb.id, "backend": sb.backend, "root": str(sb.root),
                "image": sb.image, "container_id": sb.container_id}

    def list(self) -> list[dict]:
        return [self.get(sid) for sid in self._sandboxes]

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    async def exec(self, sid: str, command: str,
                   cwd: Optional[str] = None, timeout: int = 120,
                   env: Optional[dict] = None) -> dict:
        sb = self._sandboxes.get(sid)
        if not sb:
            raise SandboxError(f"No sandbox {sid}")

        if sb.backend == "docker":
            args = ["docker", "exec"]
            if cwd:
                args += ["-w", cwd]
            for k, v in (env or {}).items():
                args += ["-e", f"{k}={v}"]
            args += [sb.container_id, "sh", "-c", command]
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            workdir = cwd or str(sb.root)
            clean_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(sb.root),
                "LANG": "C.UTF-8",
                **(env or {}),
            }
            proc = await asyncio.create_subprocess_shell(
                command, cwd=workdir, env=clean_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise SandboxError(f"Sandbox command timed out after {timeout}s")

        return {
            "exit_code": proc.returncode,
            "stdout": out.decode(errors="replace"),
            "stderr": err.decode(errors="replace"),
        }

    # ------------------------------------------------------------------ #
    # Snapshots (subprocess backend only)
    # ------------------------------------------------------------------ #

    def snapshot(self, sid: str) -> dict:
        import tarfile
        sb = self._sandboxes.get(sid)
        if not sb or sb.backend != "subprocess":
            raise SandboxError("Snapshots only supported for subprocess backend")
        snap = sb.root.parent / f"{sb.id}.snapshot.tar"
        with tarfile.open(snap, "w") as tar:
            tar.add(sb.root, arcname=".")
        return {"snapshot": str(snap)}

    def restore(self, sid: str, snapshot: str) -> dict:
        import tarfile
        sb = self._sandboxes.get(sid)
        if not sb or sb.backend != "subprocess":
            raise SandboxError("Snapshots only supported for subprocess backend")
        shutil.rmtree(sb.root, ignore_errors=True)
        sb.root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(snapshot) as tar:
            tar.extractall(sb.root)
        return {"restored": str(sb.root)}