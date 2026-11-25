import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


def test_browser_operates_real_cli_session(tmp_path):
    async def exercise():
        requests = []

        async def provider(request):
            data = await request.json()
            requests.append(data)
            calls = [
                {
                    "id": "catalog1",
                    "type": "function",
                    "function": {
                        "name": "skill_catalog",
                        "arguments": json.dumps({"query": "sha256"}),
                    },
                }
            ]
            if data["messages"][-1]["role"] == "tool":
                assert "text_sha256" in data["messages"][-1]["content"]
                msg = {
                    "role": "assistant",
                    "content": "**Verified** the skill catalog.",
                }
            else:
                msg = {"role": "assistant", "content": "", "tool_calls": calls}
            reply = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await reply.prepare(request)
            deltas = (
                [msg]
                if "tool_calls" in msg
                else [{"content": "**Verified** "}, {"content": "the skill catalog."}]
            )
            for delta in deltas:
                chunk = {"choices": [{"index": 0, "delta": delta}]}
                await reply.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            await reply.write(b"data: [DONE]\n\n")
            await reply.write_eof()
            return reply

        api = web.Application()
        api.router.add_post("/{route:.*}", provider)
        runner = web.AppRunner(api)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(ROOT / "main.py"),
            "--frontend",
            "--no-open-browser",
            "--no-web",
            "--home",
            str(tmp_path / "home"),
            "--provider",
            "local",
            "--base-url",
            f"http://127.0.0.1:{port}",
            cwd=tmp_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), 15)
            assert line.startswith(b"Agent workspace: "), line
            url = line.decode().strip().split()[-1]
            origin, token = url.split("/#")
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    executable_path=os.getenv("TEST_CHROMIUM_EXECUTABLE")
                    or shutil.which("google-chrome")
                )
                try:
                    page = await browser.new_page(
                        viewport={"width": 1200, "height": 850}
                    )
                    errors = []
                    page.on("pageerror", lambda exc: errors.append(str(exc)))
                    await page.goto(url)
                    await page.get_by_role(
                        "button", name="Chat in current directory"
                    ).click(timeout=15000)
                    await page.get_by_role("button", name="Skill library").click()
                    await page.get_by_role("searchbox").fill("sha256")
                    await page.get_by_text("text_sha256", exact=True).wait_for()
                    await page.get_by_role(
                        "button", name="Tools", exact=False
                    ).first.click()
                    tool = page.locator(".toolRow").filter(has_text="skill_run")
                    await tool.get_by_role("button", name="Enabled").click()
                    await tool.get_by_role("button", name="Disabled").wait_for()
                    await page.locator('[data-view="session"]').click()
                    await page.locator("#message").fill("Check the catalog")
                    await page.locator("#send").click()
                    await page.get_by_text("Verified", exact=True).wait_for(
                        timeout=20000
                    )
                    assert len(requests) == 2
                    await page.reload()
                    await page.get_by_text("Verified", exact=True).wait_for()
                    await page.locator("#new").click()
                    await page.get_by_text(
                        "New conversation started.", exact=False
                    ).wait_for()
                    assert await page.get_by_text("Verified", exact=True).count() == 0
                    await page.set_viewport_size({"width": 375, "height": 740})
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    assert (await page.request.get(origin + "/api/state")).status == 403
                    headers = {
                        "Authorization": "Bearer " + token,
                        "Origin": "https://unrelated.test",
                    }
                    assert (
                        await page.request.post(
                            origin + "/api/input", headers=headers, data={"text": "bad"}
                        )
                    ).status == 403
                    headers["Origin"] = origin
                    assert (
                        await page.request.post(
                            origin + "/api/input",
                            headers=headers,
                            data={"text": "bad", "id": "stale"},
                        )
                    ).status == 409
                    await page.locator("#message").fill("/exit")
                    await page.locator("#send").click()
                    await (
                        page.locator("#connection")
                        .get_by_text("Session ended")
                        .wait_for()
                    )
                    assert not errors, errors
                finally:
                    await browser.close()
        finally:
            if proc.returncode is None:
                proc.terminate()
            await proc.wait()
            await runner.cleanup()

    asyncio.run(exercise())
