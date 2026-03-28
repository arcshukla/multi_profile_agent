"""
file_utils.py
-------------
Read document files into plain text. Supports PDF, TXT, CSV, DOCX, MD.

This is the ONLY place file parsing happens. Add new format support here.
"""

from pathlib import Path
from app.core.logging_config import get_logger

logger = get_logger(__name__)

def read_document(path: str | Path) -> str:
    """
    Read a document file and return its text content.

    Supports: .pdf, .txt, .md, .csv, .docx, .doc
    Raises: FileNotFoundError, ValueError for unsupported types
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    ext = path.suffix.lower()

    if ext == ".pdf":
        return _read_pdf(path)
    elif ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    elif ext == ".csv":
        return _read_csv(path)
    elif ext in (".docx", ".doc"):
        return _read_docx(path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _read_pdf(path: Path) -> str:
    try:
        import pymupdf  # PyMuPDF
        doc = pymupdf.open(str(path))
        pages = [page.get_text() for page in doc]
        doc.close()
        text = "\n\n".join(pages)
        logger.debug("PDF read: %s | %d pages | %d chars", path.name, len(pages), len(text))
        return text
    except ImportError as e:
        logger.warning("Failed to import pymupdf: %s", e)

    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n\n".join(pages)
        logger.debug("PDF read (pypdf): %s | %d pages", path.name, len(pages))
        return text
    except ImportError as e:
        logger.warning("Failed to import pypdf: %s", e)
        raise ImportError("Install pymupdf or pypdf: pip install pymupdf")


def _read_csv(path: Path) -> str:
    import csv
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(", ".join(row))
    return "\n".join(rows)


def _read_docx(path: Path) -> str:
    try:
        from docx import Document
        doc = Document(str(path))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        raise ImportError("Install python-docx: pip install python-docx")


def read_text_file(path: str | Path, default: str = "") -> str:
    """Read a plain text file, return default if missing."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.warning("Could not read %s: %s", path, e)
        return default
