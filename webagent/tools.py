from __future__ import annotations

from typing import Any

WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for pages, news, images, videos, or products. "
            "Returns a list of {title, url, domain, snippet, published_date, relevance}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["web", "news", "images", "videos", "shopping"],
                    "default": "web",
                },
                "domains": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Restrict results to these domains.",
                },
                "exclude_domains": {
                    "type": "array", "items": {"type": "string"},
                },
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                "language": {"type": "string"},
                "country": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
                "page": {"type": "integer", "default": 0},
            },
            "required": ["query"],
        },
    },
}


WEB_BROWSER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_browser",
        "description": (
            "Operate a real Chromium browser. Actions cover navigation, "
            "interaction, extraction, JS evaluation, screenshots, downloads, "
            "tabs, forms, frames, network inspection, and accessibility."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        # navigation
                        "open", "back", "forward", "reload", "wait",
                        # extraction
                        "get_text", "get_html", "get_dom", "get_metadata",
                        "get_url", "get_title", "get_links",
                        "get_interactive", "get_accessibility",
                        "semantic_snapshot", "get_headings", "find_main",
                        # interaction
                        "click", "click_text", "type", "fill", "press",
                        "scroll", "hover", "select", "check", "uncheck",
                        "upload",
                        # js
                        "execute_js", "evaluate",
                        # visual
                        "screenshot", "screenshot_element",
                        # tabs
                        "new_tab", "close_tab", "switch_tab", "list_tabs",
                        # frames
                        "get_frames", "get_frame_content",
                        # network
                        "get_network", "get_api_calls", "get_response_body",
                        # downloads
                        "download", "download_link", "get_downloads",
                        # state
                        "get_state", "save_session", "restore_session",
                        "get_cookies", "get_local_storage",
                        # lookup
                        "find_element", "find_button", "find_link",
                        # detection
                        "detect_challenge",
                        # recovery
                        "recover_navigation",
                    ],
                },
                "url": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "value": {"type": "string"},
                "key": {"type": "string"},
                "tab_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer"},
                "timeout": {"type": "integer"},
                "wait_for": {"type": "string"},
                "full_page": {"type": "boolean"},
                "save_as": {"type": "string"},
                "script": {"type": "string"},
                "file_path": {"type": "string"},
                "form_index": {"type": "integer"},
                "values": {"type": "object"},
            },
            "required": ["action"],
        },
    },
}


TOOLS = [WEB_SEARCH_SCHEMA, WEB_BROWSER_SCHEMA]