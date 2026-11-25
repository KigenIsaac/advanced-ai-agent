import asyncio
import os
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from docx import Document
from pypdf import PdfWriter
from playwright.async_api import async_playwright
import pytest

from codeagent.workspace import Workspace
from main import ToolRegistry
from webagent.agent import WebAgent


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(tmp_path)
    registry = ToolRegistry(None, SimpleNamespace(ws=ws))

    def run(skill, **options):
        return asyncio.run(registry.dispatch("skill_run", {"skill":skill,"options":options}))

    return ws, registry, run


def test_word_edit_export_and_table_order(workspace):
    ws, _, run = workspace
    def invoke(skill, **opts):
        return run("document_"+skill, **opts)["result"]
    invoke("create_docx", title="Minutes",text="First item",output="minutes.docx")
    source = Document(ws.root / "minutes.docx")
    source.add_table(rows=1, cols=1).cell(0,0).text = "Total"
    source.save(ws.root / "minutes.docx")
    before = (ws.root / "minutes.docx").read_bytes()
    invoke("append_section", path="minutes.docx",heading="Actions",text="Send notes",output="revision.docx")
    assert (ws.root / "minutes.docx").read_bytes() == before
    assert [p["text"] for p in invoke("outline",path="revision.docx")] == ["Minutes","Actions"]
    assert invoke("read_table",path="revision.docx") == [["Total"]]
    invoke("export_markdown",path="revision.docx",output="export/notes.md")
    md = (ws.root/"export/notes.md").read_text()
    assert md.index("First item") < md.index("| Total |") < md.index("# Actions")
    assert run("document_create_docx",title="x",text="y",output="minutes.docx")["type"] == "FileExistsError"
    assert (ws.root / "minutes.docx").read_bytes() == before


def test_pdf_range_and_access(workspace):
    ws,registry,run = workspace
    writer = PdfWriter()
    writer.add_blank_page(100,100)
    writer.write(ws.root/"blank.pdf")
    assert run("document_pdf_pages",path="blank.pdf")["result"] == {"total_pages":1,"pages":[{"page":1,"text":""}]}
    assert "error" in run("document_pdf_pages",path="blank.pdf",first=0)
    assert "error" in run("document_outline",path="../a.docx")
    registry.disable("workspace")
    assert run("document_pdf_pages",path="blank.pdf")["error"] == "backend_unavailable"


def test_project_workflows_in_real_repository(workspace):
    ws,registry,run = workspace
    def git(*args):
        subprocess.run(["git","-C",str(ws.root),*args],check=True,capture_output=True)
    git("init")
    ws.write_file("src/account.py","class Account:\n    async def balance(self) -> int:\n        return 1\n")
    ws.write_file("tests/test_account.py","")
    ws.write_file("pyproject.toml","")
    git("add",".")
    assert len(run("project_change_summary")["result"]["staged"]) == 3
    git("-c","user.name=Fixture","-c","user.email=test@example.org","commit","-m","Initial ledger")
    ws.write_file("extra.txt","draft")
    ws.write_file("pyproject.toml","# updated")
    assert run("project_inventory")["result"]["manifests"] == ["pyproject.toml"]
    assert run("project_change_summary")["result"] == {"staged":[],"unstaged":["pyproject.toml"],"untracked":["extra.txt"]}
    assert run("project_recent_changes")["result"][0]["subject"] == "Initial ledger"
    assert run("project_related_tests",path="src/account.py")["result"]["candidates"] == ["tests/test_account.py"]
    rows = run("project_public_api",path="src/account.py")["result"]
    assert (rows[1]["name"],rows[1]["returns"]) == ("Account.balance","int")
    registry.disable("git")
    assert run("project_inventory")["error"] == "backend_unavailable"


