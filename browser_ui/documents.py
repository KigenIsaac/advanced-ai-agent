"""Extract readable chat context from an in-memory upload."""

from io import BytesIO
from zipfile import ZipFile


def extract_document(extension, data):
    if extension in {".md", ".txt", ".csv", ".json"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Save the document as UTF-8 text and try again.") from exc
    elif extension in {".docx", ".pdf"}:
        stream = BytesIO(data)
        try:
            if extension == ".docx":
                from docx import Document

                with ZipFile(stream) as archive:
                    if sum(item.file_size for item in archive.infolist()) > 16 * 1024**2:
                        raise ValueError
                document = Document(stream)
                lines = [p.text for p in document.paragraphs]
                for table in document.tables:
                    lines.extend("\t".join(c.text for c in r.cells) for r in table.rows)
                text = "\n".join(lines)
            else:
                from pypdf import PdfReader

                reader = PdfReader(stream)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise ValueError("Could not read this document. Check its format and password protection.") from exc
    else:
        raise ValueError("Choose a PDF, DOCX, TXT, MD, CSV or JSON file.")
    text = text.strip()
    if not text or "\x00" in text:
        raise ValueError("No readable text found. Scanned documents need OCR first.")
    if len(text) > 32000:
        raise ValueError("Choose a shorter document (32,000 characters maximum).")
    return text
