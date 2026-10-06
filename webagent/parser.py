from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


def html_to_text(html: str, url: str = "") -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        tag.decompose()
    # Prefer main content containers
    for sel in ("main", "article", '[role="main"]', "#content", "#main"):
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 200:
            soup = node
            break
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def extract_metadata(html: str, url: str = "") -> dict:
    soup = BeautifulSoup(html, "lxml")

    def meta(name: str, attr: str = "name") -> Optional[str]:
        el = soup.find("meta", attrs={attr: name})
        return el.get("content") if el else None

    canonical = soup.find("link", rel="canonical")
    return {
        "title": (soup.title.string.strip() if soup.title and soup.title.string else None),
        "description": meta("description") or meta("og:description", "property"),
        "canonical": canonical.get("href") if canonical else None,
        "og_title": meta("og:title", "property"),
        "og_type": meta("og:type", "property"),
        "og_image": meta("og:image", "property"),
        "og_url": meta("og:url", "property"),
        "lang": (soup.html.get("lang") if soup.html else None),
        "url": url,
    }


def extract_tables(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for i, table in enumerate(soup.find_all("table")):
        headers: list[str] = []
        thead = table.find("thead")
        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all(["th", "td"])]
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            if not headers and all(c for c in cells):
                headers = cells
                continue
            rows.append(cells)
        out.append({"index": i, "headers": headers, "rows": rows})
    return out


def extract_links(html: str, base_url: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]) if base_url else a["href"]
        out.append({
            "text": a.get_text(" ", strip=True),
            "href": href,
            "domain": urlparse(href).netloc,
        })
    return out


def extract_forms(html: str, base_url: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    forms = []
    for i, form in enumerate(soup.find_all("form")):
        fields = []
        for el in form.find_all(["input", "select", "textarea", "button"]):
            options = None
            if el.name == "select":
                options = [{"value": o.get("value"), "text": o.get_text(strip=True)}
                           for o in el.find_all("option")]
            fields.append({
                "tag": el.name,
                "type": el.get("type"),
                "name": el.get("name"),
                "id": el.get("id"),
                "placeholder": el.get("placeholder"),
                "required": el.has_attr("required"),
                "options": options,
            })
        action = form.get("action")
        forms.append({
            "index": i,
            "action": urljoin(base_url, action) if action and base_url else action,
            "method": (form.get("method") or "get").lower(),
            "fields": fields,
        })
    return forms