def test_search_source_projection_and_error():
    web = SimpleNamespace(web_search=AsyncMock(return_value={"results":[{"title":"Docs","url":"https://example.org","snippet":"Guide"}]}))
    registry = ToolRegistry(web,None)
    args = {"skill":"web_search_sources","options":{"query":"guide"}}
    result = asyncio.run(registry.dispatch("skill_run",args))
    assert result["result"][0]["title"] == "Docs"
    web.web_search.assert_awaited_once_with(query="guide",max_results=5)
    web.web_search.return_value={"error":"rate limited"}
    assert asyncio.run(registry.dispatch("skill_run",args))["error"] == "rate limited"


def test_live_surf_and_page_controls(tmp_path, monkeypatch):
    for key in ("all_proxy", "http_proxy", "https_proxy", "ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path/"index.html").write_text("""<title>Ledger</title><main><h1>Members</h1>
    <button onclick="this.textContent='Saved'">Save</button><a href="next.html">Next</a><a href="next.html">More</a><label>Name<input></label>
    <label for="county">County</label><select id="county"><option value="n">Nairobi</option></select>
    <label>Active<input type="checkbox"></label><table><tr><th>Name</th></tr><tr><td>Isaac</td></tr></table></main>""")
    (tmp_path/"next.html").write_text("<title>Help</title><main>Next page</main>")
    http = ThreadingHTTPServer(("127.0.0.1",0),partial(SimpleHTTPRequestHandler,directory=str(tmp_path)))
    thread = threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()

    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, executable_path=(
                os.environ.get("TEST_CHROMIUM_EXECUTABLE") or shutil.which("google-chrome")))
            web = WebAgent(downloads_dir=str(tmp_path/"downloads"))
            try:
                page = await browser.new_page()
                tab = await web.browser._register_page(page)
                registry = ToolRegistry(web,None)
                async def run(name, **opts):
                    return await registry.dispatch("skill_run",{"skill":"web_"+name,"options":{"tab_id":tab,**opts}})
                assert (await run("visit",url=f"http://127.0.0.1:{http.server_port}/"))["result"]["title"] == "Ledger"
                assert (await run("read_page"))["result"]["headings"][0]["text"] == "Members"
                assert "Saved" in (await run("click_button",label="Save"))["result"]["text"]
                assert len((await run("list_links"))["result"]) == 1
                assert (await run("fill_field",label="Name",value="Isaac"))["result"]["matches"]
                assert (await run("choose_option",label="County",value="n"))["result"]["selected"] == ["n"]
                assert (await run("set_checkbox",label="Active",checked=True))["result"]["checked"]
                assert (await run("read_table"))["result"]["rows"] == [["Name"],["Isaac"]]
                assert (await run("follow_link",label="Next"))["result"]["text"] == "Next page"
                assert "error" in await run("visit",url="file:///tmp/private")
                registry.disable("web_browser")
                assert (await run("read_page"))["error"] == "backend_unavailable"
            finally:
                await web.search_engine.close()
                await browser.close()
    try:
        asyncio.run(scenario())
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def test_docx_replace_across_runs(workspace):
    ws,_,run = workspace
    doc = Document()
    p = doc.add_paragraph()
    p.add_run("old na").bold = True
    p.add_run("me and old name").italic = True
    doc.save(ws.root/"original.docx")
    result = run("document_replace_text",path="original.docx",old="old name",new="Kenya",output="new.docx")
    assert result["result"]["replacements"] == 2
    text = Document(ws.root/"new.docx").paragraphs[0]
    assert text.text == "Kenya and Kenya" and text.runs[0].bold and text.runs[1].italic


def test_api_diff_ignores_line_movement(workspace):
    ws,_,run = workspace
    ws.write_file("old.py","def stable(): pass\ndef update(a): pass\nclass Removed: pass")
    ws.write_file("new.py","\n\ndef stable(): pass\ndef update(a,b): pass\nclass Added: pass")
    out = run("project_api_diff",before="old.py",after="new.py")["result"]
    assert (out["added"],out["removed"],[v["name"] for v in out["changed"]]) == (["Added"],["Removed"],["update"])
