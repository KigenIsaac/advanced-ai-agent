from __future__ import annotations

from typing import Any, Optional

from .browser import (
    BrowserEngine, BrowserError, CaptchaDetected,
    LoginWallDetected, AccessDenied, RateLimited,
)
from .documents import read_any
from .search import WebSearch


class WebAgent:
    def __init__(self, *, headless: bool = True,
                 downloads_dir: str = "./downloads",
                 serpapi_key: Optional[str] = None,
                 proxy: Optional[str] = None):
        self.search_engine = WebSearch(serpapi_key=serpapi_key, proxy=proxy)
        self.browser = BrowserEngine(headless=headless, downloads_dir=downloads_dir)

    async def start(self):
        await self.browser.start()

    async def close(self):
        await self.browser.close()
        await self.search_engine.close()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ------------------------------------------------------------------ #
    # Tool 1: web_search
    # ------------------------------------------------------------------ #

    async def web_search(self, **kwargs) -> dict:
        try:
            return await self.search_engine.search(**kwargs)
        except Exception as e:
            return {"error": str(e), "results": [], "count": 0}

    # ------------------------------------------------------------------ #
    # Tool 2: web_browser
    # ------------------------------------------------------------------ #

    async def web_browser(self, action: str, **kw) -> dict:
        b = self.browser
        try:
            return await self._dispatch(b, action, kw)
        except CaptchaDetected:
            return {"error": "captcha_required",
                    "message": "This site requires human verification (CAPTCHA).",
                    "action": action}
        except LoginWallDetected:
            return {"error": "login_required",
                    "message": "This site requires the user to log in.",
                    "action": action}
        except AccessDenied:
            return {"error": "access_denied",
                    "message": "The site returned an access-denied page.",
                    "action": action}
        except RateLimited:
            return {"error": "rate_limited",
                    "message": "The site is rate-limiting requests.",
                    "action": action}
        except BrowserError as e:
            return {"error": type(e).__name__, "message": str(e), "action": action}
        except Exception as e:
            return {"error": "unexpected", "message": str(e), "action": action}

    async def _dispatch(self, b: BrowserEngine, action: str, kw: dict) -> Any:
        sel = kw.get("selector")
        tid = kw.get("tab_id")

        # --- navigation -------------------------------------------------
        if action == "open":          return await b.navigate(kw["url"])
        if action == "back":          return await b.go_back()
        if action == "forward":       return await b.go_forward()
        if action == "reload":        return await b.reload()
        if action == "wait":
            if kw.get("wait_for"):
                return await b.wait_for_selector(kw["wait_for"],
                                                 timeout=kw.get("timeout", 15_000))
            if kw.get("timeout"):
                return await b.wait_for_timeout(kw["timeout"])
            return await b.wait_for_network_idle()

        # --- extraction -------------------------------------------------
        if action == "get_text":       return {"text": await b.get_page_text()}
        if action == "get_html":       return {"html": await b.get_page_html()}
        if action == "get_dom":        return {"dom": await b.get_dom()}
        if action == "get_metadata":   return await b.get_page_metadata()
        if action == "get_url":        return {"url": await b.get_page_url()}
        if action == "get_title":      return {"title": await b.get_page_title()}
        if action == "get_links":      return {"links": await b.get_links()}
        if action == "get_interactive":
            return {"elements": await b.get_interactive_elements()}
        if action == "get_accessibility":
            return {"nodes": await b.get_accessibility_tree()}
        if action == "semantic_snapshot":
            return {"snapshot": await b.semantic_snapshot()}
        if action == "get_headings":   return {"headings": await b.get_headings()}
        if action == "find_main":      return {"text": await b.find_main_content()}

        # --- interaction ------------------------------------------------
        if action == "click":       return await b.click(sel, timeout=kw.get("timeout", 10_000))
        if action == "click_text":  return await b.click_text(kw["text"])
        if action == "type":        return await b.type_text(sel, kw["text"])
        if action == "fill":        return await b.fill(sel, kw.get("text") or kw.get("value", ""))
        if action == "press":       return await b.press_key(kw["key"])
        if action == "scroll":      return await b.scroll(kw.get("direction", "down"),
                                                          kw.get("amount", 800))
        if action == "hover":       return await b.hover(sel)
        if action == "select":      return await b.select_option(sel, kw["value"])
        if action == "check":       return await b.check(sel)
        if action == "uncheck":     return await b.uncheck(sel)
        if action == "upload":      return await b.upload_file(sel, kw["file_path"])

        # --- JS ---------------------------------------------------------
        if action == "execute_js":  return {"result": await b.execute_javascript(kw["script"])}
        if action == "evaluate":    return {"result": await b.evaluate_expression(kw["script"])}

        # --- visual -----------------------------------------------------
        if action == "screenshot":
            return await b.screenshot(full_page=kw.get("full_page", False))
        if action == "screenshot_element":
            return await b.screenshot_element(sel)

        # --- tabs -------------------------------------------------------
        if action == "new_tab":     return {"tab_id": await b.new_tab(kw.get("url"))}
        if action == "close_tab":   await b.close_tab(tid); return {"ok": True}
        if action == "switch_tab":  await b.switch_tab(tid); return {"ok": True}
        if action == "list_tabs":   return {"tabs": await b.list_tabs()}

        # --- frames -----------------------------------------------------
        if action == "get_frames":        return {"frames": await b.get_frames()}
        if action == "get_frame_content": return {"html": await b.get_frame_content(
            name_or_url=kw.get("text"), index=kw.get("form_index"))}

        # --- network ----------------------------------------------------
        if action == "get_network":    return {"entries": await b.get_network_requests(tid)}
        if action == "get_api_calls":  return {"calls": await b.get_api_calls(tid)}
        if action == "get_response_body":
            body = await b.get_response_body(kw["url"], tid)
            return {"body": body}

        # --- downloads --------------------------------------------------
        if action == "download":
            return await b.download(kw["url"], kw.get("save_as"))
        if action == "download_link":
            return await b.download_link(sel, kw.get("save_as"))
        if action == "get_downloads":
            return {"downloads": b.get_downloads(tid)}

        # --- state ------------------------------------------------------
        if action == "get_state":         return b.get_browser_state()
        if action == "save_session":      return await b.save_session(kw["file_path"])
        if action == "restore_session":   return await b.restore_session(kw["file_path"])
        if action == "get_cookies":       return {"cookies": await b.get_cookies()}
        if action == "get_local_storage": return {"items": await b.get_local_storage()}

        # --- lookup -----------------------------------------------------
        if action == "find_element":
            return {"element": await b.find_element(kw["text"])}
        if action == "find_button":
            return {"element": await b.find_button(kw["text"])}
        if action == "find_link":
            return {"element": await b.find_link(kw["text"])}

        # --- detection --------------------------------------------------
        if action == "detect_challenge":
            return await b.detect_bot_challenge()

        # --- recovery ---------------------------------------------------
        if action == "recover_navigation":
            return await b.recover_navigation(kw["url"])

        raise BrowserError(f"Unknown action: {action}")

    # ------------------------------------------------------------------ #
    # Convenience: read a downloaded file
    # ------------------------------------------------------------------ #

    async def read_document(self, path: str) -> dict:
        return read_any(path)