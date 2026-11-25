"""Local document inspection and copy-on-write editing."""
import io
import os
import re
import tempfile
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from pypdf import PdfReader


def _path(ws, path, extension):
    target = ws._resolve(path)
    if target.suffix.lower() != extension:
        raise ValueError(f"expected {extension} path")
    return target


def _source(ws, path, extension):
    target = _path(ws, path, extension)
    if not target.is_file() or target.stat().st_size > 8_000_000:
        raise ValueError("expected a regular document up to 8 MB")
    return target


def _docx(ws, path):
    source = _source(ws, path, ".docx")
    with ZipFile(source) as archive:
        if len(archive.infolist()) > 2000 or sum(x.file_size for x in archive.infolist()) > 40_000_000:
            raise ValueError("document exceeds the unpacked size limit")
    return Document(source)


def _publish(ws, output, ext, data):
    target = _path(ws, output, ext)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        name = Path(tmp.name)
    try:
        name.write_bytes(data)
        os.link(name, target)  # Atomic creation, including concurrent calls to the same destination.
    finally:
        name.unlink(missing_ok=True)
    return {"output": ws._rel(target), "bytes": len(data)}


def _save(ws, output, doc):
    buffer = io.BytesIO()
    doc.save(buffer)
    return _publish(ws, output, ".docx", buffer.getvalue())


def document_create_docx(ws, title, text, output):
    """Create a new Word document with a title and one paragraph per input line."""
    doc = Document()
    doc.add_heading(title, 0)
    for line in text.splitlines():
        doc.add_paragraph(line)
    return _save(ws, output, doc)


def document_append_section(ws, path, heading, text, output):
    """Add a heading and paragraphs to a Word document and save a new DOCX."""
    doc = _docx(ws, path)
    doc.add_heading(heading, 1)
    for line in text.splitlines():
        doc.add_paragraph(line)
    return _save(ws, output, doc)


def document_outline(ws, path):
    """List top-level Word title and heading paragraphs in order."""
    doc = _docx(ws, path)
    return [{"style": p.style.name, "text": p.text} for p in doc.paragraphs
            if p.style and (p.style.name == "Title" or p.style.name.startswith("Heading "))]


def document_read_table(ws, path, index=0):
    """Read a Word table as rows of cell text using a zero-based table index."""
    tables = _docx(ws, path).tables
    if type(index) is not int or not 0 <= index < len(tables):
        raise ValueError("table index out of range")
    return [[cell.text for cell in row.cells] for row in tables[index].rows]


def document_export_markdown(ws, path, output):
    """Convert Word body headings, paragraphs and tables into a new Markdown file."""
    from docx.table import Table
    doc = _docx(ws, path)
    blocks = []
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            rows = [[cell.text.replace("|", "\\|").replace("\n", "<br>") for cell in row.cells]
                    for row in block.rows]
            if rows:
                rows.insert(1, ["---"] * len(rows[0]))
                blocks.append("\n".join("| " + " | ".join(row) + " |" for row in rows))
        else:
            style = block.style.name if block.style else ""
            level = 1 if style == "Title" else 0
            if style.startswith("Heading ") and style[8:].isdigit():
                level = min(6, int(style[8:]))
            blocks.append(("#" * level + " " if level else "") + block.text)
    return _publish(ws, output, ".md", ("\n\n".join(blocks) + "\n").encode())


def document_pdf_pages(ws, path, first=1, last=None):
    """Extract selectable text from an inclusive one-based range of up to ten PDF pages."""
    pdf = PdfReader(_source(ws, path, ".pdf"))
    if pdf.is_encrypted:
        raise ValueError("encrypted PDF")
    last = first if last is None else last
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(pdf.pages) or last-first >= 10:
        raise ValueError("invalid PDF page range")
    return {"total_pages": len(pdf.pages), "pages": [
        {"page": i, "text": pdf.pages[i-1].extract_text() or ""} for i in range(first, last+1)]}


def document_replace_text(ws, path, old, new, output):
    """Replace literal text in Word body paragraph runs and save a copy with their formatting."""
    if not old:
        raise ValueError("old must be nonempty")
    doc = _docx(ws, path)
    count = 0
    for paragraph in doc.paragraphs:
        runs = paragraph.runs
        # Keep non-text runs (including drawings, fields and links) intact.
        if any(any(child.tag.rsplit("}", 1)[-1] not in {"rPr", "t"} for child in run._r) for run in runs):
            continue
        original = "".join(run.text for run in runs)
        if original != paragraph.text or old not in original:
            continue
        spans = [m.span() for m in re.finditer(re.escape(old), original)]
        # Work backwards so offsets in untouched prefixes remain valid.
        offsets = []
        pos = 0
        for run in runs:
            offsets.append((pos, pos + len(run.text)))
            pos += len(run.text)
        for start, end in reversed(spans):
            for run, (a,b) in zip(runs, offsets):
                if a == b or a >= end or b <= start:
                    continue
                left = run.text[:max(0,start-a)]
                right = run.text[max(0,end-a):]
                run.text = left + (new if a <= start < b else "") + right
            count += 1
    if count == 0:
        raise ValueError("no matching plain paragraph text")
    return {**_save(ws, output, doc), "replacements":count}
