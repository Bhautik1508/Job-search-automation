"""
Phase R4 — CSV parser unit tests.

Covers LinkedIn export and Happenstance/generic format, alias matching,
the LinkedIn Notes preamble, and rows that should be dropped with warnings.
"""

from __future__ import annotations

from backend.connections.csv_parser import parse_csv


class TestParseCsvLinkedinExport:
    def test_parses_linkedin_export_with_notes_preamble(self):
        csv_text = """Notes:
"When exporting your data..."
"You can find more info..."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Bob,Peer,https://linkedin.com/in/bobpeer,bob@x.com,Razorpay,Senior PM,01 Jan 2024
Alice,Smith,https://linkedin.com/in/alicesmith,,Stripe,Product Lead,02 Feb 2024
"""
        rows, warnings = parse_csv(csv_text)
        assert len(rows) == 2
        assert warnings == []
        assert rows[0].name == "Bob Peer"
        assert rows[0].company == "Razorpay"
        assert rows[0].current_title == "Senior PM"
        assert rows[0].linkedin_url == "https://linkedin.com/in/bobpeer"
        assert rows[1].name == "Alice Smith"
        assert rows[1].company == "Stripe"

    def test_skips_blank_and_hash_lines_before_header(self):
        csv_text = """
# comment line
# another

name,company,current_title,linkedin_url
Bob Peer,Razorpay,PM,https://linkedin.com/in/bobpeer
"""
        rows, _ = parse_csv(csv_text)
        assert len(rows) == 1
        assert rows[0].name == "Bob Peer"


class TestParseCsvGenericFormat:
    def test_parses_happenstance_format(self):
        csv_text = """name,company,current_title,linkedin_url
Bob Peer,Razorpay,Senior PM,https://linkedin.com/in/bobpeer
"""
        rows, warnings = parse_csv(csv_text)
        assert warnings == []
        assert rows[0].name == "Bob Peer"
        assert rows[0].current_title == "Senior PM"

    def test_picks_alternative_column_aliases(self):
        # "Full Name" + "Organization" + "Headline" + "Profile URL"
        csv_text = """Full Name,Organization,Headline,Profile URL
Bob Peer,Razorpay,Building payments,https://linkedin.com/in/bobpeer
"""
        rows, _ = parse_csv(csv_text)
        assert len(rows) == 1
        r = rows[0]
        assert r.name == "Bob Peer"
        assert r.company == "Razorpay"
        assert r.current_title == "Building payments"
        assert r.linkedin_url == "https://linkedin.com/in/bobpeer"

    def test_imports_row_without_url(self):
        # URL-less rows still import — fuzzy company match is what matters.
        csv_text = "name,company\nBob Peer,Razorpay\n"
        rows, warnings = parse_csv(csv_text)
        assert len(rows) == 1
        assert rows[0].linkedin_url is None
        assert warnings == []


class TestParseCsvDroppedRows:
    def test_warns_on_missing_company(self):
        csv_text = "name,company\nBob Peer,Razorpay\nAlice Smith,\n"
        rows, warnings = parse_csv(csv_text)
        assert len(rows) == 1
        assert any("missing name or company" in w for w in warnings)

    def test_warns_on_missing_name(self):
        csv_text = "First Name,Last Name,Company\n,,Razorpay\n"
        rows, warnings = parse_csv(csv_text)
        assert rows == []
        assert any("missing name or company" in w for w in warnings)

    def test_empty_input(self):
        rows, warnings = parse_csv("")
        assert rows == []
        assert warnings == ["Empty CSV"]

    def test_no_header_no_comma_lines(self):
        # No comma anywhere means no parseable header — should return
        # empty rows without crashing.
        rows, _ = parse_csv("just a single word\n")
        assert rows == []


class TestParseCsvCaseInsensitive:
    def test_column_names_are_case_insensitive(self):
        csv_text = "NAME,COMPANY,POSITION\nBob Peer,Razorpay,PM\n"
        rows, _ = parse_csv(csv_text)
        assert len(rows) == 1
        assert rows[0].current_title == "PM"
