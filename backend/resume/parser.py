"""
Resume parser — extracts clean text from a PDF resume using pdfplumber.

Usage:
    from backend.resume.parser import ResumeParser

    parser = ResumeParser("path/to/resume.pdf")
    text = parser.extract_text()
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber


class ResumeParser:
    """Parse a PDF resume and extract its text content."""

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"Resume not found: {self.pdf_path}")
        if self.pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Expected a PDF file, got: {self.pdf_path.suffix}")

    def extract_text(self) -> str:
        """
        Extract all text from the PDF, page by page.

        Returns a single cleaned string with page breaks removed
        and excess whitespace collapsed.
        """
        pages_text: list[str] = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text.strip())

        full_text = "\n\n".join(pages_text)
        return _clean_text(full_text)

    def extract_text_by_page(self) -> list[str]:
        """Extract text page-by-page, returning a list of strings."""
        pages: list[str] = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                pages.append(_clean_text(text) if text else "")

        return pages

    def page_count(self) -> int:
        """Return the number of pages in the PDF."""
        with pdfplumber.open(self.pdf_path) as pdf:
            return len(pdf.pages)


def _clean_text(text: str) -> str:
    """
    Minimal cleaning:
      - Strip leading/trailing whitespace
      - Collapse runs of 3+ newlines into 2
      - Remove null bytes
    """
    if not text:
        return ""
    text = text.replace("\x00", "")
    # Collapse excessive blank lines
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
