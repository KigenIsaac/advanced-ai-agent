"""Loopback browser transport for a single existing agent App."""

import asyncio
import contextlib
import json
import secrets
import webbrowser
from collections import deque
from pathlib import Path

from aiohttp import web
from rich.console import Console

import engineering_skills
from main import (
    MODEL_PROVIDERS,
    ProjectMenu,
    TerminalUI,
    is_error_result,
    summarize_result,
)

STATIC = Path(__file__).with_name("static")


class ConsoleSink:
    def __init__(self, ui):
        self.ui = ui

    def write(self, text):
        if text.strip() and any(c.isalnum() for c in text):
            self.ui.add("console", text.rstrip())
        return len(text)

    def flush(self):
        pass


class BrowserUI(TerminalUI):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.console = Console(
            file=ConsoleSink(self), width=90, color_system=None, highlight=False
        )
        self.rows = deque(maxlen=160)
        self.revision = 0
        self.seq = 0
        self.question = None
        self.waiter = None
        self.choices = []
        self.done = False
        self.live = None
        self.changed = asyncio.Event()

    def dirty(self):
        self.revision += 1
        self.changed.set()

    def add(self, kind, text, label=""):
        self.seq += 1
        text = str(text)
        if len(text) > 6000:
            text = text[:6000] + "\n[Display truncated]"
        self.rows.append(
            {"id": self.seq, "kind": kind, "text": text, "label": label}
        )
        self.dirty()
        return self.rows[-1]

    async def read(self, label, default="", chat=False):
        self.waiter = asyncio.get_running_loop().create_future()
        self.question = {
            "id": secrets.token_hex(12),
            "label": label,
            "default": default,
            "chat": chat,
            "choices": self.choices,
        }
        self.choices = []
        self.dirty()
        try:
            return await self.waiter
        finally:
            self.question = self.waiter = None
            self.dirty()

    def submit(self, prompt, value):
        if not self.question or self.question["id"] != prompt or self.waiter.done():
            return False
        value = value.strip() or self.question["default"]
        if not value and self.question["chat"]:
            return False
        if self.question["chat"]:
            self.add("user", value)
        self.waiter.set_result(value)
        return True

    async def ask(self):
        return await self.read("Message the agent", chat=True)

    async def _read_single_line(self, label, default=""):
        if label == "Select provider":
            self.choices = [{"value": p, "name": p} for p in sorted(MODEL_PROVIDERS)]
            self.rows.clear()
        return await self.read(label, default)

    def restore(self):
        self.rows.clear()
        for msg in (self.app.session.messages if self.app.session else [])[-100:]:
            role = msg.get("role")
            if (
                role in {"user", "assistant"}
                and isinstance(msg.get("content"), str)
                and msg["content"]
            ):
                self.add(role, msg["content"])
            elif role == "tool":
                self.add("tool", msg.get("content", ""), "Saved tool result")
        self.dirty()

    def conversation_changed(self, session, command):
        if command == "/clear":
            self.rows.clear()
            self.dirty()
        else:
            notice = (
                self.rows[-1]
                if self.rows and self.rows[-1]["kind"] in {"error", "notice"}
                else None
            )
            self.restore()
            if notice:
                self.add(notice["kind"], notice["text"])

    def banner(self, **kwargs):
        self.restore()

    def content_start(self):
        self.live = self.add("assistant", "")

    def content_delta(self, text):
        if self.live is None:
            self.content_start()
        combined = self.live["text"] + text
        self.live["text"] = (
            combined
            if len(combined) <= 32000
            else "[Earlier text omitted from live view]\n" + combined[-31000:]
        )
        self.dirty()

    def content_end(self):
        self.live = None

    def reasoning_start(self):
        self.add("activity", "Thinking")

    def reasoning_delta(self, text):
        pass

    def reasoning_end(self):
        pass

    def tool_call(self, name, arguments):
        self.add("activity", name, "Tool call")

    def tool_result(self, name, result, elapsed, verbose=False, key_label=""):
        self.add(
            "error" if is_error_result(result) else "tool",
            json.dumps(result, default=str)
            if verbose
            else summarize_result(name, result),
            f"{name} · {elapsed:.2f}s",
        )

    def error(self, msg):
        self.add("error", msg)

    def warning(self, msg):
        self.add("notice", msg)

    def info(self, msg):
        self.add("notice", msg)

    def success(self, msg):
        self.add("notice", msg)

    def snapshot(self):
        app = self.app
        cfg, session = app.config, app.session
        return {
            "revision": self.revision,
            "question": self.question,
            "done": self.done,
            "rows": list(self.rows),
            "project": app.project.name if app.project else "Local workspace",
            "agent": cfg.agent_name if cfg else "Agnes Agent",
            "provider": cfg.provider if cfg else app.settings["provider"],
            "model": cfg.model if cfg else app.settings["model"],
            "tools": [
                {"name": n, "enabled": app.tools.is_enabled(n)}
                for n in app.tools.names()
            ]
            if app.tools
            else [],
            "skills": len(engineering_skills.DESCRIPTIONS),
            "swarm": bool(cfg and cfg.swarm_default),
            "tokens": session.prompt_tokens + session.completion_tokens
            if session
            else 0,
        }


