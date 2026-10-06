"""
runtime.py — Running applications and inspecting them at runtime.

Layer 17 Running applications
Layer 18 Browser testing
Layer 19 Browser console/debugging
Layer 20 Database
Layer 21 API testing
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from .shell import Shell
from .workspace import Workspace


# --------------------------------------------------------------------------- #
# Layer 17 — Application lifecycle
# --------------------------------------------------------------------------- #

class Application:
    def __init__(self, workspace: Workspace, shell: Shell):
        self.ws = workspace
        self.sh = shell
        self._proc_id: Optional[str] = None
        self._port: Optional[int] = None

    def detect_start_command(self) -> Optional[str]:
        root = self.ws.root
        if (root / "package.json").exists():
            try:
                pkg = json.loads((root / "package.json").read_text())
                scripts = pkg.get("scripts", {})
                for key in ("dev", "start", "serve"):
                    if key in scripts:
                        return f"npm run {key}"
            except Exception:
                pass
        if (root / "manage.py").exists():
            return "python manage.py runserver"
        if (root / "main.py").exists():
            return "python main.py"
        if (root / "app.py").exists():
            return "python app.py"
        if (root / "Cargo.toml").exists():
            return "cargo run"
        if (root / "go.mod").exists():
            return "go run ."
        return None

    async def start(self, command: Optional[str] = None,
                    port: Optional[int] = None, env: Optional[dict] = None) -> dict:
        cmd = command or self.detect_start_command()
        if not cmd:
            return {"error": "no start command detected"}
        if port:
            env = {**(env or {}), "PORT": str(port)}
            self._port = port
        self._proc_id = await self.sh.run_background(cmd, env=env)
        # Give the server a moment
        await asyncio.sleep(2)
        return {"process_id": self._proc_id, "command": cmd, "port": self._port}

    async def stop(self) -> dict:
        if not self._proc_id:
            return {"stopped": False}
        result = await self.sh.kill_process(self._proc_id)
        self._proc_id = None
        return result

    async def restart(self, **kw) -> dict:
        await self.stop()
        await asyncio.sleep(1)
        return await self.start(**kw)

    def status(self) -> dict:
        if not self._proc_id:
            return {"running": False}
        p = self.sh.get_process(self._proc_id)
        return {"running": p["status"] == "running",
                "process_id": self._proc_id,
                "port": self._port}

    def logs(self, tail: int = 200) -> dict:
        if not self._proc_id:
            return {"stdout": "", "stderr": ""}
        p = self.sh.get_process(self._proc_id)
        out = p["stdout"].splitlines()[-tail:]
        err = p["stderr"].splitlines()[-tail:]
        return {"stdout": "\n".join(out), "stderr": "\n".join(err)}

    @staticmethod
    def port_free(port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex((host, port)) != 0

    @staticmethod
    def find_free_port(start: int = 3000, end: int = 9999) -> int:
        for p in range(start, end):
            if Application.port_free(p):
                return p
        raise RuntimeError("No free port found")

    def get_port(self) -> Optional[int]:
        return self._port


# --------------------------------------------------------------------------- #
# Layer 18/19 — Browser bridge
# --------------------------------------------------------------------------- #
# This is deliberately thin: it accepts any object that exposes the
# web_browser(action, **kw) coroutine, so the user can plug in the
# browser engine from the previous chapter without a hard dependency.

class BrowserBridge:
    def __init__(self, browser: Any):
        """`browser` must expose `async def web_browser(action, **kw)`."""
        self.browser = browser

    async def open(self, url: str) -> dict:
        return await self.browser.web_browser(action="open", url=url)

    async def click(self, selector: str) -> dict:
        return await self.browser.web_browser(action="click", selector=selector)

    async def fill(self, selector: str, text: str) -> dict:
        return await self.browser.web_browser(action="fill", selector=selector, text=text)

    async def screenshot(self, full_page: bool = False) -> dict:
        return await self.browser.web_browser(action="screenshot", full_page=full_page)

    async def console_logs(self) -> list[dict]:
        # Assumes the browser engine has been configured to capture console events.
        result = await self.browser.web_browser(action="get_console_logs")
        return result.get("logs", [])

    async def network_logs(self) -> list[dict]:
        result = await self.browser.web_browser(action="get_network")
        return result.get("entries", [])

    def get_console_errors(self, logs: list[dict]) -> list[dict]:
        return [l for l in logs if l.get("level") in ("error", "assert")]

    def get_console_warnings(self, logs: list[dict]) -> list[dict]:
        return [l for l in logs if l.get("level") == "warning"]

    def get_failed_requests(self, entries: list[dict]) -> list[dict]:
        return [e for e in entries
                if e.get("kind") == "response" and e.get("status", 200) >= 400]


# --------------------------------------------------------------------------- #
# Layer 20 — Database
# --------------------------------------------------------------------------- #

class Database:
    def __init__(self, workspace: Workspace, shell: Shell):
        self.ws = workspace
        self.sh = shell
        self._connections: dict[str, Any] = {}

    def detect(self) -> list[str]:
        root = self.ws.root
        found: list[str] = []
        # SQLAlchemy / Django
        for py in root.rglob("*.py"):
            if self.ws._is_ignored(py):
                continue
            try:
                text = py.read_text(errors="replace")
            except OSError:
                continue
            if "postgres" in text.lower():
                found.append("postgres")
            if "mysql" in text.lower():
                found.append("mysql")
            if "sqlite" in text.lower():
                found.append("sqlite")
            if "mongo" in text.lower():
                found.append("mongodb")
            if found:
                break
        # Node
        pkg = root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                for name, engine in (("pg", "postgres"), ("mysql2", "mysql"),
                                     ("mongoose", "mongodb"), ("sqlite3", "sqlite")):
                    if name in deps:
                        found.append(engine)
            except Exception:
                pass
        # .env / docker-compose
        for name in (".env", ".env.example", "docker-compose.yml", "docker-compose.yaml"):
            p = root / name
            if p.exists():
                text = p.read_text(errors="replace").lower()
                for eng in ("postgres", "mysql", "mongo", "redis", "sqlite"):
                    if eng in text:
                        found.append(eng)
        return sorted(set(found))

    def connect_sqlite(self, path: str) -> None:
        import sqlite3
        self._connections[f"sqlite:{path}"] = sqlite3.connect(
            str(self.ws._resolve(path))
        )

    def _sqlite(self) -> "sqlite3.Connection":
        for k, c in self._connections.items():
            if k.startswith("sqlite:"):
                return c
        raise RuntimeError("No SQLite connection")

    def list_tables(self) -> list[str]:
        cur = self._sqlite().execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r[0] for r in cur.fetchall()]

    def describe_table(self, table: str) -> list[dict]:
        cur = self._sqlite().execute(f"PRAGMA table_info({table})")
        return [{"cid": r[0], "name": r[1], "type": r[2],
                 "notnull": bool(r[3]), "default": r[4], "pk": bool(r[5])}
                for r in cur.fetchall()]

    def execute_query(self, query: str, readonly: bool = True,
                      limit: int = 1000) -> dict:
        if readonly:
            upper = query.strip().upper()
            if not upper.startswith(("SELECT", "PRAGMA", "EXPLAIN", "WITH")):
                raise PermissionError("Read-only mode: only SELECT/PRAGMA/EXPLAIN/WITH allowed")
        cur = self._sqlite().execute(query)
        if cur.description:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit)
            return {"columns": cols, "rows": rows, "count": len(rows)}
        self._sqlite().commit()
        return {"rowcount": cur.rowcount}

    def get_schema(self) -> dict:
        return {t: self.describe_table(t) for t in self.list_tables()}

    def find_migrations(self) -> list[str]:
        out: list[str] = []
        for pat in ("**/migrations/*.py", "**/migrations/*.sql",
                    "**/versions/*.py", "db/migrate/*.rb"):
            for f in self.ws.root.glob(pat):
                if self.ws._is_ignored(f):
                    continue
                out.append(self.ws._rel(f))
        return sorted(out)

    def find_database_usage(self, model: str) -> list[dict]:
        return self.ws.search_code(model)


# --------------------------------------------------------------------------- #
# Layer 21 — HTTP / API testing
# --------------------------------------------------------------------------- #

class HTTP:
    def __init__(self, timeout: float = 30.0):
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def close(self):
        await self._client.aclose()

    async def request(self, method: str, url: str,
                      headers: Optional[dict] = None,
                      params: Optional[dict] = None,
                      body: Any = None,
                      json_body: Any = None,
                      cookies: Optional[dict] = None) -> dict:
        started = time.time()
        r = await self._client.request(
            method.upper(), url,
            headers=headers, params=params,
            content=body if isinstance(body, (str, bytes)) else None,
            json=json_body, cookies=cookies,
        )
        elapsed = time.time() - started
        try:
            parsed = r.json()
        except Exception:
            parsed = None
        return {
            "status": r.status_code,
            "headers": dict(r.headers),
            "text": r.text[:50_000],
            "json": parsed,
            "latency": round(elapsed, 3),
        }

    async def test_endpoint(self, method: str, url: str,
                            expected_status: Optional[int] = None, **kw) -> dict:
        result = await self.request(method, url, **kw)
        ok = True
        if expected_status is not None:
            ok = result["status"] == expected_status
        return {**result, "ok": ok}

    async def compare(self, url_a: str, url_b: str,
                      method: str = "GET", **kw) -> dict:
        a = await self.request(method, url_a, **kw)
        b = await self.request(method, url_b, **kw)
        return {
            "status_match": a["status"] == b["status"],
            "a_status": a["status"],
            "b_status": b["status"],
            "body_equal": a["text"] == b["text"],
        }

    def get_api_routes(self) -> list[dict]:
        # Heuristic: search for common route decorators
        patterns = [
            r"@(app|router|bp)\.(get|post|put|delete|patch)\(",
            r"@(Get|Post|Put|Delete|Patch|Request)Mapping",
            r"router\.(get|post|put|delete|patch)\(",
            r'\.(get|post|put|delete|patch)\([\'"]',
        ]
        hits: list[dict] = []
        for pat in patterns:
            hits.extend(Workspace.grep(
                _wrap_ws(self), pat, max_results=500))
        return hits


def _wrap_ws(_self) -> Workspace:  # placeholder for grep dispatch
    raise NotImplementedError