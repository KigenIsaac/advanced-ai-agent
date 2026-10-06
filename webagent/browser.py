from __future__ import annotations

import asyncio
import base64
import functools
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urljoin

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Locator,
    TimeoutError as PWTimeoutError,
    Error as PWError,
)


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #

class BrowserError(Exception):
    """Base class for all browser-layer errors."""


class NavigationError(BrowserError):
    pass


class ElementNotFound(BrowserError):
    pass


class ActionTimeout(BrowserError):
    pass


class CaptchaDetected(BrowserError):
    pass


class LoginWallDetected(BrowserError):
    pass


class AccessDenied(BrowserError):
    pass


class RateLimited(BrowserError):
    pass


# --------------------------------------------------------------------------- #
# Retry helper (Layer 18)
# --------------------------------------------------------------------------- #

def with_retry(attempts: int = 3, delay: float = 0.5, backoff: float = 2.0,
               exceptions: tuple = (PWTimeoutError, PWError)):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last: Optional[BaseException] = None
            d = delay
            for i in range(attempts):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as e:
                    last = e
                    if i < attempts - 1:
                        await asyncio.sleep(d)
                        d *= backoff
            raise ActionTimeout(str(last)) from last
        return wrapper
    return deco


# --------------------------------------------------------------------------- #
# Injected JS
# --------------------------------------------------------------------------- #

_INTERACTIVE_JS = r"""
() => {
  const results = [];
  const seen = new Set();

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    if (parseFloat(s.opacity) === 0) return false;
    return true;
  }

  function accessibleName(el) {
    const aria = el.getAttribute && el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();
    const lb = el.getAttribute && el.getAttribute('aria-labelledby');
    if (lb) {
      const parts = lb.split(/\s+/).map(id => {
        const n = document.getElementById(id);
        return n ? (n.innerText || n.textContent || '').trim() : '';
      }).filter(Boolean);
      if (parts.length) return parts.join(' ');
    }
    if (el.labels && el.labels.length) {
      const t = Array.from(el.labels)
        .map(l => (l.innerText || l.textContent || '').trim())
        .filter(Boolean).join(' ');
      if (t) return t;
    }
    if (el.tagName === 'IMG') {
      const alt = el.getAttribute('alt');
      if (alt) return alt.trim();
    }
    const ph = el.getAttribute && el.getAttribute('placeholder');
    if (ph && ph.trim()) return ph.trim();
    const title = el.getAttribute && el.getAttribute('title');
    if (title && title.trim()) return title.trim();
    const val = el.value;
    if (typeof val === 'string' && val.trim()) return val.trim();
    const text = (el.innerText || el.textContent || '').trim();
    return text.slice(0, 200);
  }

  function cssPath(el) {
    if (!el || el.nodeType !== 1) return null;
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 6) {
      let sel = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (sibs.length > 1) sel += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(sel);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  }

  const sel = 'a,button,input,select,textarea,[role="button"],[role="link"],' +
              '[role="tab"],[role="menuitem"],[role="checkbox"],[role="radio"],' +
              '[role="switch"],[role="combobox"],[contenteditable="true"],[onclick]';
  for (const el of document.querySelectorAll(sel)) {
    if (!isVisible(el)) continue;
    const path = cssPath(el);
    if (!path || seen.has(path)) continue;
    seen.add(path);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    let kind = role || tag;
    if (!role && tag === 'a') kind = 'link';
    else if (!role && tag === 'button') kind = 'button';
    else if (!role && tag === 'input') {
      kind = 'input:' + (el.getAttribute('type') || 'text').toLowerCase();
    }
    const r = el.getBoundingClientRect();
    results.push({
      kind, tag,
      name: accessibleName(el),
      selector: path,
      href: el.href || null,
      value: typeof el.value === 'string' ? el.value.slice(0, 200) : null,
      placeholder: el.getAttribute ? el.getAttribute('placeholder') : null,
      type: el.getAttribute ? el.getAttribute('type') : null,
      disabled: !!el.disabled,
      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
    });
  }
  return results;
}
"""