class BrowserMenu(ProjectMenu):
    def _render(self, projects):
        self.ui.choices = [
            {"value": str(i), "name": p.name} for i, p in enumerate(projects, 1)
        ] + [
            {"value": "c", "name": "Chat in current directory"},
            {"value": "n", "name": "Create project"},
            {"value": "i", "name": "Import project"},
            {"value": "d", "name": "Delete project"},
            {"value": "q", "name": "Exit"},
        ]


def application(ui, token, authority):
    @web.middleware
    async def guard(request, handler):
        if request.host != authority:
            raise web.HTTPForbidden()
        if request.path.startswith("/api/"):
            if not secrets.compare_digest(
                request.headers.get("Authorization", "").encode(),
                ("Bearer " + token).encode(),
            ):
                raise web.HTTPForbidden()
            if (
                request.method == "POST"
                and request.headers.get("Origin") != "http://" + authority
            ):
                raise web.HTTPForbidden()
        response = await handler(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            }
        )
        return response

    async def state(request):
        if request.query.get("since") == str(ui.revision):
            ui.changed.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ui.changed.wait(), 15)
            # Coalesce streaming tokens into one display update.
            await asyncio.sleep(0.08)
        if request.query.get("since") == str(ui.revision):
            return web.Response(status=204)
        return web.json_response(ui.snapshot())

    async def respond(request):
        try:
            data = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest()
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("text"), str)
            or len(data["text"]) > 32000
        ):
            raise web.HTTPBadRequest()
        if not ui.submit(data.get("id"), data["text"]):
            raise web.HTTPConflict(
                text="This prompt has already been answered or is no longer active."
            )
        return web.json_response({"ok": True})

    async def catalog(request):
        query = request.query.get("q", "")
        try:
            offset = int(request.query.get("offset", "0"))
        except ValueError:
            raise web.HTTPBadRequest()
        return web.json_response(
            engineering_skills.catalog(
                group=request.query.get("group"), query=query, offset=offset
            )
        )

    app = web.Application(middlewares=[guard], client_max_size=160000)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/input", respond)
    app.router.add_get("/api/skills", catalog)
    for route, file in [
        ("/", "index.html"),
        ("/app.js", "app.js"),
        ("/style.css", "style.css"),
    ]:

        async def asset(request, name=file):
            return web.FileResponse(STATIC / name)

        app.router.add_get(route, asset)
    return app


async def serve(app, port, launch=True):
    import socket

    ui = BrowserUI(app)
    app.ui, app.menu = ui, BrowserMenu(app.manager, ui)
    token = secrets.token_urlsafe(32)
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    authority = f"127.0.0.1:{sock.getsockname()[1]}"
    runner = web.AppRunner(application(ui, token, authority), access_log=None)
    await runner.setup()
    await web.SockSite(runner, sock).start()
    url = f"http://{authority}/#{token}"
    print(f"Agent workspace: {url}", flush=True)
    if launch:
        await asyncio.to_thread(webbrowser.open, url)
    try:
        try:
            code = await app.start()
        except Exception as exc:
            ui.error(str(exc))
            code = 1
        ui.done = True
        ui.add("notice", "Session ended. Stop the local server with Ctrl+C.")
        await asyncio.Event().wait()
        return code
    finally:
        await runner.cleanup()
