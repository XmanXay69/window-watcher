"""
documents.py: getting reference documents ready for Claude.

Claude reads files with its Read tool, which only takes in about 25,000
tokens (roughly 1,000 lines of text) at a time. To handle big documents, we:

1. Turn each document into plain text, which Claude can search with its
   Grep tool (PDFs and Word files can't be searched as they are).
2. Tell Claude how big each one is, so for big ones it searches for the
   relevant part instead of trying to read the whole thing.

Documents can be up to MAX_DOCUMENT_MB. Even a 500-page manual works,
because Claude only reads the parts that match your question.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from Foundation import NSURL
from Quartz import PDFDocument

# Files macOS's built-in `textutil` command can turn into plain text.
TEXTUTIL_TYPES = {".doc", ".docx", ".rtf", ".rtfd", ".odt", ".wordml", ".webarchive"}

# The biggest file you can add, in megabytes.
MAX_DOCUMENT_MB = 500

# Claude Code can only open PDFs up to 20 MB itself. Bigger PDFs still work
# through the text we pull out of them.
CLAUDE_PDF_LIMIT_MB = 20

# Above this many tokens (about 4 characters each), Claude should search the
# document instead of reading all of it.
LARGE_DOCUMENT_TOKENS = 20_000


@dataclass
class Document:
    """One file Claude can use."""
    path: Path         # the copy in the app's private folder
    description: str   # e.g. "text, 5,000 lines, about 93,000 tokens (large: search it)"
    shown_name: str    # the name of the file you picked
    warning: str = ""  # something to tell you about this file, if anything


def _unique_path(folder, name):
    """folder/name, or folder/'name 2' etc. if that's already taken."""
    path = folder / name
    number = 2
    while path.exists():
        path = folder / f"{Path(name).stem} {number}{Path(name).suffix}"
        number += 1
    return path


def _describe_text(path, kind="text"):
    """Size info for a text file, so Claude knows whether to search it."""
    text = path.read_text(errors="replace")
    lines = text.count("\n") + 1
    tokens = len(text) // 4
    note = f"{kind}, {lines:,} lines, about {tokens:,} tokens"
    if tokens > LARGE_DOCUMENT_TOKENS:
        note += " (large: Grep it for keywords, then Read only the matching parts)"
    return note


def _pdf_to_text(pdf_path, text_path):
    """Pull the text out of a PDF with Apple's PDFKit. Returns the page count."""
    pdf = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(pdf_path)))
    if pdf is None:
        raise ValueError("macOS couldn't open this PDF.")
    pages = []
    for number in range(pdf.pageCount()):
        page_text = pdf.pageAtIndex_(number).string() or ""
        pages.append(f"--- Page {number + 1} ---\n{page_text}")
    text_path.write_text("\n\n".join(pages))
    words = sum(len(page.split()) for page in pages)
    return pdf.pageCount(), words


def prepare_document(source, docs_folder):
    """
    Copy one file you picked into docs_folder, converting it if needed.
    Returns a list of Documents (a PDF gives two: the original and its text).
    Raises ValueError with a friendly message if the file can't be used.
    """
    source = Path(source)
    suffix = source.suffix.lower()
    name = source.name

    size_mb = source.stat().st_size / 1_000_000
    if size_mb > MAX_DOCUMENT_MB:
        raise ValueError(f"it's {size_mb:,.0f} MB, and the limit is {MAX_DOCUMENT_MB} MB.")

    if suffix == ".pdf":
        pdf_copy = _unique_path(docs_folder, name)
        shutil.copyfile(source, pdf_copy)
        text_copy = _unique_path(docs_folder, name + ".txt")
        pages, words = _pdf_to_text(pdf_copy, text_copy)
        result = [Document(text_copy, _describe_text(text_copy, f"text of the PDF {name}"), name)]

        if size_mb <= CLAUDE_PDF_LIMIT_MB:
            # Small enough for Claude to open itself, e.g. to look at diagrams.
            result.append(Document(pdf_copy, f"original PDF, {pages:,} pages (Read it with the pages "
                                             "option only if you need its pictures or layout)", name))
        else:
            pdf_copy.unlink()  # too big for Claude to open, so the text is all it needs

        if words < pages * 10:
            # Barely any text: probably scanned pages (pictures of text).
            result[0].warning = (f"{name} looks like scanned pages, so it has almost no text to search."
                                 + (" Claude can still look at its pages." if size_mb <= CLAUDE_PDF_LIMIT_MB
                                    else " It's also too big for Claude to look at the pages."))
        return result

    if suffix in TEXTUTIL_TYPES:
        text_copy = _unique_path(docs_folder, name + ".txt")
        result = subprocess.run(["textutil", "-convert", "txt", "-output", str(text_copy), str(source)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"macOS couldn't convert {name} to text.")
        return [Document(text_copy, _describe_text(text_copy, f"text of {name}"), name)]

    copy = _unique_path(docs_folder, name)
    shutil.copyfile(source, copy)
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return [Document(copy, "image", name)]
    return [Document(copy, _describe_text(copy), name)]
