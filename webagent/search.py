from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

import httpx
from bs4 import BeautifulSoup


DDG_HTML = "https://html.duckduckgo.com/html/"
DDG_LITE = "https://lite.duckduckgo.com/lite/"

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class SearchResult:
    title: str
    url: str
    domain: str
    snippet: str
    published_date: Optional[str] = None
    relevance: float = 0.0
    source: str = "duckduckgo"


def _clean_ddg_url(href: str) -> str:
    if not href:
        return href
    if "duckduckgo.com/l/" in href or href.startswith("//duckduckgo.com/l/"):
        qs = parse_qs(urlparse("https:" + href if href.startswith("//") else href).query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return href


class WebSearch:
    def __init__(self, serpapi_key: Optional[str] = None,
                 proxy: Optional[str] = None, timeout: float = 15.0):
        self.serpapi_key = serpapi_key or os.getenv("SERPAPI_KEY")
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            headers={"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"},
            proxy=proxy,
            timeout=timeout,
            follow_redirects=True,
        )

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ------------------------------------------------------------------ #

    def _score(self, result: dict, query: str, idx: int) -> float:
        # Position-based baseline + keyword overlap
        base = 1.0 / (idx + 1)
        tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
        hay = (result.get("title", "") + " " + result.get("snippet", "")).lower()
        overlap = len(tokens & set(re.findall(r"[a-z0-9]+", hay)))
        return base + overlap * 0.05

    def _post_filter(self, results: list[dict], domains, exclude_domains,
                     date_from, date_to) -> list[dict]:
        out = []
        for r in results:
            dom = r.get("domain", "")
            if domains and not any(dom.endswith(d) for d in domains):
                continue
            if exclude_domains and any(dom.endswith(d) for d in exclude_domains):
                continue
            if date_from or date_to:
                pd = r.get("published_date")
                # Only filter if we have a date to compare
                if pd:
                    if date_from and pd < date_from:
                        continue
                    if date_to and pd > date_to:
                        continue
            out.append(r)
        return out

    # ------------------------------------------------------------------ #
    # DuckDuckGo (default)
    # ------------------------------------------------------------------ #

    async def _ddg(self, query: str, page: int = 0,
                   site: Optional[str] = None, extra_params: Optional[dict] = None) -> list[dict]:
        q = query
        if site:
            q = f"site:{site} {q}"
        data = {"q": q, "b": "", "kl": "us-en"}
        if page:
            data["s"] = str(page * 30)
            data["dc"] = str(page * 30 + 1)
        if extra_params:
            data.update(extra_params)

        r = await self._client.post(DDG_HTML, data=data)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        results = []
        for div in soup.select(".result, .web-result"):
            a = div.select_one(".result__a, a.result__a")
            if not a:
                continue
            href = _clean_ddg_url(a.get("href", ""))
            snippet_el = div.select_one(".result__snippet")
            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
            results.append({
                "title": a.get_text(" ", strip=True),
                "url": href,
                "domain": urlparse(href).netloc,
                "snippet": snippet,
                "published_date": None,
            })
        return results

    # ------------------------------------------------------------------ #
    # SerpAPI (optional premium backend)
    # ------------------------------------------------------------------ #

    async def _serpapi(self, query: str, engine: str = "google",
                       page: int = 1, extra: Optional[dict] = None) -> list[dict]:
        params = {
            "engine": engine,
            "q": query,
            "api_key": self.serpapi_key,
            "start": (page - 1) * 10,
        }
        if extra:
            params.update(extra)
        r = await self._client.get("https://serpapi.com/search.json", params=params)
        r.raise_for_status()
        data = r.json()

        results = []
        if engine == "google":
            for item in data.get("organic_results", []):
                results.append({
                    "title": item.get("title"),
                    "url": item.get("link"),
                    "domain": urlparse(item.get("link", "")).netloc,
                    "snippet": item.get("snippet"),
                    "published_date": item.get("date"),
                })
        elif engine == "google_news":
            for item in data.get("news_results", []):
                results.append({
                    "title": item.get("title"),
                    "url": item.get("link"),
                    "domain": urlparse(item.get("link", "")).netloc,
                    "snippet": item.get("snippet"),
                    "published_date": item.get("date"),
                })
        elif engine == "google_images":
            for item in data.get("images_results", []):
                results.append({
                    "title": item.get("title"),
                    "url": item.get("link"),
                    "domain": urlparse(item.get("link", "")).netloc,
                    "snippet": item.get("source"),
                    "image_url": item.get("original"),
                })
        elif engine == "google_shopping":
            for item in data.get("shopping_results", []):
                results.append({
                    "title": item.get("title"),
                    "url": item.get("link"),
                    "domain": urlparse(item.get("link", "")).netloc,
                    "snippet": item.get("source"),
                    "price": item.get("price"),
                })
        return results

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def search(self, query: str, *, domains=None, exclude_domains=None,
                     date_from=None, date_to=None, language=None, country=None,
                     max_results: int = 10, page: int = 0, kind: str = "web") -> dict:
        """kind: web | news | images | shopping"""
        domains = domains or []
        results: list[dict] = []

        if self.serpapi_key:
            engine_map = {"web": "google", "news": "google_news",
                          "images": "google_images", "shopping": "google_shopping"}
            engine = engine_map.get(kind, "google")
            extra = {}
            if country: extra["gl"] = country
            if language: extra["hl"] = language
            if date_from and date_to:
                extra["tbs"] = f"cdr:1,cd_min:{date_from},cd_max:{date_to}"
            results = await self._serpapi(query, engine, page + 1, extra)
        else:
            # DuckDuckGo path (web only; other kinds fall back to web with hints)
            if kind == "news":
                query = f"{query} news"
            elif kind == "images":
                query = f"{query} images"
            elif kind == "shopping":
                query = f"{query} price buy"

            # Search across any restricted domains, or unrestricted
            if domains:
                for d in domains:
                    results.extend(await self._ddg(query, page=page, site=d))
                    if len(results) >= max_results * 2:
                        break
            else:
                results.extend(await self._ddg(query, page=page))

        # De-dupe by URL
        seen, deduped = set(), []
        for r in results:
            u = r.get("url")
            if not u or u in seen:
                continue
            seen.add(u)
            deduped.append(r)

        filtered = self._post_filter(deduped, domains, exclude_domains,
                                     date_from, date_to)

        for i, r in enumerate(filtered[:max_results]):
            r["relevance"] = self._score(r, query, i)

        return {
            "query": query,
            "kind": kind,
            "count": len(filtered[:max_results]),
            "results": [
                asdict(SearchResult(**{k: r.get(k) for k in SearchResult.__dataclass_fields__}))
                if False else r
                for r in filtered[:max_results]
            ],
            "backend": "serpapi" if self.serpapi_key else "duckduckgo",
        }

    async def news(self, query: str, **kw):
        return await self.search(query, kind="news", **kw)

    async def images(self, query: str, **kw):
        return await self.search(query, kind="images", **kw)

    async def videos(self, query: str, **kw):
        return await self.search(query, kind="videos", **kw)

    async def shopping(self, query: str, **kw):
        return await self.search(query, kind="shopping", **kw)

    async def suggest(self, query: str) -> list[str]:
        r = await self._client.get(
            "https://duckduckgo.com/ac/",
            params={"q": query, "type": "list"},
        )
        try:
            return [item.get("phrase") for item in r.json() if item.get("phrase")]
        except Exception:
            return []