"""
Tests for the PSE EDGE company dividends page scraper and the
DividendAnnouncement model.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ph_stocks_advisor.data.clients.pse_edge_company_dividends import (
    _parse_dividend_rows,
    fetch_company_dividend_announcements,
)
from ph_stocks_advisor.data.models import DividendAnnouncement, DividendInfo


# ---------------------------------------------------------------------------
# Sample HTML fixtures (mirrors real PSE EDGE output)
# ---------------------------------------------------------------------------

SAMPLE_DIVIDEND_TABLE_HTML = """
<table class="list">
<caption>Dividend Information</caption>
<thead>
  <tr>
    <th>Type of Security</th>
    <th>Type of Dividend</th>
    <th>Dividend Rate</th>
    <th>Ex-Dividend Date</th>
    <th>Record Date</th>
    <th>Payment Date</th>
    <th>Circular Number</th>
  </tr>
</thead>
<tbody>
<tr>
    <td class="alignC">COMMON</td>
    <td class="alignC">Cash</td>
    <td class="alignR">Php0.62</td>
    <td class="alignC">Mar 04, 2026</td>
    <td class="alignC">Mar 5, 2026</td>
    <td class="alignC">Mar 20, 2026</td>
    <td class="alignC"><a href="#viewer" onclick="openPopup('abc123');return false;">C01040-2026</a></td>
</tr>
<tr>
    <td class="alignC">COMMON</td>
    <td class="alignC">Cash</td>
    <td class="alignR">Php0.62</td>
    <td class="alignC">Nov 25, 2025</td>
    <td class="alignC">Nov 26, 2025</td>
    <td class="alignC">Dec 12, 2025</td>
    <td class="alignC"><a href="#viewer" onclick="openPopup('def456');return false;">C07988-2025</a></td>
</tr>
<tr>
    <td class="alignC">COMMON</td>
    <td class="alignC">Cash</td>
    <td class="alignR">Php0.59</td>
    <td class="alignC">Aug 28, 2025</td>
    <td class="alignC">Aug 29, 2025</td>
    <td class="alignC">Sep 12, 2025</td>
    <td class="alignC"><a href="#viewer" onclick="openPopup('ghi789');return false;">C06014-2025</a></td>
</tr>
</tbody>
</table>
"""

EMPTY_TABLE_HTML = """
<table class="list">
<caption>Dividend Information</caption>
<thead>
  <tr><th>Type of Security</th></tr>
