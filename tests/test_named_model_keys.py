import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from browser_ui.model_settings import ModelSettings
from main import App, Config, Session, Swarm, UnconfiguredModel


def test_three_keys_for_one_model_switch_next_request_and_survive_restart(tmp_path):
    async def exercise():
        app = App.__new__(App)
        app.home = tmp_path
        app.settings = {"provider":"deepseek", "base_url":"https://api.deepseek.com"}
        app.args = SimpleNamespace(routing_config=None)
        app.ui = SimpleNamespace(dirty=lambda: None, retry_notice=lambda *args: None)
        app.config = Config(api_keys=[], provider="deepseek")
        app.client = UnconfiguredModel()
        app.tools = SimpleNamespace(schemas=lambda: [])
        app.session = Session(app.config, app.client, app.tools, app.ui)
        app.swarm = Swarm(app.config, app.client, app.tools, app.ui)
        app.session.messages.append({"role":"user", "content":"Remember this"})
        store = app.ui.models = ModelSettings(app)
        store.unlock("a fixture vault passphrase")
        ids = [store.save("deepseek", label, "test-secret-" + label) for label in ("one","two","three")]
        other = store.save("openai", "Work", "test-secret-openai")
        with pytest.raises(ValueError):
            store.select("deepseek", "model-a", other)
        sent = []

        def capture(request):
            sent.append((request.url.host, request.headers["Authorization"], json.loads(request.content)["model"]))
            return httpx.Response(200, json={"choices":[{"message":{"content":"OK"}}]})

        original = httpx.AsyncClient
        with patch("main.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(capture), **kw)):
            for provider, model, key_id in (("deepseek","model-a",ids[0]), ("deepseek","model-a",ids[2]),
                                            ("deepseek","model-b",ids[2]), ("openai","model-c",other)):
                store.select(provider, model, key_id)
                await app._apply_browser_model()
                assert app.session.client is app.swarm.client is app.client
                async for _ in app.client.stream_chat(app.session.messages, stream=False):
                    pass
        assert sent == [("api.deepseek.com","Bearer test-secret-one","model-a"),
                        ("api.deepseek.com","Bearer test-secret-three","model-a"),
                        ("api.deepseek.com","Bearer test-secret-three","model-b"),
                        ("api.openai.com","Bearer test-secret-openai","model-c")]
        assert app.session.messages == [{"role":"user","content":"Remember this"}]
        await app.client.close()
        assert b"test-secret" not in store.path.read_bytes()
        assert "test-secret" not in json.dumps(store.state())
        reopened = ModelSettings(app)
        with pytest.raises(ValueError, match="Unlock"):
            reopened.selection()
        with pytest.raises(ValueError, match="Incorrect"):
            reopened.unlock("wrong passphrase")
        reopened.unlock("a fixture vault passphrase")
        assert reopened.selection()[-1] == "test-secret-openai"

    asyncio.run(exercise())
