import asyncio
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from browser_ui import credentials
from browser_ui.server import application


def test_settings_secrets_are_write_only_and_destination_bound(tmp_path):
    async def exercise():
        records = {}
        class Vault:
            def get_password(self, service, account):
                return records.get((service, account))
            def set_password(self, service, account, value):
                records[service, account] = value
            def delete_password(self, service, account):
                del records[service, account]
        app = SimpleNamespace(home=Path(tmp_path), web_agent=None,
                              settings=dict(provider="example", base_url="https://example.test/v1"),
                              ui=SimpleNamespace(dirty=lambda: None))
        store = credentials.WorkspaceCredentials(app)
        from unittest.mock import patch
        with patch.object(credentials, "native_vault", Vault):
            ui = SimpleNamespace(app=app, credentials=store)
            server = TestServer(application(ui, "token", "localhost"))
            async with TestClient(server) as client:
                path = "/api/settings/provider"
                headers = {"Host":"localhost", "Authorization":"Bearer token", "Origin":"http://localhost"}
                payload = dict(label="example", endpoint=app.settings["base_url"], action="session", value="fixture-secret")
                assert (await client.post(path, headers={**headers,"Origin":"https://other.test"}, json=payload)).status == 403
                assert (await client.get(path, headers={"Host":"localhost"})).status == 403
                waiter = asyncio.create_task(store.await_provider())
                await asyncio.sleep(0)
                assert (await client.post(path, headers=headers, json=payload)).status == 204
                assert await asyncio.wait_for(waiter, 2) == payload["value"]
                assert records == {}
                response = await client.get(path, headers=headers)
                assert payload["value"] not in await response.text()
                assert (await response.json())["session"]
                payload["action"] = "save"
                assert (await client.post(path, headers=headers, json=payload)).status == 204
                assert await credentials.WorkspaceCredentials(app).read("provider") == payload["value"]
                app.settings["base_url"] += "/other"
                assert await store.read("provider") is None
                assert (await client.post(path, headers=headers, json=payload)).status == 400
                app.settings["base_url"] = payload["endpoint"]
                payload["action"] = "forget"
                assert (await client.post(path, headers=headers, json=payload)).status == 204
                assert not records
                with patch.object(credentials, "native_vault", side_effect=RuntimeError("locked")):
                    payload["action"] = "save"
                    assert (await client.post(path, headers=headers, json=payload)).status == 503
                    assert await store.read("provider") is None
    asyncio.run(exercise())