</thead>
<tbody>
</tbody>
</table>
"""


# ---------------------------------------------------------------------------
# DividendAnnouncement model tests
# ---------------------------------------------------------------------------


class TestDividendAnnouncement:
    def test_create_with_required_fields(self):
        ann = DividendAnnouncement(
            dividend_rate="Php0.62",
            ex_date="Mar 04, 2026",
            payment_date="Mar 20, 2026",
        )
        assert ann.dividend_rate == "Php0.62"
        assert ann.ex_date == "Mar 04, 2026"
        assert ann.payment_date == "Mar 20, 2026"
        assert ann.security_type == "COMMON"
        assert ann.dividend_type == "Cash"

    def test_to_summary(self):
        ann = DividendAnnouncement(
            dividend_rate="Php0.62",
            ex_date="Mar 04, 2026",
            payment_date="Mar 20, 2026",
        )
        summary = ann.to_summary()
        assert "Php0.62/share" in summary
        assert "ex-date Mar 04, 2026" in summary
        assert "payment Mar 20, 2026" in summary

    def test_announcements_serialize_round_trip(self):
        """Announcements serialize to JSON and can be restored via model_dump."""
        ann = DividendAnnouncement(
            security_type="COMMON",
            dividend_type="Cash",
            dividend_rate="Php0.58",
            ex_date="May 26, 2025",
            record_date="May 27, 2025",
            payment_date="Jun 11, 2025",
            circular_number="C03323-2025",
        )
        # Round-trip through model_dump
        data = ann.model_dump()
        restored = DividendAnnouncement(**data)
        assert restored == ann

        # Also verify JSON serialization with parent model
        info = DividendInfo(symbol="AREIT", dividend_announcements=[ann])
        json_str = info.model_dump_json()
        assert "Php0.58" in json_str
        assert "dividend_announcements" in json_str


# ---------------------------------------------------------------------------
# DividendInfo with announcements
# ---------------------------------------------------------------------------


class TestDividendInfoWithAnnouncements:
    def test_with_announcements(self):
        announcements = [
            DividendAnnouncement(
                dividend_rate="Php0.62",
                ex_date="Mar 04, 2026",
                payment_date="Mar 20, 2026",
            ),
            DividendAnnouncement(
                dividend_rate="Php0.59",
                ex_date="Aug 28, 2025",
                payment_date="Sep 12, 2025",
            ),
        ]
        info = DividendInfo(
            symbol="AREIT",
            dividend_announcements=announcements,
        )
        assert len(info.dividend_announcements) == 2
        assert info.dividend_announcements[0].dividend_rate == "Php0.62"
        assert info.dividend_announcements[1].ex_date == "Aug 28, 2025"


# ---------------------------------------------------------------------------
# HTML parser tests
# ---------------------------------------------------------------------------


class TestParseDividendRows:
    def test_parses_rows_with_correct_fields(self):
        """Verify all rows are parsed with correct field values."""
        rows = _parse_dividend_rows(SAMPLE_DIVIDEND_TABLE_HTML)
        assert len(rows) == 3

        # First row
        first = rows[0]
        assert first.security_type == "COMMON"
        assert first.dividend_type == "Cash"
        assert first.dividend_rate == "Php0.62"
        assert first.ex_date == "Mar 04, 2026"
        assert first.record_date == "Mar 5, 2026"
        assert first.payment_date == "Mar 20, 2026"
        assert first.circular_number == "C01040-2026"

        # Third row has different rate
        third = rows[2]
        assert third.dividend_rate == "Php0.59"
        assert third.ex_date == "Aug 28, 2025"

    def test_empty_tbody_returns_empty(self):
        rows = _parse_dividend_rows(EMPTY_TABLE_HTML)
        assert rows == []

    def test_no_tbody_returns_empty(self):
        rows = _parse_dividend_rows("<table><thead></thead></table>")
        assert rows == []


# ---------------------------------------------------------------------------
# Integration-style tests (HTTP mocked)
# ---------------------------------------------------------------------------


class TestFetchCompanyDividendAnnouncements:
    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends._resolve_cmpy_id")
    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends.requests.post")
    def test_fetches_and_parses_announcements(self, mock_post, mock_resolve):
        mock_resolve.return_value = "679"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_DIVIDEND_TABLE_HTML
        mock_post.return_value = mock_response

        results = fetch_company_dividend_announcements("AREIT")

        assert len(results) == 3
        assert results[0].dividend_rate == "Php0.62"
        assert results[0].ex_date == "Mar 04, 2026"
        assert results[0].payment_date == "Mar 20, 2026"
        mock_resolve.assert_called_once_with("AREIT")

    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends._resolve_cmpy_id")
    def test_returns_empty_when_cmpy_id_not_found(self, mock_resolve):
        mock_resolve.return_value = None
        results = fetch_company_dividend_announcements("UNKNOWN")
        assert results == []

    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends._resolve_cmpy_id")
    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends.requests.post")
    def test_returns_empty_on_http_error(self, mock_post, mock_resolve):
        mock_resolve.return_value = "679"
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        results = fetch_company_dividend_announcements("AREIT")
        assert results == []

    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends._resolve_cmpy_id")
    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends.requests.post")
    def test_respects_max_results(self, mock_post, mock_resolve):
        mock_resolve.return_value = "679"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_DIVIDEND_TABLE_HTML
        mock_post.return_value = mock_response

        results = fetch_company_dividend_announcements("AREIT", max_results=2)
        assert len(results) == 2

    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends._resolve_cmpy_id")
    @patch("ph_stocks_advisor.data.clients.pse_edge_company_dividends.requests.post")
    def test_strips_ps_suffix_from_symbol(self, mock_post, mock_resolve):
        mock_resolve.return_value = "679"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = SAMPLE_DIVIDEND_TABLE_HTML
        mock_post.return_value = mock_response

        fetch_company_dividend_announcements("AREIT.PS")
        mock_resolve.assert_called_once_with("AREIT")
