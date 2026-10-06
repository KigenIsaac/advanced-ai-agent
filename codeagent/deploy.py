"""
deploy.py — Deployment and monitoring hooks.

Layers 29 and 30 are intentionally thin. Real deployments vary wildly by
provider, so this module defines a hook-based interface plus sensible
defaults for common platforms. Deployment ALWAYS requires explicit
authorization from the caller — the agent cannot deploy without an
out-of-band confirmation token.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from .workspace import Workspace
from .shell import Shell


class DeployForbidden(Exception): ...


@dataclass
class Deployment:
    provider: str
    status: str
    url: Optional[str] = None
    logs: str = ""


class Deployment:
    def __init__(self, workspace: Workspace, shell: Shell,
                 authorization: Optional[Callable[[str], bool]] = None):
        self.ws = workspace
        self.sh = shell
        # `authorization(action)` is called before any destructive action.
        # Default: refuse. The host application must supply a policy.
        self.authorization = authorization or (lambda _: False)

    def _require_auth(self, action: str):
        if not self.authorization(action):
            raise DeployForbidden(
                f"Deployment action '{action}' was not authorized by the host."
            )

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #

    def detect(self) -> list[str]:
        root = self.ws.root
        found: list[str] = []
        if (root / "vercel.json").exists() or (root / ".vercel").is_dir():
            found.append("vercel")
        if (root / "netlify.toml").exists():
            found.append("netlify")
        if (root / "fly.toml").exists():
            found.append("fly")
        if (root / "render.yaml").exists():
            found.append("render")
        if (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists():
            found.append("docker")
        if list(root.glob(".github/workflows/*.y*ml")):
            found.append("github-actions")
        if (root / "Procfile").exists():
            found.append("heroku")
        if (root / "k8s").is_dir() or list(root.glob("*.k8s.yaml")):
            found.append("kubernetes")
        return found

    # ------------------------------------------------------------------ #
    # Build artifacts
    # ------------------------------------------------------------------ #

    async def build(self) -> dict:
        return await self.sh.run("npm run build || true")

    # ------------------------------------------------------------------ #
    # Deploy
    # ------------------------------------------------------------------ #

    async def deploy(self, provider: Optional[str] = None,
                     environment: str = "staging") -> dict:
        providers = self.detect() if not provider else [provider]
        if not providers:
            return {"error": "no deployment provider detected"}
        provider = providers[0]
        self._require_auth(f"deploy:{provider}:{environment}")

        if provider == "vercel" and shutil.which("vercel"):
            return await self.sh.run(f"vercel deploy --yes")
        if provider == "netlify" and shutil.which("netlify"):
            return await self.sh.run("netlify deploy --build --prod" if environment == "production"
                                     else "netlify deploy --build")
        if provider == "fly" and shutil.which("flyctl"):
            return await self.sh.run("flyctl deploy")
        if provider == "heroku" and shutil.which("git"):
            return await self.sh.run("git push heroku HEAD:main")
        if provider == "github-actions" and shutil.which("gh"):
            return await self.sh.run("gh workflow run deploy.yml")
        if provider == "docker":
            return await self.sh.run("docker build -t app .")
        return {"error": f"provider {provider} not usable in this environment"}

    async def status(self, provider: Optional[str] = None) -> dict:
        provider = provider or (self.detect() or [None])[0]
        if provider == "vercel" and shutil.which("vercel"):
            return await self.sh.run("vercel ls")
        if provider == "fly" and shutil.which("flyctl"):
            return await self.sh.run("flyctl status")
        return {"status": "unknown", "provider": provider}

    async def logs(self, provider: Optional[str] = None, tail: int = 200) -> dict:
        provider = provider or (self.detect() or [None])[0]
        if provider == "fly" and shutil.which("flyctl"):
            return await self.sh.run(f"flyctl logs")
        if provider == "vercel" and shutil.which("vercel"):
            return await self.sh.run("vercel logs")
        return {"logs": "", "provider": provider}

    async def rollback(self, provider: Optional[str] = None,
                       target: Optional[str] = None) -> dict:
        self._require_auth(f"rollback:{provider}")
        if provider == "vercel" and shutil.which("vercel"):
            return await self.sh.run("vercel rollback")
        if provider == "fly" and shutil.which("flyctl") and target:
            return await self.sh.run(f"flyctl releases rollback {target}")
        return {"error": "rollback not supported for this provider"}


class Monitoring:
    def __init__(self, workspace: Workspace, shell: Shell):
        self.ws = workspace
        self.sh = shell

    async def health(self, url: str) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                return {"status": r.status_code, "ok": r.is_success,
                        "body": r.text[:500]}
        except Exception as e:
            return {"error": str(e)}

    async def metrics(self, url: str) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                return {"status": r.status_code, "body": r.text[:5000]}
        except Exception as e:
            return {"error": str(e)}