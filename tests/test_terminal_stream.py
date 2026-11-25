import asyncio
import json
import shlex
import shutil
import sys
from types import SimpleNamespace

import pytest

from browser_ui.server import BrowserUI
from codeagent.shell import Shell, ShellTimeout
from codeagent.workspace import Workspace
from main import Agent
from terminal_stream import output_sink


def test_build_output_reaches_model_and_same_bubble_before_exit(tmp_path, monkeypatch):
    if not shutil.which("npm"):
        pytest.skip("npm unavailable")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "node build.js"}}))
    (tmp_path / "build.js").write_text("""
        const fs = require('fs');
        process.stdout.write('compiling');
        process.stderr.write('warning');
        const timer = setInterval(() => {
          if (fs.existsSync('continue')) { clearInterval(timer); process.exitCode = 2; }
        }, 20);
    """)
    async def exercise():
        command = "npm run build"
        shell = Shell(Workspace(tmp_path))
        ui = BrowserUI(SimpleNamespace())
        calls = [{"id":"t", "name":"terminal", "args":{"command":command}, "error":None}]
        ui.tools_requested(calls)
        ui.tool_started("t")
        row = ui.rows[0]

        class Tools:
            async def dispatch(self, name, args):
                return await shell.run(args["command"], timeout=6)

        class Model:
            async def stream_chat(self, messages, **kwargs):
                assert row["status"] == "Running"
                if "warning" not in messages[-1]["content"]:
                    return
                assert "compiling" in row["stdout"] and "warning" in row["stderr"]
                assert kwargs["tools"] is None
                (tmp_path / "continue").touch()
                yield {"choices":[{"message":{"content":"Build is compiling."}}]}

        agent = Agent(SimpleNamespace(max_rounds=5, max_tokens=100), Model(), Tools(), ui, "test")
        monkeypatch.setattr("main.TERMINAL_OBSERVATION_INTERVAL", 0.25)
        result = await agent._run_terminal_live("t", calls[0]["args"])
        ui.tool_finished("t", result, 1)
        assert len(ui.rows) == 1 and row is ui.rows[0]
        assert result["exit_code"] == 2 and row["status"] == "Failed"
        assert result["progress_notes"] == ["Build is compiling."]

    asyncio.run(exercise())


def test_terminal_timeout_retains_live_partial_output(tmp_path):
    async def exercise():
        shell = Shell(Workspace(tmp_path))
        seen = []
        token = output_sink.set(lambda channel, text: seen.append(text))
        try:
            with pytest.raises(ShellTimeout):
                await shell.run(shlex.join([sys.executable, "-uc",
                    "import time;print('working',end='',flush=True);time.sleep(10)"]), timeout=1)
        finally:
            output_sink.reset(token)
        assert "".join(seen) == "working"

    asyncio.run(exercise())


def test_browser_displays_terminal_output_before_command_exits(tmp_path):
    import os
    from aiohttp.test_utils import TestServer
    from playwright.async_api import async_playwright
    from browser_ui.server import application

    async def exercise():
        ui = BrowserUI(SimpleNamespace(config=None, project=None, session=None, tools=None,
                                       settings={"provider":"local", "model":"test"}))
        ui.tools_requested([{"id":"terminal1", "name":"terminal", "args":{}, "error":None}])
        ui.tool_started("terminal1")
        script = tmp_path / "build.js"
        script.write_text("""
            process.stdout.write('Building module');
            process.stderr.write('Build warning');
            const fs = require('fs');
            const t = setInterval(() => {
                if (fs.existsSync('finish')) { clearInterval(t); process.exitCode = 1; }
            }, 20);
        """)
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = TestServer(application(ui, "test-token", f"127.0.0.1:{port}"),
                            socket_factory=lambda *args, **kwargs: sock)
        async with server, async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=os.getenv("TEST_CHROMIUM_EXECUTABLE")
                                               or shutil.which("google-chrome"))
            try:
                page = await browser.new_page()
                await page.goto(str(server.make_url("/")) + "#test-token")
                bubble = page.locator(".entry.tool")
                await bubble.locator("summary").click()
                with_sink = output_sink.set(lambda channel, text: ui.terminal_progress("terminal1", channel, text))
                try:
                    job = asyncio.create_task(Shell(Workspace(tmp_path)).run("node build.js", timeout=10))
                finally:
                    output_sink.reset(with_sink)
                try:
                    await bubble.locator(".stdout pre").get_by_text("Building module").wait_for()
                    await bubble.locator(".stderr pre").get_by_text("Build warning").wait_for()
                    assert not job.done()
                finally:
                    (tmp_path / "finish").touch()
                    result = await job
                ui.tool_finished("terminal1", result, result["duration"])
                await bubble.locator(".toolStatus").get_by_text("Failed").wait_for()
                assert await bubble.count() == 1 and await bubble.locator("details").evaluate("d => d.open")
            finally:
                await browser.close()

    asyncio.run(exercise())
