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
        release_call = asyncio.Event()
        report = (
            "## Catalog check\n\n**Verified** the skill catalog.\n\n"
            "| Skill | Result |\n| --- | ---: |\n| SHA-256 | Available |\n\n"
            "> Session tools\n\n- [x] Search catalog\n- Check results\n  - Read name\n\n"
            "```python\nprint('sha256')\n```\n\n~~pending~~\n\n"
            "[Details](https://example.com)\n\n<script>window.injected=true</script>"
        )

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
                else [{"content": report[:18]}, {"content": report[18:]}]
            )
            deltas.insert(0, {"reasoning_content": "internal reasoning",
                              "reasoning_summary": "Reviewing the catalog request."})
            for delta in deltas:
                chunk = {"choices": [{"index": 0, "delta": delta}]}
                await reply.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            if "tool_calls" in msg:
                await asyncio.wait_for(release_call.wait(), 10)
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
                    await page.get_by_role("button", name="Settings").click()
                    await page.locator("#modelProvider").select_option("deepseek")
                    await page.locator("#vaultPhrase").fill("browser test vault passphrase")
                    await page.locator("#unlockVault").click()
                    for label in ("Personal", "Work", "Testing"):
                        await page.locator("#namedLabel").fill(label)
                        await page.locator("#namedSecret").fill("fixture-" + label)
                        await page.locator("#saveNamedKey").click()
                        await page.wait_for_function(
                            "(label) => document.querySelector('#modelKey').selectedOptions[0]?.text === label",
                            arg=label)
                    await page.locator("#modelKey").select_option(label="Work")
                    await page.locator("#modelId").fill("deepseek-test-model")
                    await page.locator("#useModel").click()
                    await page.locator("#modelSelection").get_by_text("deepseek-test-model", exact=False).wait_for()
                    await page.locator("#modelProvider").select_option("local")
                    await page.locator("#modelId").fill("browser-chosen-model")
                    await page.locator("#useModel").click()
                    await page.locator("#modelSelection").get_by_text("browser-chosen-model", exact=False).wait_for()
                    await page.get_by_label("Service", exact=True).select_option("search")
                    await page.get_by_text("SerpAPI · https://serpapi.com", exact=True).wait_for()
                    secret_field = page.get_by_label("New API key")
                    assert await secret_field.get_attribute("type") == "password"
                    await secret_field.fill("fixture-search-credential")
                    await page.get_by_role("button", name="Apply key").click()
                    await page.get_by_text("Session key applied.", exact=True).wait_for()
                    assert await secret_field.input_value() == ""
                    await page.get_by_role("button", name="Clear session key").click()
                    await page.get_by_text("Key removed.", exact=True).wait_for()
                    await page.get_by_label("Message text size").select_option("large")
                    await page.reload()
                    assert await page.locator("html.largeMessages").count() == 1
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
                    tool_bubble = page.locator("#feed .entry.tool")
                    await tool_bubble.get_by_text("Requested", exact=True).wait_for()
                    await tool_bubble.locator("summary").click()
                    initial_node = await tool_bubble.element_handle()
                    release_call.set()
                    await tool_bubble.get_by_text("Completed", exact=True).wait_for()
                    assert await tool_bubble.evaluate("(node, original) => node === original", initial_node)
                    assert await tool_bubble.locator("details").evaluate("node => node.open")
                    await tool_bubble.locator("summary").click()
                    await page.get_by_text("Verified", exact=True).wait_for(
                        timeout=20000
                    )
                    assert len(requests) == 2
                    assert all(r["model"] == "browser-chosen-model" for r in requests)
                    bubbles = page.locator("#feed .entry:is(.user,.thinking,.tool,.assistant)")
                    assert await bubbles.evaluate_all(
                        "(nodes) => nodes.slice(-5).map(n => n.classList[1])"
                    ) == ["user", "thinking", "tool", "thinking", "assistant"]
                    thinking = page.locator(".thinking details").first
                    assert not await thinking.evaluate("el => el.open")
                    await thinking.locator("summary").click()
                    assert await thinking.locator(".stepText").inner_text() == "Reviewing the catalog request."
                    assert "internal reasoning" not in await page.locator("#feed").inner_text()
                    assert "_display_steps" not in json.dumps(requests)
                    answer = page.locator(".assistant .answer")
                    assert await answer.get_by_role("heading", name="Catalog check").count() == 1
                    assert await answer.get_by_role("cell", name="Available").count() == 1
                    assert await answer.get_by_role("checkbox").is_checked()
                    assert await answer.locator("blockquote, s, pre code, li li").count() == 4
                    assert await answer.locator("script").count() == 0
                    assert not await page.evaluate("window.injected")
                    trace = page.locator("#feed details").filter(has_text="skill_catalog")
                    await trace.locator("summary").click()
                    assert "text_sha256" in await trace.locator("pre").last.inner_text()
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
                    await page.get_by_role("button", name="History", exact=True).click()
                    await page.get_by_role("dialog").get_by_role("button", name="Check the catalog").click()
                    await answer.get_by_role("heading", name="Catalog check").wait_for()
                    assert await page.locator(".thinking").count() == 2
                    await trace.locator("summary").click()
                    assert "sha256" in await trace.locator("pre").first.inner_text()
                    assert "text_sha256" in await trace.locator("pre").last.inner_text()
                    for width in [320, 390, 768, 1440]:
                        await page.set_viewport_size({"width": width, "height": 810})
                        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        box = await page.locator("#send").bounding_box()
                        assert box and box["y"] + box["height"] <= 810
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


def test_restore_pairs_reordered_results_and_retains_long_messages():
    from types import SimpleNamespace
    from browser_ui.server import BrowserUI

    calls = [{"id": i, "function": {"name": "lookup", "arguments": i}} for i in "abc"]
    long_message = "paragraph\n\n" * 4000
    messages = [{"role": "user", "content": str(i)} for i in range(180)] + [
        {"role": "assistant", "content": long_message, "tool_calls": calls},
        {"role": "tool", "tool_call_id": "b", "content": '{"error":"timeout"}'},
        {"role": "tool", "tool_call_id": "a", "content": "legacy plain text"},
        {"role": "tool", "tool_call_id": "unknown", "content": "orphan result"},
    ]
    ui = BrowserUI(SimpleNamespace(session=SimpleNamespace(messages=messages)))
    ui.restore()
    assert ui.rows[0]["text"] == "0"
    assert ui.rows[180]["text"] == long_message
    first, second, missing, orphan = ui.rows[-4:]
    assert first["result"] == "legacy plain text"
    assert second["arguments"] == "b" and second["status"] == "Failed"
    assert missing["status"] == "No saved result"
    assert orphan["result"] == "orphan result"



def test_tool_bubbles_follow_each_call_through_completion():
    from types import SimpleNamespace
    from browser_ui.server import BrowserUI

    ui = BrowserUI(SimpleNamespace())
    calls = [{"id": key, "name": "read_file", "args": {"path": key}, "error": None}
             for key in ("a", "b", "c")]
    ui.tools_requested(calls)
    first, second, third = ui.rows
    assert [r["status"] for r in ui.rows] == ["Requested"] * 3
    ui.tool_started("a")
    ui.tool_started("b")
    assert first["status"] == second["status"] == "Running"
    ui.tool_finished("b", {"error": "missing file"}, 0.2)
    ui.tool_finished("a", {"text": "file a"}, 0.3)
    ui.finish_tool_batch()
    assert ui.rows == [first, second, third]
    assert json.loads(first["result"]) == {"text": "file a"}
    assert second["status"] == "Failed" and "missing file" in second["result"]
    assert third["status"] == "Interrupted"