_METADATA_JS = r"""
() => {
  const meta = (n) => {
    const el = document.querySelector(`meta[name="${n}"], meta[property="${n}"]`);
    return el ? el.getAttribute('content') : null;
  };
  return {
    title: document.title || null,
    description: meta('description') || meta('og:description'),
    canonical: (document.querySelector('link[rel="canonical"]') || {}).href || null,
    og_title: meta('og:title'),
    og_type: meta('og:type'),
    og_image: meta('og:image'),
    og_url: meta('og:url'),
    lang: document.documentElement.lang || null,
    url: location.href,
    ready_state: document.readyState,
  };
}
"""


# --------------------------------------------------------------------------- #
# Tab bookkeeping
# --------------------------------------------------------------------------- #

@dataclass
class Tab:
    tab_id: str
    page: Page
    created_at: float = field(default_factory=time.time)
    opener: Optional[str] = None


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class BrowserEngine:
    def __init__(
        self,
        headless: bool = True,
        user_agent: Optional[str] = None,
        locale: str = "en-US",
        timezone: str = "America/New_York",
        viewport: tuple[int, int] = (1440, 900),
        proxy: Optional[dict] = None,
        downloads_dir: str | Path = "./downloads",
        slow_mo: int = 0,
    ):
        self.headless = headless
        self.user_agent = user_agent
        self.locale = locale
        self.timezone = timezone
        self.viewport = {"width": viewport[0], "height": viewport[1]}
        self.proxy = proxy
        self.slow_mo = slow_mo
        self.downloads_dir = Path(downloads_dir)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

        self._tabs: dict[str, Tab] = {}
        self._active: Optional[str] = None

        # Network capture: tab_id -> list of {type, ...}
        self._network: dict[str, list[dict]] = {}
        # Map request url -> Response for body fetching
        self._responses: dict[str, Any] = {}

        # Downloads observed per tab
        self._downloads: dict[str, list[dict]] = {}

        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            proxy=self.proxy,
            slow_mo=self.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=self.user_agent,
            locale=self.locale,
            timezone_id=self.timezone,
            viewport=self.viewport,
            accept_downloads=True,
        )
        # Track popups → become tabs
        self._context.on("page", self._on_new_page)

        # Create first tab
        page = await self._context.new_page()
        await self._register_page(page)

    async def close(self):
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
            self._tabs.clear()
            self._active = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ------------------------------------------------------------------ #
    # Tab wiring
    # ------------------------------------------------------------------ #

    async def _register_page(self, page: Page, opener: Optional[str] = None) -> str:
        tab_id = uuid.uuid4().hex[:12]
        self._tabs[tab_id] = Tab(tab_id=tab_id, page=page, opener=opener)
        self._network[tab_id] = []
        self._downloads[tab_id] = []

        # Network listeners (Layer 10)
        def on_request(req):
            self._network[tab_id].append({
                "kind": "request",
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "headers": dict(req.headers),
                "ts": time.time(),
            })

        def on_response(resp):
            entry = {
                "kind": "response",
                "url": resp.url,
                "status": resp.status,
                "headers": dict(resp.headers),
                "ts": time.time(),
            }
            self._network[tab_id].append(entry)
            self._responses[f"{tab_id}:{resp.url}"] = resp

        def on_download(dl):
            self._downloads[tab_id].append({
                "url": dl.url,
                "suggested_filename": dl.suggested_filename,
                "ts": time.time(),
                "_download": dl,
            })

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("download", on_download)
        page.on("close", lambda: self._tabs.pop(tab_id, None))

        self._active = tab_id
        return tab_id

    def _on_new_page(self, page: Page):
        # Fired for window.open / target=_blank
        asyncio.create_task(self._register_page(page, opener=self._active))

    # ------------------------------------------------------------------ #
    # Resolution helpers
    # ------------------------------------------------------------------ #

    def _resolve(self, tab_id: Optional[str] = None) -> Page:
        tid = tab_id or self._active
        if not tid or tid not in self._tabs:
            raise BrowserError("No active tab")
        return self._tabs[tid].page

    def _tab_of(self, tab_id: Optional[str] = None) -> str:
        tid = tab_id or self._active
        if not tid or tid not in self._tabs:
            raise BrowserError("No active tab")
        return tid

    # ------------------------------------------------------------------ #
    # Layer 16 — Browser state
    # ------------------------------------------------------------------ #

    def get_browser_state(self) -> dict:
        return {
            "active_tab": self._active,
            "tabs": [
                {
                    "tab_id": t.tab_id,
                    "url": t.page.url,
                    "title": None,  # filled lazily
                    "created_at": t.created_at,
                    "opener": t.opener,
                }
                for t in self._tabs.values()
            ],
        }

    # ------------------------------------------------------------------ #
    # Layer 15 — Tabs / windows
    # ------------------------------------------------------------------ #

    async def new_tab(self, url: Optional[str] = None) -> str:
        if not self._context:
            raise BrowserError("Engine not started")
        page = await self._context.new_page()
        tab_id = await self._register_page(page)
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        return tab_id

    async def close_tab(self, tab_id: Optional[str] = None) -> None:
        tid = self._tab_of(tab_id)
        page = self._tabs[tid].page
        await page.close()
        self._tabs.pop(tid, None)
        if self._active == tid:
            self._active = next(iter(self._tabs), None)

    async def switch_tab(self, tab_id: str) -> None:
        if tab_id not in self._tabs:
            raise BrowserError(f"Unknown tab {tab_id}")
        self._active = tab_id
        await self._tabs[tab_id].page.bring_to_front()

    async def list_tabs(self) -> list[dict]:
        out = []
        for t in self._tabs.values():
            try:
                title = await t.page.title()
            except Exception:
                title = None
            out.append({
                "tab_id": t.tab_id,
                "url": t.page.url,
                "title": title,
                "active": t.tab_id == self._active,
            })
        return out

    def get_active_tab(self) -> Optional[str]:
        return self._active

    # ------------------------------------------------------------------ #
    # Layer 2 — Navigation / retrieval
    # ------------------------------------------------------------------ #

    @with_retry(attempts=3, delay=0.5)
    async def navigate(self, url: str, wait_until: str = "domcontentloaded",
                       timeout: int = 30_000) -> dict:
        page = self._resolve()
        try:
            resp = await page.goto(url, wait_until=wait_until, timeout=timeout)
        except PWTimeoutError as e:
            raise NavigationError(f"Timed out navigating to {url}: {e}") from e
        await self._check_challenge(page)
        return {
            "url": page.url,
            "status": resp.status if resp else None,
            "title": await page.title(),
        }

    async def go_back(self):
        await self._resolve().go_back(wait_until="domcontentloaded")
        return {"url": self._resolve().url}

    async def go_forward(self):
        await self._resolve().go_forward(wait_until="domcontentloaded")
        return {"url": self._resolve().url}

    async def reload(self):
        await self._resolve().reload(wait_until="domcontentloaded")
        return {"url": self._resolve().url}

    async def get_page_url(self) -> str:
        return self._resolve().url

    async def get_page_title(self) -> str:
        return await self._resolve().title()

    async def get_page_html(self) -> str:
        return await self._resolve().content()

    async def get_page_text(self) -> str:
        return await self._resolve().evaluate(
            "() => (document.body ? document.body.innerText : '')"
        )

    async def get_dom(self) -> str:
        return await self._resolve().evaluate(
            "() => document.documentElement.outerHTML"
        )

    async def get_page_metadata(self) -> dict:
        return await self._resolve().evaluate(_METADATA_JS)

    # ------------------------------------------------------------------ #
    # Layer 3 — JS execution
    # ------------------------------------------------------------------ #

    async def execute_javascript(self, code: str, arg: Any = None) -> Any:
        # Wrap bare statements so Playwright accepts them
        if not code.strip().startswith(("(", "function", "async", "()")):
            code = f"() => {{ {code} }}"
        return await self._resolve().evaluate(code, arg)

    async def evaluate_expression(self, expression: str) -> Any:
        return await self._resolve().evaluate(f"() => ({expression})")

    async def get_computed_style(self, selector: str) -> dict:
        return await self._resolve().evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return null;
                const s = getComputedStyle(el);
                const out = {};
                for (const k of s) out[k] = s.getPropertyValue(k);
                return out;
            }""",
            selector,
        )

    async def get_element_properties(self, selector: str) -> dict:
        return await self._resolve().evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return null;
                const attrs = {};
                for (const a of el.attributes) attrs[a.name] = a.value;
                return {
                    tag: el.tagName.toLowerCase(),
                    attributes: attrs,
                    text: (el.innerText || el.textContent || '').slice(0, 1000),
                    value: typeof el.value === 'string' ? el.value : null,
                    href: el.href || null,
                };
            }""",
            selector,
        )

    # ------------------------------------------------------------------ #
    # Layer 3 — Waiting
    # ------------------------------------------------------------------ #

    async def wait_for_selector(self, selector: str, timeout: int = 15_000,
                                state: str = "visible"):
        await self._resolve().wait_for_selector(selector, state=state, timeout=timeout)
        return {"ok": True}

    async def wait_for_navigation(self, timeout: int = 30_000):
        await self._resolve().wait_for_load_state("load", timeout=timeout)
        return {"ok": True}

    async def wait_for_network_idle(self, timeout: int = 15_000, idle: float = 0.5):
        await self._resolve().wait_for_load_state(
            "networkidle", timeout=timeout
        )
        return {"ok": True}

    async def wait_for_timeout(self, ms: int):
        await self._resolve().wait_for_timeout(ms)
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # Layer 4 — Interaction
    # ------------------------------------------------------------------ #

    @with_retry(attempts=3, delay=0.4)
    async def click(self, selector: str, timeout: int = 10_000, force: bool = False):
        await self._resolve().click(selector, timeout=timeout, force=force)
        return {"clicked": selector}

    async def click_text(self, text: str, timeout: int = 10_000):
        loc = self._resolve().get_by_text(text, exact=False).first
        await loc.click(timeout=timeout)
        return {"clicked_text": text}

    async def double_click(self, selector: str, timeout: int = 10_000):
        await self._resolve().dblclick(selector, timeout=timeout)
        return {"ok": True}

    async def right_click(self, selector: str, timeout: int = 10_000):
        await self._resolve().click(selector, button="right", timeout=timeout)
        return {"ok": True}

    async def hover(self, selector: str, timeout: int = 10_000):
        await self._resolve().hover(selector, timeout=timeout)
        return {"ok": True}

    async def focus(self, selector: str):
        await self._resolve().focus(selector)
        return {"ok": True}

    async def scroll(self, direction: str = "down", amount: int = 800):
        dx = dy = 0
        if direction == "down": dy = amount
        elif direction == "up": dy = -amount
        elif direction == "right": dx = amount
        elif direction == "left": dx = -amount
        await self._resolve().evaluate(f"window.scrollBy({dx}, {dy})")
        return {"ok": True}

    async def scroll_to(self, selector: str):
        await self._resolve().locator(selector).scroll_into_view_if_needed()
        return {"ok": True}

    async def drag(self, source: str, target: str):
        await self._resolve().drag_and_drop(source, target)
        return {"ok": True}

    async def press_key(self, key: str):
        await self._resolve().keyboard.press(key)
        return {"ok": True}

    async def type_text(self, selector: str, text: str, delay: int = 20):
        await self._resolve().type(selector, text, delay=delay)
        return {"ok": True}

    async def fill(self, selector: str, text: str):
        await self._resolve().fill(selector, text)
        return {"ok": True}

    async def clear_input(self, selector: str):
        await self._resolve().fill(selector, "")
        return {"ok": True}

    async def select_option(self, selector: str, value: str):
        await self._resolve().select_option(selector, value)
        return {"ok": True}

    async def check(self, selector: str):
        await self._resolve().check(selector)
        return {"ok": True}

    async def uncheck(self, selector: str):
        await self._resolve().uncheck(selector)
        return {"ok": True}

    async def upload_file(self, selector: str, file_path: str):
        await self._resolve().set_input_files(selector, file_path)
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # Layer 5 — Link helpers
    # ------------------------------------------------------------------ #

    async def get_links(self) -> list[dict]:
        return await self._resolve().evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: (a.innerText || a.textContent || '').trim().slice(0, 200),
                href: a.href,
                target: a.target || null,
            }))"""
        )

    async def click_link(self, text: str):
        return await self.click_text(text)

    async def open_link(self, url: str):
        return await self.navigate(url)

    async def open_in_new_tab(self, url: str) -> str:
        return await self.new_tab(url)

    # ------------------------------------------------------------------ #
    # Layer 6 — Forms
    # ------------------------------------------------------------------ #

    async def get_forms(self) -> list[dict]:
        return await self._resolve().evaluate(
            """() => Array.from(document.forms).map((f, i) => ({
                index: i,
                name: f.name || null,
                action: f.action || null,
                method: (f.method || 'get').toLowerCase(),
                fields: Array.from(f.elements).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || null,
                    name: el.name || null,
                    id: el.id || null,
                    value: typeof el.value === 'string' ? el.value : null,
                    placeholder: el.placeholder || null,
                    required: !!el.required,
                    options: el.tagName === 'SELECT'
                        ? Array.from(el.options).map(o => ({value: o.value, text: o.text}))
                        : null,
                })),
            }))"""
        )

    async def get_form_fields(self, form_index: int = 0) -> list[dict]:
        forms = await self.get_forms()
        if form_index >= len(forms):
            raise ElementNotFound(f"No form at index {form_index}")
        return forms[form_index]["fields"]

    async def fill_form(self, form_index: int, values: dict[str, str]):
        fields = await self.get_form_fields(form_index)
        for f in fields:
            key = f.get("name") or f.get("id")
            if key and key in values:
                sel = f'[name="{key}"]' if f.get("name") else f'#{f["id"]}'
                if f["tag"] == "select":
                    await self.select_option(sel, values[key])
                else:
                    await self.fill(sel, values[key])
        return {"ok": True}

    async def submit_form(self, form_index: int = 0):
        await self._resolve().evaluate(
            "(i) => { const f = document.forms[i]; if (f) f.submit(); }",
            form_index,
        )
        return {"ok": True}

    async def get_validation_errors(self) -> list[str]:
        return await self._resolve().evaluate(
            """() => Array.from(document.querySelectorAll(':invalid'))
                .map(el => el.validationMessage).filter(Boolean)"""
        )

    # ------------------------------------------------------------------ #
    # Layer 7 — Session / auth storage
    # ------------------------------------------------------------------ #

    async def get_cookies(self) -> list[dict]:
        return await self._context.cookies()

    async def set_cookie(self, cookie: dict):
        await self._context.add_cookies([cookie])
        return {"ok": True}

    async def delete_cookie(self, name: str, domain: Optional[str] = None):
        cookies = await self._context.cookies()
        keep = [c for c in cookies if not (c["name"] == name and (domain is None or c["domain"] == domain))]
        await self._context.clear_cookies()
        if keep:
            await self._context.add_cookies(keep)
        return {"ok": True}

    async def get_local_storage(self) -> dict:
        return await self._resolve().evaluate(
            "() => Object.fromEntries(Object.entries(localStorage))"
        )

    async def set_local_storage(self, key: str, value: str):
        await self._resolve().evaluate(
            "([k, v]) => localStorage.setItem(k, v)", [key, value]
        )
        return {"ok": True}

    async def get_session_storage(self) -> dict:
        return await self._resolve().evaluate(
            "() => Object.fromEntries(Object.entries(sessionStorage))"
        )

    async def set_session_storage(self, key: str, value: str):
        await self._resolve().evaluate(
            "([k, v]) => sessionStorage.setItem(k, v)", [key, value]
        )
        return {"ok": True}

    async def clear_session(self):
        if self._context:
            await self._context.clear_cookies()
        return {"ok": True}

    async def save_session(self, path: str):
        await self._context.storage_state(path=path)
        return {"path": path}

    async def restore_session(self, path: str):
        # Replace context with one seeded from saved state
        state = json.loads(Path(path).read_text())
        old = self._context
        self._context = await self._browser.new_context(
            storage_state=state,
            user_agent=self.user_agent,
            locale=self.locale,
            timezone_id=self.timezone,
            viewport=self.viewport,
            accept_downloads=True,
        )
        self._context.on("page", self._on_new_page)
        self._tabs.clear()
        page = await self._context.new_page()
        await self._register_page(page)
        await old.close()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # Layer 8 — Screenshots
    # ------------------------------------------------------------------ #

    async def screenshot(self, path: Optional[str] = None, full_page: bool = False) -> dict:
        data = await self._resolve().screenshot(full_page=full_page, type="png")
        out = {"format": "png", "bytes": len(data)}
        if path:
            Path(path).write_bytes(data)
            out["path"] = path
        else:
            out["base64"] = base64.b64encode(data).decode("ascii")
        return out

    async def screenshot_element(self, selector: str, path: Optional[str] = None) -> dict:
        data = await self._resolve().locator(selector).screenshot(type="png")
        out = {"format": "png", "bytes": len(data)}
        if path:
            Path(path).write_bytes(data)
            out["path"] = path
        else:
            out["base64"] = base64.b64encode(data).decode("ascii")
        return out

    async def get_viewport(self) -> dict:
        return self._resolve().viewport_size or {}

    async def set_viewport(self, width: int, height: int):
        await self._resolve().set_viewport_size({"width": width, "height": height})
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # Layer 9 — Accessibility / semantic representation
    # ------------------------------------------------------------------ #

    async def get_accessibility_tree(self) -> list[dict]:
        page = self._resolve()
        cdp = await page.context.new_cdp_session(page)
        try:
            result = await cdp.send("Accessibility.getFullAXTree")
            nodes = result.get("nodes", [])
            return [
                {
                    "role": (n.get("role") or {}).get("value"),
                    "name": (n.get("name") or {}).get("value"),
                    "description": (n.get("description") or {}).get("value"),
                    "value": (n.get("value") or {}).get("value"),
                    "ignored": n.get("ignored", False),
                    "node_id": n.get("nodeId"),
                    "backend_id": n.get("backendDOMNodeId"),
                }
                for n in nodes
            ]
        finally:
            await cdp.detach()

    async def get_interactive_elements(self) -> list[dict]:
        return await self._resolve().evaluate(_INTERACTIVE_JS)

    async def get_buttons(self) -> list[dict]:
        els = await self.get_interactive_elements()
        return [e for e in els if e["kind"] in ("button", "input:submit", "input:button")]

    async def get_inputs(self) -> list[dict]:
        els = await self.get_interactive_elements()
        return [e for e in els if e["kind"].startswith("input:")
                or e["tag"] in ("textarea", "select")]

    async def get_headings(self) -> list[dict]:
        return await self._resolve().evaluate(
            """() => Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))
                .map(h => ({level: +h.tagName[1], text: h.innerText.trim().slice(0, 300)}))"""
        )

    async def semantic_snapshot(self, max_elements: int = 200) -> str:
        """Compact BUTTON / LINK / INPUT listing for LLM context."""
        els = await self.get_interactive_elements()
        lines = []
        for i, e in enumerate(els[:max_elements]):
            name = (e["name"] or "").replace("\n", " ").strip()[:120]
            if e["kind"] == "link":
                lines.append(f'[{i}] LINK "{name}" -> {e["href"]}')
            elif e["kind"].startswith("input:"):
                lines.append(
                    f'[{i}] INPUT type={e["type"]} placeholder="{e["placeholder"]}" '
                    f'name="{name}"'
                )
            else:
                lines.append(f'[{i}] {e["kind"].upper()} "{name}"')
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Layer 10 — Network inspection
    # ------------------------------------------------------------------ #

    async def get_network_requests(self, tab_id: Optional[str] = None,
                                   kind: Optional[str] = None) -> list[dict]:
        tid = self._tab_of(tab_id)
        entries = self._network.get(tid, [])
        if kind:
            entries = [e for e in entries if e["kind"] == kind]
        return entries

    async def get_network_responses(self, tab_id: Optional[str] = None) -> list[dict]:
        return await self.get_network_requests(tab_id, kind="response")

    async def get_api_calls(self, tab_id: Optional[str] = None) -> list[dict]:
        entries = await self.get_network_requests(tab_id)
        return [
            e for e in entries
            if e["kind"] == "request"
            and e["resource_type"] in ("xhr", "fetch")
        ]

    async def get_response_body(self, url: str, tab_id: Optional[str] = None) -> Optional[str]:
        tid = self._tab_of(tab_id)
        resp = self._responses.get(f"{tid}:{url}")
        if not resp:
            return None
        try:
            return await resp.text()
        except Exception:
            return None

    async def get_response_headers(self, url: str, tab_id: Optional[str] = None) -> Optional[dict]:
        tid = self._tab_of(tab_id)
        resp = self._responses.get(f"{tid}:{url}")
        return dict(resp.headers) if resp else None

    # ------------------------------------------------------------------ #
    # Layer 11 — Downloads
    # ------------------------------------------------------------------ #

    async def download(self, url: str, save_as: Optional[str] = None) -> dict:
        page = self._resolve()
        async with page.expect_download() as dl_info:
            await page.goto(url)
        dl = await dl_info.value
        target = Path(save_as) if save_as else self.downloads_dir / dl.suggested_filename
        await dl.save_as(str(target))
        return {"path": str(target), "suggested_filename": dl.suggested_filename}

    async def download_link(self, selector: str, save_as: Optional[str] = None) -> dict:
        page = self._resolve()
        async with page.expect_download() as dl_info:
            await page.click(selector)
        dl = await dl_info.value
        target = Path(save_as) if save_as else self.downloads_dir / dl.suggested_filename
        await dl.save_as(str(target))
        return {"path": str(target), "suggested_filename": dl.suggested_filename}

    async def wait_for_download(self, timeout: int = 30_000) -> dict:
        page = self._resolve()
        async with page.expect_download(timeout=timeout) as dl_info:
            pass
        dl = await dl_info.value
        target = self.downloads_dir / dl.suggested_filename
        await dl.save_as(str(target))
        return {"path": str(target), "suggested_filename": dl.suggested_filename}

    def get_downloads(self, tab_id: Optional[str] = None) -> list[dict]:
        tid = self._tab_of(tab_id)
        return [{k: v for k, v in d.items() if k != "_download"}
                for d in self._downloads.get(tid, [])]

    # ------------------------------------------------------------------ #
    # Layer 13 — Frames / iframes
    # ------------------------------------------------------------------ #

    async def get_frames(self) -> list[dict]:
        return [
            {"name": f.name, "url": f.url, "is_main": f == self._resolve().main_frame}
            for f in self._resolve().frames
        ]

    def switch_frame(self, name_or_url: Optional[str] = None, index: Optional[int] = None):
        page = self._resolve()
        frames = page.frames
        if index is not None:
            return frames[index]
        for f in frames:
            if f.name == name_or_url or f.url == name_or_url:
                return f
        raise ElementNotFound(f"No frame matching {name_or_url}")

    async def get_frame_content(self, name_or_url: Optional[str] = None,
                                index: Optional[int] = None) -> str:
        frame = self.switch_frame(name_or_url, index)
        return await frame.content()

    # ------------------------------------------------------------------ #
    # Layer 14 — Shadow DOM
    # ------------------------------------------------------------------ #
    # Playwright's CSS engine pierces open shadow roots automatically,
    # so `click("button")` already works inside them. For explicit root
    # access, we expose this helper for closed or nested cases.

    async def query_shadow_dom(self, host_selector: str, inner_selector: str) -> list[dict]:
        return await self._resolve().evaluate(
            """([host, inner]) => {
                const h = document.querySelector(host);
                if (!h || !h.shadowRoot) return [];
                return Array.from(h.shadowRoot.querySelectorAll(inner)).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || el.textContent || '').trim().slice(0, 200),
                    id: el.id || null,
                }));
            }""",
            [host_selector, inner_selector],
        )

    # ------------------------------------------------------------------ #
    # Layer 17 — Challenge / bot-detection reporting
    # ------------------------------------------------------------------ #

    _CAPTCHA_HINTS = [
        "recaptcha", "g-recaptcha", "hcaptcha", "cf-turnstile",
        "captcha", "are you a robot",
    ]
    _LOGIN_HINTS = [
        "sign in to continue", "log in to continue", "please log in",
        "you must be signed in", "create an account to continue",
    ]
    _DENIED_HINTS = ["access denied", "403 forbidden", "you don't have permission"]
    _RATE_HINTS = ["rate limit", "too many requests", "slow down", "429"]

    async def _check_challenge(self, page: Optional[Page] = None):
        p = page or self._resolve()
        try:
            text = (await p.inner_text("body")).lower()
        except Exception:
            return
        html = (await p.content()).lower()
        haystack = text + "\n" + html

        if any(h in haystack for h in self._CAPTCHA_HINTS):
            raise CaptchaDetected("Site requires human verification (CAPTCHA).")
        if any(h in haystack for h in self._LOGIN_HINTS):
            raise LoginWallDetected("Site requires login.")
        if any(h in haystack for h in self._DENIED_HINTS):
            raise AccessDenied("Access denied by site.")
        if any(h in haystack for h in self._RATE_HINTS):
            raise RateLimited("Site rate-limited the request.")

    async def detect_captcha(self) -> bool:
        try:
            await self._check_challenge()
        except CaptchaDetected:
            return True
        except BrowserError:
            return False
        return False

    async def detect_bot_challenge(self) -> dict:
        page = self._resolve()
        try:
            text = (await page.inner_text("body")).lower()
        except Exception:
            text = ""
        return {
            "captcha": any(h in text for h in self._CAPTCHA_HINTS),
            "login_wall": any(h in text for h in self._LOGIN_HINTS),
            "access_denied": any(h in text for h in self._DENIED_HINTS),
            "rate_limit": any(h in text for h in self._RATE_HINTS),
        }

    # ------------------------------------------------------------------ #
    # Layer 19 — Page understanding (natural-language lookup)
    # ------------------------------------------------------------------ #

    async def find_element(self, description: str, kind: Optional[str] = None) -> Optional[dict]:
        els = await self.get_interactive_elements()
        if kind:
            els = [e for e in els if kind.lower() in e["kind"].lower()]
        desc = description.lower()
        tokens = set(re.findall(r"[a-z0-9]+", desc))

        def score(e: dict) -> float:
            name = (e.get("name") or "").lower()
            if not name:
                return 0.0
            s = 0.0
            if desc in name:
                s += 3.0
            name_tokens = set(re.findall(r"[a-z0-9]+", name))
            s += len(tokens & name_tokens) * 1.0
            if e.get("kind") == "button" and "button" in desc:
                s += 0.5
            if e.get("kind") == "link" and ("link" in desc or "click" in desc):
                s += 0.5
            return s

        ranked = sorted(els, key=score, reverse=True)
        return ranked[0] if ranked and score(ranked[0]) > 0 else None

    async def find_button(self, description: str):
        return await self.find_element(description, kind="button")

    async def find_link(self, description: str):
        return await self.find_element(description, kind="link")

    async def find_input(self, description: str):
        return await self.find_element(description, kind="input")

    async def find_text(self, text: str) -> bool:
        page = self._resolve()
        try:
            count = await page.locator(f"text={text}").count()
            return count > 0
        except Exception:
            return False

    async def find_main_content(self) -> str:
        return await self._resolve().evaluate(
            """() => {
                const cands = ['main', 'article', '[role="main"]',
                    '#content', '#main', '.content', '.post', '.article'];
                for (const s of cands) {
                    const el = document.querySelector(s);
                    if (el && el.innerText && el.innerText.length > 200)
                        return el.innerText;
                }
                return document.body ? document.body.innerText : '';
            }"""
        )

    async def find_article(self) -> dict:
        return await self._resolve().evaluate(
            """() => {
                const el = document.querySelector('article, [role="article"], .post, .article');
                if (!el) return null;
                return {
                    title: (el.querySelector('h1,h2') || {}).innerText || null,
                    text: el.innerText,
                };
            }"""
        )

    # ------------------------------------------------------------------ #
    # Layer 18 — Error recovery
    # ------------------------------------------------------------------ #

    async def retry(self, coro_factory, attempts: int = 3, delay: float = 0.6):
        last = None
        for i in range(attempts):
            try:
                return await coro_factory()
            except (PWTimeoutError, PWError, BrowserError) as e:
                last = e
                await asyncio.sleep(delay * (2 ** i))
        raise ActionTimeout(str(last)) from last

    async def recover_navigation(self, url: str):
        await self._resolve().goto("about:blank")
        return await self.navigate(url)

    async def recover_stale_element(self, selector: str):
        await self.wait_for_selector(selector, timeout=10_000)
        return {"ok": True}

    async def recover_timeout(self, action: str, selector: Optional[str] = None):
        await self._resolve().wait_for_timeout(1000)
        if action == "reload":
            return await self.reload()
        if action == "wait" and selector:
            return await self.wait_for_selector(selector)
        return {"ok": True}