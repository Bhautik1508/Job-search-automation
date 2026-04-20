"""
Unit tests for the resume parser module.

Uses a real tiny PDF fixture created on the fly for testing.
"""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.resume.parser import (
    ResumeParser,
    _clean_text,
    load_resume_text,
    clear_resume_cache,
)


# ==================================================================
# Tests: _clean_text helper
# ==================================================================

class TestCleanText:
    def test_empty_string(self):
        assert _clean_text("") == ""

    def test_none(self):
        assert _clean_text(None) == ""

    def test_strips_whitespace(self):
        assert _clean_text("  hello  ") == "hello"

    def test_collapses_excessive_newlines(self):
        result = _clean_text("a\n\n\n\n\nb")
        assert result == "a\n\nb"

    def test_removes_null_bytes(self):
        result = _clean_text("hello\x00world")
        assert result == "helloworld"

    def test_preserves_double_newlines(self):
        result = _clean_text("a\n\nb")
        assert result == "a\n\nb"


# ==================================================================
# Tests: ResumeParser initialisation
# ==================================================================

class TestResumeParserInit:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            ResumeParser("/nonexistent/path/resume.pdf")

    def test_wrong_extension(self, tmp_path):
        txt_file = tmp_path / "resume.txt"
        txt_file.write_text("not a pdf")
        with pytest.raises(ValueError, match="Expected a PDF"):
            ResumeParser(txt_file)

    def test_accepts_string_path(self, tmp_path):
        # Create a minimal PDF-like file (won't parse, but init should work)
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")
        parser = ResumeParser(str(pdf_file))
        assert parser.pdf_path == pdf_file

    def test_accepts_path_object(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")
        parser = ResumeParser(pdf_file)
        assert parser.pdf_path == pdf_file


# ==================================================================
# Tests: ResumeParser with mocked pdfplumber
# ==================================================================

class TestResumeParserExtraction:
    """Test extraction with mocked pdfplumber to avoid needing a real PDF."""

    @patch("backend.resume.parser.pdfplumber")
    def test_extract_text_single_page(self, mock_plumber, tmp_path):
        pdf_file = tmp_path / "resume.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")

        # Mock pdfplumber
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "  John Doe\nProduct Manager\n5 years experience  "
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        parser = ResumeParser(pdf_file)
        text = parser.extract_text()

        assert "John Doe" in text
        assert "Product Manager" in text

    @patch("backend.resume.parser.pdfplumber")
    def test_extract_text_multi_page(self, mock_plumber, tmp_path):
        pdf_file = tmp_path / "resume.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")

        page1 = MagicMock()
        page1.extract_text.return_value = "Page 1 content"
        page2 = MagicMock()
        page2.extract_text.return_value = "Page 2 content"

        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        parser = ResumeParser(pdf_file)
        text = parser.extract_text()

        assert "Page 1 content" in text
        assert "Page 2 content" in text
        # Pages separated by double newline
        assert "\n\n" in text

    @patch("backend.resume.parser.pdfplumber")
    def test_extract_text_empty_page(self, mock_plumber, tmp_path):
        pdf_file = tmp_path / "resume.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")

        page1 = MagicMock()
        page1.extract_text.return_value = "Content"
        page2 = MagicMock()
        page2.extract_text.return_value = None  # Empty page

        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        parser = ResumeParser(pdf_file)
        text = parser.extract_text()

        assert text == "Content"

    @patch("backend.resume.parser.pdfplumber")
    def test_extract_text_by_page(self, mock_plumber, tmp_path):
        pdf_file = tmp_path / "resume.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")

        page1 = MagicMock()
        page1.extract_text.return_value = "Page 1"
        page2 = MagicMock()
        page2.extract_text.return_value = "Page 2"

        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        parser = ResumeParser(pdf_file)
        pages = parser.extract_text_by_page()

        assert len(pages) == 2
        assert pages[0] == "Page 1"
        assert pages[1] == "Page 2"

    @patch("backend.resume.parser.pdfplumber")
    def test_page_count(self, mock_plumber, tmp_path):
        pdf_file = tmp_path / "resume.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")

        mock_pdf = MagicMock()
        mock_pdf.pages = [MagicMock(), MagicMock(), MagicMock()]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        parser = ResumeParser(pdf_file)
        assert parser.page_count() == 3


# ==================================================================
# Tests: load_resume_text cache
# ==================================================================

class TestLoadResumeTextCache:
    def test_cache_hit_skips_extract(self, tmp_path):
        """Second call for an unchanged file must not re-invoke pdfplumber."""
        pdf_file = tmp_path / "resume.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")

        clear_resume_cache()
        with patch("backend.resume.parser.pdfplumber") as mock_plumber:
            page = MagicMock()
            page.extract_text.return_value = "Resume content"
            mock_pdf = MagicMock()
            mock_pdf.pages = [page]
            mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
            mock_pdf.__exit__ = MagicMock(return_value=False)
            mock_plumber.open.return_value = mock_pdf

            first = load_resume_text(pdf_file)
            second = load_resume_text(pdf_file)

            assert first == second == "Resume content"
            # One parse, not two.
            assert mock_plumber.open.call_count == 1

    def test_cache_invalidates_on_mtime_change(self, tmp_path):
        """Updating the PDF on disk forces a re-parse."""
        pdf_file = tmp_path / "resume.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 v1")

        clear_resume_cache()
        with patch("backend.resume.parser.pdfplumber") as mock_plumber:
            page = MagicMock()
            page.extract_text.side_effect = ["V1", "V2"]
            mock_pdf = MagicMock()
            mock_pdf.pages = [page]
            mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
            mock_pdf.__exit__ = MagicMock(return_value=False)
            mock_plumber.open.return_value = mock_pdf

            first = load_resume_text(pdf_file)

            # Force a different mtime.
            import time as _t
            _t.sleep(0.01)
            pdf_file.write_bytes(b"%PDF-1.4 v2-updated")

            second = load_resume_text(pdf_file)

            assert first == "V1"
            assert second == "V2"
            assert mock_plumber.open.call_count == 2
