"""Loopback browser transport for a single existing agent App."""

import asyncio
import contextlib
import json
import secrets
import time
import webbrowser
from bisect import bisect_left
from functools import partial
from html import escape

from markdown_it import MarkdownIt
from pathlib import Path

from browser_ui.model_settings import ModelSettings
from browser_ui.credentials import WorkspaceCredentials, VaultUnavailable
from aiohttp import web
from rich.console import Console

import engineering_skills
from main import (
    MODEL_PROVIDERS,
    ProjectMenu,
    TerminalUI,
    is_error_result,
    Agent,
)

STATIC = Path(__file__).with_name("static")


markdown = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def image_link(tokens, idx, options, env):
    token = tokens[idx]
    url = token.attrGet("src") or ""
    label = escape(token.content or "View image")
    if url.lower().startswith(("https://", "http://")):
        return f'<a href="{escape(url, quote=True)}">{label}</a>'
    return label


markdown.renderer.rules["image"] = image_link


class ConsoleSink:
    def __init__(self, ui):
        self.ui = ui

    def write(self, text):
        if text.strip() and any(c.isalnum() for c in text):
            self.ui.add("console", text.rstrip())
        return len(text)

    def flush(self):
        pass


def result_status(name, result):
    if is_error_result(result):
        return "Failed"
    if name == "terminal" and isinstance(result, dict) and result.get("exit_code") not in (None, 0):
        return "Failed"
    return "Completed"


