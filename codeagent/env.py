"""
env.py — environment introspection and secret indirection.

Layer 10 Environment management
Layer 11 Environment variables and secrets
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


class Env:
    def __init__(self, secret_store: Optional[dict] = None):
        # Secret store is a name -> value mapping populated out-of-band.
        # The LLM never sees values; it only gets handles.
        self._secrets = secret_store or {}

    # ------------------------------------------------------------------ #
    # Layer 10 — environment
    # ------------------------------------------------------------------ #

    def get_os(self) -> dict:
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": sys.version,
            "python_executable": sys.executable,
        }

    def get_architecture(self) -> str:
        return platform.machine()

    def get_cpu_info(self) -> dict:
        return {"processor": platform.processor(),
                "cores": os.cpu_count()}

    def get_memory_info(self) -> dict:
        try:
            if platform.system() == "Linux":
                with open("/proc/meminfo") as f:
                    data = {k.strip(): v.strip() for k, v in
                            (line.split(":", 1) for line in f if ":" in line)}
                return {"total_kb": int(data["MemTotal"].split()[0]),
                        "available_kb": int(data["MemAvailable"].split()[0])}
        except Exception:
            pass
        return {}

    def get_disk_info(self, path: str = ".") -> dict:
        try:
            u = shutil.disk_usage(path)
            return {"total": u.total, "used": u.used, "free": u.free}
        except OSError:
            return {}

    def get_environment(self) -> dict:
        # Never expose values that look like secrets
        safe = {}
        for k, v in os.environ.items():
            if any(tok in k.upper() for tok in
                   ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "PWD")):
                safe[k] = "<redacted>"
            else:
                safe[k] = v
        return safe

    def get_runtime_versions(self) -> dict:
        return {
            "python": self.get_python_version(),
            "node": self._run_version(["node", "--version"]),
            "npm": self._run_version(["npm", "--version"]),
            "java": self._run_version(["java", "-version"], stderr=True),
            "go": self._run_version(["go", "version"]),
            "rustc": self._run_version(["rustc", "--version"]),
            "cargo": self._run_version(["cargo", "--version"]),
            "ruby": self._run_version(["ruby", "--version"]),
            "php": self._run_version(["php", "--version"]),
            "dotnet": self._run_version(["dotnet", "--version"]),
            "docker": self._run_version(["docker", "--version"]),
            "git": self._run_version(["git", "--version"]),
        }

    @staticmethod
    def _run_version(cmd: list[str], stderr: bool = False) -> Optional[str]:
        if not shutil.which(cmd[0]):
            return None
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return (r.stderr if stderr else r.stdout).strip().split("\n")[0] or None
        except Exception:
            return None

    def get_python_version(self) -> str:
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    def get_node_version(self) -> Optional[str]:
        return self._run_version(["node", "--version"])

    def get_java_version(self) -> Optional[str]:
        return self._run_version(["java", "-version"], stderr=True)

    def get_go_version(self) -> Optional[str]:
        return self._run_version(["go", "version"])

    def get_rust_version(self) -> Optional[str]:
        return self._run_version(["rustc", "--version"])

    # ------------------------------------------------------------------ #
    # Layer 11 — secret indirection
    # ------------------------------------------------------------------ #

    def register_secret(self, name: str, value: str) -> None:
        """Called by the host application, never by the LLM."""
        self._secrets[name] = value

    def has_secret(self, name: str) -> bool:
        return name in self._secrets or os.environ.get(name) is not None

    def use_secret(self, name: str) -> str:
        """
        Returns an opaque handle that a command can reference via ${name}
        in its env argument. The LLM never sees the value.
        """
        if name not in self._secrets and name not in os.environ:
            raise KeyError(f"No secret named {name}")
        return f"${{secret:{name}}}"

    def resolve_for_env(self, names: list[str]) -> dict[str, str]:
        """
        Called by the Shell before spawning a subprocess. The returned
        mapping is passed directly to the child; the LLM sees only the
        placeholder string.
        """
        out: dict[str, str] = {}
        for n in names:
            if n in self._secrets:
                out[n] = self._secrets[n]
            elif n in os.environ:
                out[n] = os.environ[n]
        return out