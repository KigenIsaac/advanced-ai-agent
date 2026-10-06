"""
packages.py — dependency and build system management.

Layer 9  Package management
Layer 16 Build system
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .workspace import Workspace
from .shell import Shell


class PackageError(Exception): ...


def detect_managers(root: Path) -> list[str]:
    managers = []
    if (root / "package.json").exists():
        managers.append("npm")
        if (root / "yarn.lock").exists():
            managers[0] = "yarn"
        elif (root / "pnpm-lock.yaml").exists():
            managers[0] = "pnpm"
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        if (root / "poetry.lock").exists():
            managers.append("poetry")
        elif (root / "uv.lock").exists():
            managers.append("uv")
        else:
            managers.append("pip")
    if (root / "Cargo.toml").exists():
        managers.append("cargo")
    if (root / "go.mod").exists():
        managers.append("go")
    if (root / "composer.json").exists():
        managers.append("composer")
    if (root / "Gemfile").exists():
        managers.append("bundler")
    if (root / "pom.xml").exists():
        managers.append("maven")
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        managers.append("gradle")
    if list(root.glob("*.csproj")) or list(root.glob("*.sln")):
        managers.append("dotnet")
    return managers


def detect_build_system(root: Path) -> Optional[str]:
    if (root / "package.json").exists():
        return "npm"
    if (root / "Cargo.toml").exists():
        return "cargo"
    if (root / "go.mod").exists():
        return "go"
    if (root / "pom.xml").exists():
        return "maven"
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        return "gradle"
    if list(root.glob("*.csproj")) or list(root.glob("*.sln")):
        return "dotnet"
    if (root / "Makefile").exists():
        return "make"
    if (root / "pyproject.toml").exists():
        return "python"
    return None


class Packages:
    def __init__(self, workspace: Workspace, shell: Shell):
        self.ws = workspace
        self.sh = shell

    def managers(self) -> list[str]:
        return detect_managers(self.ws.root)

    def build_system(self) -> Optional[str]:
        return detect_build_system(self.ws.root)

    # ------------------------------------------------------------------ #
    # Generic
    # ------------------------------------------------------------------ #

    async def list_dependencies(self) -> dict:
        mgrs = self.managers()
        result: dict = {}
        for m in mgrs:
            try:
                result[m] = await self._list_for(m)
            except Exception as e:
                result[m] = {"error": str(e)}
        return result

    async def install(self, package: str, manager: Optional[str] = None,
                      dev: bool = False) -> dict:
        m = manager or (self.managers() or ["pip"])[0]
        cmd = self._install_cmd(m, package, dev)
        return await self.sh.run(cmd)

    async def remove(self, package: str, manager: Optional[str] = None) -> dict:
        m = manager or (self.managers() or ["pip"])[0]
        cmd = self._remove_cmd(m, package)
        return await self.sh.run(cmd)

    async def update(self, package: Optional[str] = None,
                     manager: Optional[str] = None) -> dict:
        m = manager or (self.managers() or ["pip"])[0]
        cmd = self._update_cmd(m, package)
        return await self.sh.run(cmd)

    async def audit(self, manager: Optional[str] = None) -> dict:
        m = manager or (self.managers() or ["pip"])[0]
        cmd = {
            "npm": "npm audit --json",
            "yarn": "yarn npm audit --json || true",
            "pnpm": "pnpm audit --json || true",
            "pip": "pip-audit --format json || true",
            "poetry": "poetry run pip-audit --format json || true",
            "cargo": "cargo audit --json || true",
            "go": "govulncheck ./... || true",
            "bundler": "bundle audit check --update || true",
            "composer": "composer audit --format=json || true",
        }.get(m, "echo 'no auditor available'")
        r = await self.sh.run(cmd)
        return r

    # ------------------------------------------------------------------ #
    # Per-manager commands
    # ------------------------------------------------------------------ #

    async def _list_for(self, m: str) -> dict:
        cmd = {
            "npm": "npm ls --json --depth=0",
            "yarn": "yarn list --json --depth=0",
            "pnpm": "pnpm list --json --depth=0",
            "pip": "pip list --format=json",
            "poetry": "poetry show --json",
            "uv": "uv pip list --format=json",
            "cargo": "cargo metadata --format-version 1 --no-deps",
            "go": "go list -m all",
            "bundler": "bundle list",
            "composer": "composer show --format=json",
            "maven": "mvn -q dependency:list",
            "gradle": "gradle dependencies --quiet",
            "dotnet": "dotnet list package --format json",
        }.get(m)
        if not cmd:
            return {}
        r = await self.sh.run(cmd)
        return {"raw": r["stdout"][:20_000]}

    @staticmethod
    def _install_cmd(m: str, pkg: str, dev: bool) -> str:
        return {
            "npm": f"npm install {'--save-dev ' if dev else ''}{pkg}",
            "yarn": f"yarn add {'--dev ' if dev else ''}{pkg}",
            "pnpm": f"pnpm add {'--save-dev ' if dev else ''}{pkg}",
            "pip": f"pip install {pkg}",
            "poetry": f"poetry add {'--group dev ' if dev else ''}{pkg}",
            "uv": f"uv add {'--dev ' if dev else ''}{pkg}",
            "cargo": f"cargo add {pkg}",
            "go": f"go get {pkg}",
            "bundler": f"bundle add {pkg}",
            "composer": f"composer require {'--dev ' if dev else ''}{pkg}",
            "dotnet": f"dotnet add package {pkg}",
        }.get(m, f"echo 'unsupported manager {m}'")

    @staticmethod
    def _remove_cmd(m: str, pkg: str) -> str:
        return {
            "npm": f"npm uninstall {pkg}",
            "yarn": f"yarn remove {pkg}",
            "pnpm": f"pnpm remove {pkg}",
            "pip": f"pip uninstall -y {pkg}",
            "poetry": f"poetry remove {pkg}",
            "uv": f"uv remove {pkg}",
            "cargo": f"cargo remove {pkg}",
            "go": f"go get -u {pkg} && go mod tidy",
            "composer": f"composer remove {pkg}",
            "dotnet": f"dotnet remove package {pkg}",
        }.get(m, f"echo 'unsupported manager {m}'")

    @staticmethod
    def _update_cmd(m: str, pkg: Optional[str]) -> str:
        if pkg is None:
            return {
                "npm": "npm update",
                "yarn": "yarn upgrade",
                "pnpm": "pnpm update",
                "pip": "pip list --outdated",
                "poetry": "poetry update",
                "uv": "uv lock --upgrade",
                "cargo": "cargo update",
                "go": "go get -u ./... && go mod tidy",
                "bundler": "bundle update",
                "composer": "composer update",
            }.get(m, "echo")
        return {
            "npm": f"npm install {pkg}@latest",
            "yarn": f"yarn upgrade {pkg} --latest",
            "pnpm": f"pnpm update {pkg} --latest",
            "pip": f"pip install --upgrade {pkg}",
            "poetry": f"poetry add {pkg}@latest",
            "uv": f"uv add {pkg}@latest",
            "cargo": f"cargo update -p {pkg}",
            "go": f"go get {pkg}@latest",
            "composer": f"composer require {pkg}",
        }.get(m, "echo")


# --------------------------------------------------------------------------- #

class Build:
    def __init__(self, workspace: Workspace, shell: Shell):
        self.ws = workspace
        self.sh = shell

    def system(self) -> Optional[str]:
        return detect_build_system(self.ws.root)

    async def build(self) -> dict:
        s = self.system()
        if s is None:
            return {"error": "no build system detected"}
        cmd = {
            "npm": "npm run build",
            "cargo": "cargo build",
            "go": "go build ./...",
            "maven": "mvn package -DskipTests",
            "gradle": "gradle build -x test",
            "dotnet": "dotnet build",
            "make": "make",
            "python": "python -m build",
        }.get(s)
        if not cmd:
            return {"error": f"no build command for {s}"}
        return await self.sh.run(cmd, timeout=600)

    async def clean(self) -> dict:
        s = self.system()
        cmd = {
            "npm": "rm -rf dist build node_modules/.cache",
            "cargo": "cargo clean",
            "go": "go clean -cache",
            "maven": "mvn clean",
            "gradle": "gradle clean",
            "dotnet": "dotnet clean",
            "make": "make clean",
            "python": "rm -rf dist build *.egg-info",
        }.get(s or "", "echo")
        return await self.sh.run(cmd)

    async def typecheck(self) -> dict:
        # Language-aware typecheck
        if (self.ws.root / "tsconfig.json").exists():
            return await self.sh.run("npx --no-install tsc --noEmit || npx tsc --noEmit")
        if (self.ws.root / "pyproject.toml").exists() or \
           (self.ws.root / "mypy.ini").exists():
            if shutil.which("mypy"):
                return await self.sh.run("mypy . --no-error-summary")
        return {"skipped": True, "reason": "no typechecker detected"}