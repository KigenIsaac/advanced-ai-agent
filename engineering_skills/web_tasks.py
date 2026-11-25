"""Interactive, single-page workflows using the attached browser and search backend."""
from urllib.parse import urlsplit


def http_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("expected an HTTP or HTTPS page URL")
    return url


async def web_search_sources(web, query):
    """Search the web for five sources and return titles, URLs and snippets."""
    response = await web.web_search(query=query, max_results=5)
    if "error" in response:
        return response
    return [{"title": r.get("title"), "url": r.get("url"), "snippet": (r.get("snippet") or "")[:500]}
            for r in response["results"][:5]]


async def snapshot(page):
    response = await page.evaluate("""() => ({
        url: location.href, title: document.title,
        headings: Array.from(document.querySelectorAll('h1,h2,h3')).slice(0,40)
            .map(h => ({level:Number(h.tagName[1]), text:h.innerText.slice(0,200)})),
        text:(document.querySelector('main,article') || document.body)?.innerText.slice(0,8000) || ''
    })""")
    return response


async def web_visit(web, url, tab_id=None):
    """Surf to an HTTP(S) URL and read its title, main text and headings."""
    page = web.browser._resolve(tab_id)
    response = await page.goto(http_url(url), wait_until="domcontentloaded", timeout=20000)
    await web.browser._check_challenge(page)
    return {**await snapshot(page), "http_status": response.status if response else None}


async def web_read_page(web, tab_id=None):
    """Read the active or named tab without navigating away."""
    return await snapshot(web.browser._resolve(tab_id))


async def web_list_links(web, tab_id=None):
    """List up to 50 distinct HTTP(S) destinations with their visible link labels."""
    return await web.browser._resolve(tab_id).evaluate("""() => {
        const links = new Map();
        for (const el of document.links) {
            if (el.getClientRects().length && ['https:', 'http:'].includes(el.protocol) && !links.has(el.href))
                links.set(el.href, {url:el.href.slice(0,2000), label:el.innerText.trim().slice(0,200)});
            if (links.size === 50) break;
        }
        return [...links.values()];
    }""")


async def web_follow_link(web, label, tab_id=None):
    """Follow one uniquely named visible link in the same tab and read its destination."""
    tab_id = web.browser._tab_of(tab_id)
    page = web.browser._resolve(tab_id)
    locator = page.get_by_role("link", name=label, exact=True)
    if await locator.count() != 1:
        raise ValueError("expected exactly one link with that accessible name")
    href = await locator.evaluate("el => el.href")
    return await web_visit(web, href, tab_id)


async def web_fill_field(web, label, value, tab_id=None):
    """Fill one labeled text field and verify the entered value without submitting."""
    field = web.browser._resolve(tab_id).get_by_label(label, exact=True)
    await field.fill(value, timeout=5000)
    return {"label": label, "matches": await field.input_value() == value}


async def web_choose_option(web, label, value, tab_id=None):
    """Select an option by value in a labeled dropdown and verify the selection."""
    field = web.browser._resolve(tab_id).get_by_label(label, exact=True)
    selected = await field.select_option(value=value, timeout=5000)
    return {"label": label, "selected": selected}


async def web_set_checkbox(web, label, checked, tab_id=None):
    """Set a named checkbox to a requested state and verify it. checked is a boolean."""
    if type(checked) is not bool:
        raise ValueError("checked must be a boolean")
    field = web.browser._resolve(tab_id).get_by_role("checkbox", name=label, exact=True)
    await field.set_checked(checked, timeout=5000)
    return {"label": label, "checked": await field.is_checked()}


async def web_read_table(web, index=0, tab_id=None):
    """Extract rows from a page table by zero-based index with row and column limits."""
    if type(index) is not int or index < 0:
        raise ValueError("index must be a nonnegative integer")
    tables = web.browser._resolve(tab_id).get_by_role("table")
    if index >= await tables.count():
        raise ValueError("table index out of range")
    return await tables.nth(index).evaluate("""t => ({
        total_rows:t.rows.length,
        rows:Array.from(t.rows).slice(0,30).map(r=>Array.from(r.cells).slice(0,12).map(c=>c.innerText.slice(0,120)))
    })""")


async def web_click_button(web, label, tab_id=None):
    """Click one visible button by its exact accessible name, then read the resulting page."""
    page = web.browser._resolve(tab_id)
    button = page.get_by_role("button", name=label, exact=True)
    if await button.count() != 1:
        raise ValueError("expected exactly one button with that accessible name")
    await button.click(timeout=5000)
    return await snapshot(page)
