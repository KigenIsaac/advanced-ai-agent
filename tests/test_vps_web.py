import asyncio

import httpx

from webagent.agent import WebAgent


def test_vps_research_survives_browser_failure_and_provider_quota(monkeypatch, tmp_path):
    async def exercise():
        monkeypatch.setenv("WEB_SEARCH_MODE", "free")
        monkeypatch.setenv("SEARXNG_URL", "http://localhost:8888")
        agent = WebAgent(serpapi_key="must-not-use", tavily_key="test",
                         downloads_dir=str(tmp_path))
        calls = []

        def respond(request):
            calls.append(request.url.host)
            if request.url.host == "localhost":
                assert "authorization" not in request.headers
                return httpx.Response(429)
            assert request.url.host == "api.tavily.com"
            assert request.headers["Authorization"] == "Bearer test"
            if request.url.path == "/extract":
                return httpx.Response(200, json={"results": [
                    {"url": "https://example.org", "raw_content": "Page text"}]})
            return httpx.Response(200, json={"results": [
                {"url": "https://example.org", "title": "Example", "content": "Snippet"}]})

        async def missing_browser():
            raise RuntimeError("No Chromium executable")

        monkeypatch.setattr(agent.browser, "start", missing_browser)
        await agent.search_engine._client.aclose()
        agent.search_engine._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        async with agent:
            assert (await agent.web_search(query="example"))["backend"] == "tavily"
            assert (await agent.web_browser("read_url", url="https://example.org"))["text"] == "Page text"
            assert (await agent.web_browser("click", selector="button"))["error"] == "browser_unavailable"
        assert calls == ["localhost", "api.tavily.com", "api.tavily.com"]

    asyncio.run(exercise())


def test_quota_falls_back_to_keyless_search(monkeypatch, tmp_path):
    async def exercise():
        monkeypatch.delenv("SEARXNG_URL", raising=False)
        agent = WebAgent(serpapi_key="must-not-use", tavily_key="test", downloads_dir=str(tmp_path))
        def respond(request):
            if request.url.host == "api.tavily.com":
                return httpx.Response(432)
            assert request.url.host == "html.duckduckgo.com"
            assert "authorization" not in request.headers
            return httpx.Response(200, text='<div class="result"><a class="result__a" href="https://example.org">Example</a></div>')
        await agent.search_engine._client.aclose()
        agent.search_engine._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            result = await agent.web_search(query="example")
            assert result["count"] == 1 and result["backend"] == "duckduckgo"
        finally:
            await agent.close()

    asyncio.run(exercise())