class BrowserUI(TerminalUI):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.console = Console(
            file=ConsoleSink(self), width=90, color_system=None, highlight=False
        )
        self.rows = []
        self.tool_rows = {}
        self.phase = None
        self.round_start = 0
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
        if kind == "console" and len(text) > 6000:
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
        self.tool_rows.clear()
        self.phase = self.live = None
        calls = {}
        for msg in self.app.session.messages if self.app.session else []:
            role = msg.get("role")
            steps = msg.get("_display_steps") if role == "assistant" else None
            if isinstance(steps, list) and steps and all(
                isinstance(step, dict) and step.get("kind") in {"thinking", "assistant"}
                and isinstance(step.get("text"), str) for step in steps
            ):
                for step in steps:
                    self.add(step["kind"], step["text"])
            elif (
                role in {"user", "assistant"}
                and isinstance(msg.get("content"), str)
                and msg["content"]
            ):
                self.add(role, msg["content"])
            if role == "assistant":
                for call in msg.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    row = self.tool_row(fn.get("name") or "Tool", fn.get("arguments", ""))
                    row["status"] = "No saved result"
                    if call.get("id"):
                        calls[call["id"]] = row
            elif role == "tool":
                row = calls.pop(msg.get("tool_call_id"), None)
                if row is None:
                    row = self.tool_row(msg.get("name") or "Saved tool", "")
                raw = msg.get("content", "")
                try:
                    result = json.loads(raw)
                except (TypeError, ValueError):
                    result = raw
                status = result_status(row["label"], result)
                if row["label"] == "terminal" and isinstance(result, dict) and result.get("process_id"):
                    status = "Launched"
                row.update(result=raw, status=status)
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
        self.live.pop("html", None)
        self.live["text"] += text
        self.dirty()

    def content_end(self):
        self.live = None

    def begin_round(self, key_label=""):
        self.round_start = len(self.rows)
        self.phase = None

    def round_display_steps(self):
        rows = self.rows[self.round_start:]
        if any(row["kind"] == "thinking" for row in rows):
            return [{"kind": r["kind"], "text": r["text"]} for r in rows
                    if r["kind"] in {"thinking", "assistant"}]
        return []

    def reasoning_start(self):
        previous = self.rows[-1] if self.rows else {}
        description = (f"Reviewing the result from {previous['label']}."
                       if previous.get("kind") == "tool" else "Processing your request.")
        self.phase = self.add("thinking", description)
        self.has_summary = False

    def reasoning_delta(self, text):
        # Only display summaries explicitly supplied for presentation.
        pass

    def summary_delta(self, text):
        if self.phase is not None and text:
            self.phase["text"] = (self.phase["text"] if self.has_summary else "") + text
            self.has_summary = True
            self.dirty()

    def reasoning_end(self):
        self.phase = None

    def tool_row(self, name, arguments):
        row = self.add("tool", "", name)
        row.update(arguments=arguments, status="Requested")
        return row

    def tool_requested(self, call_id, name, arguments):
        row = self.tool_rows.get(call_id)
        if row is None:
            self.tool_rows[call_id] = self.tool_row(name, arguments)
        else:
            row.update(label=name, arguments=arguments)
            self.dirty()

    def tools_requested(self, calls):
        for call in calls:
            args = call["raw"]["arguments"] if call["error"] else call["args"]
            self.tool_requested(call["id"], call["name"], args)

    def terminal_progress(self, call_id, channel, text):
        row = self.tool_rows.get(call_id)
        if row is not None:
            self.row_progress(row, channel, text)

    def background_callbacks(self, call_id):
        row = self.tool_rows[call_id]
        return partial(self.row_progress, row), partial(self.background_exited, row)

    def row_progress(self, row, channel, text):
        if channel == "observation":
            row["observation"] = text
        else:
            row[channel] = (row.get(channel, "") + text)[-16000:]
        self.dirty()

    def tool_started(self, call_id):
        self.tool_rows[call_id]["status"] = "Running"
        self.dirty()

    def background_exited(self, row, result):
        row["result"] = Agent._serialize_tool_result(result)
        code = result.get("exit_code")
        row["status"] = "Interrupted" if code is None else ("Completed" if code == 0 else "Failed")
        row["elapsed"] = round(max(0, time.time() - result["started"]), 2)
        self.dirty()

    def tool_finished(self, call_id, result, elapsed):
        row = self.tool_rows.pop(call_id)
        if row["label"] == "terminal" and isinstance(result, dict) and result.get("process_id"):
            if row["status"] == "Running":
                row["result"] = Agent._serialize_tool_result(result)
        else:
            row.update(result=Agent._serialize_tool_result(result), elapsed=round(elapsed, 2),
                       status=result_status(row["label"], result))
        self.dirty()

    def finish_tool_batch(self):
        for row in self.tool_rows.values():
            row.update(status="Interrupted")
        self.tool_rows.clear()
        self.dirty()

    def display_rows(self, rows):
        for row in rows:
            if row["kind"] in {"user", "assistant"} and "html" not in row:
                row["html"] = markdown.render(row["text"])
        return rows

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
            "needsCredential": bool(getattr(self, "credentials", None) and self.credentials.waiting),
            "question": self.question,
            "done": self.done,
            "rows": self.display_rows(self.rows[-160:]),
            "firstId": self.rows[0]["id"] if self.rows else None,
            "hasOlder": len(self.rows) > 160,
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

    async def history(request):
        store = ui.app.project or ui.app.chat_store
        return web.json_response([e for e in store.list_conversations() if e["id"] != "current"])

    async def earlier(request):
        try:
            before = int(request.query["before"])
        except (KeyError, ValueError):
            raise web.HTTPBadRequest()
        end = bisect_left(ui.rows, before, key=lambda row: row["id"])
        return web.json_response({"rows": ui.display_rows(ui.rows[max(0, end-160):end]), "hasOlder": end > 160})

    async def credential(request):
        name = request.match_info["name"]
        try:
            if request.method == "POST":
                if request.content_type != "application/json":
                    raise web.HTTPUnsupportedMediaType()
                data = await request.json()
                if not isinstance(data, dict):
                    raise web.HTTPBadRequest()
                await ui.credentials.update(name, data.get("endpoint"), data.get("label"),
                                            data.get("action"), data.get("value"))
                if name == "search" and ui.app.web_agent:
                    import os
                    ui.app.web_agent.search_engine.serpapi_key = (
                        await ui.credentials.read("search") or os.getenv("SERPAPI_KEY")
                    )
                if name == "tavily" and ui.app.web_agent:
                    import os
                    ui.app.web_agent.search_engine.tavily_key = (
                        await ui.credentials.read(name) or os.getenv("TAVILY_API_KEY")
                    )
                return web.Response(status=204)
            return web.json_response(await ui.credentials.status(name))
        except VaultUnavailable:
            raise web.HTTPServiceUnavailable(text="Unlock your OS credential store or use session storage.")
        except (ValueError, TypeError, UnicodeError):
            raise web.HTTPBadRequest(text="Invalid credential or destination.")

    async def model_settings(request):
        store = ui.models
        if request.method == "GET":
            return web.json_response(store.state())
        try:
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType()
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("Invalid settings.")
            async with store.lock:
                action = data.get("action")
                if action == "unlock":
                    await asyncio.to_thread(store.unlock, data.get("passphrase"))
                elif action == "save":
                    key_id = store.save(data.get("provider"), data.get("label"), data.get("value"))
                    return web.json_response({"key_id": key_id})
                elif action == "select":
                    store.select(data.get("provider"), data.get("model"), data.get("key_id"))
                else:
                    raise ValueError("Unknown settings action.")
            ui.dirty()
            return web.json_response({"ok": True})
        except (ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc))

    app = web.Application(middlewares=[guard], client_max_size=160000)
    app.router.add_get("/api/settings/{name:provider|search|tavily}", credential)
    app.router.add_post("/api/settings/{name:provider|search|tavily}", credential)
    app.router.add_get("/api/settings/models", model_settings)
    app.router.add_post("/api/settings/models", model_settings)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/input", respond)
    app.router.add_get("/api/skills", catalog)
    app.router.add_get("/api/history", history)
    app.router.add_get("/api/earlier", earlier)
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
    ui.credentials = WorkspaceCredentials(app)
    ui.models = ModelSettings(app)
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

