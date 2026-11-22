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

    @pytest.mark.parametrize(
        "html",
        [
            EMPTY_TABLE_HTML,
            "<table><thead></thead></table>",
        ],
        ids=["empty-tbody", "no-tbody"],
    )
    def test_returns_empty_for_edge_cases(self, html):
        assert _parse_dividend_rows(html) == []


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


# ---------------------------------------------------------------------------
# PSE EDGE dividend disclosure scraper tests (pse_edge_dividends.py)
# ---------------------------------------------------------------------------

SAMPLE_DISCLOSURE_SEARCH_HTML = """
<table class="list">
<tbody>
  <tr>
    <td><a href="#viewer" onclick="openPopup('edge_1');return false;">Declaration of Cash Dividends</a></td>
    <td class="alignC">Nov 12, 2026 02:10 PM</td>
    <td class="alignC">REIT-6-A</td>
    <td class="alignC">C08164-2026</td>
  </tr>
</tbody>
</table>
"""

SAMPLE_FORM_6_1_HTML = """
<html><body>
  <div>TEL PSE Disclosure Form 6-1 - Declaration of Cash Dividends</div>
  <div>Amount of Cash Dividend Per Share : P46.00</div>
  <div>Ex-Date : Aug 27, 2026</div>
  <div>Record Date Aug 28, 2026</div>
  <div>Payment Date Sep 11, 2026</div>
</body></html>
"""

SAMPLE_FORM_REIT_6_A_HTML = """
<html><body>
  <div>AREIT, Inc. AREIT PSE Disclosure Form REIT-6-A - Declaration of Cash Dividends</div>
  <div>Amount of Cash Dividend Per Share Php0.679</div>
  <div>Ex-Date : Nov 25, 2026</div>
  <div>Record Date Nov 26, 2026</div>
  <div>Payment Date Dec 11, 2026</div>
</body></html>
"""


class TestPseEdgeDividends:
    def test_parse_disclosure_html_supports_form_6_1_and_reit_6_a(self):
        from ph_stocks_advisor.data.clients.pse_edge_dividends import (
            _parse_disclosure_html,
        )

        tel = _parse_disclosure_html(SAMPLE_FORM_6_1_HTML)
        assert tel is not None
        assert tel.symbol == "TEL"
        assert tel.amount_per_share == "P46.00"
        assert tel.ex_date == "Aug 27, 2026"
        assert tel.record_date == "Aug 28, 2026"
        assert tel.payment_date == "Sep 11, 2026"

        areit = _parse_disclosure_html(SAMPLE_FORM_REIT_6_A_HTML)
        assert areit is not None
        assert areit.symbol == "AREIT"
        assert areit.amount_per_share == "Php0.679"
        assert areit.ex_date == "Nov 25, 2026"
        assert areit.record_date == "Nov 26, 2026"
        assert areit.payment_date == "Dec 11, 2026"

    @patch("ph_stocks_advisor.data.clients.pse_edge_dividends._fetch_disclosure_content")
    @patch("ph_stocks_advisor.data.clients.pse_edge_dividends.requests.post")
    @patch("ph_stocks_advisor.data.clients.pse_edge_dividends._resolve_cmpy_id", return_value="679")
    def test_fetch_recent_dividend_declarations_filters_by_cmpy_id(self, mock_resolve, mock_post, mock_fetch_content):
        from ph_stocks_advisor.data.clients.pse_edge_dividends import (
            fetch_recent_dividend_declarations,
        )

        mock_post.return_value = MagicMock(status_code=200, text=SAMPLE_DISCLOSURE_SEARCH_HTML)
        mock_fetch_content.return_value = SAMPLE_FORM_REIT_6_A_HTML

        results = fetch_recent_dividend_declarations("AREIT.PS")

        mock_resolve.assert_called_once_with("AREIT")
        assert mock_post.call_args.kwargs["data"]["keyword"] == "679"
        assert len(results) == 1
        assert results[0].symbol == "AREIT"
        assert results[0].amount_per_share == "Php0.679"
        assert results[0].ex_date == "Nov 25, 2026"
        assert results[0].announce_date == "Nov 12, 2026"

    @patch("ph_stocks_advisor.data.clients.pse_edge_dividends.requests.post")
    @patch("ph_stocks_advisor.data.clients.pse_edge_dividends._resolve_cmpy_id", return_value=None)
    def test_fetch_recent_dividend_declarations_skips_when_cmpy_id_unresolved(self, _mock_resolve, mock_post):
        from ph_stocks_advisor.data.clients.pse_edge_dividends import (
            fetch_recent_dividend_declarations,
        )

        assert fetch_recent_dividend_declarations("UNKNOWN") == []
        mock_post.assert_not_called()

    @patch("ph_stocks_advisor.data.services.dividend.fetch_recent_dividend_declarations")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_company_dividend_announcements")
    @patch("ph_stocks_advisor.data.services.dividend.fetch_annual_cashflow_trends", return_value={"fcf": {}})
    @patch(
        "ph_stocks_advisor.data.services.dividend.fetch_annual_income_trends",
        return_value={"net_income": {}, "revenue": {}},
    )
    @patch(
        "ph_stocks_advisor.data.services.dividend.fetch_stock_profile",
        return_value={"price": 37.25, "dividendYield": 5.5, "sharesOutstanding": 1_000_000, "isREIT": True},
    )
    def test_company_dividends_table_is_primary_and_skips_popup_scrape(self, _prof, _inc, _cf, mock_ann, mock_decl):
        from ph_stocks_advisor.data.services.dividend import fetch_dividend_info

        mock_ann.return_value = [
            DividendAnnouncement(
                dividend_rate="Php0.679",
                ex_date="Nov 25, 2026",
                record_date="Nov 26, 2026",
                payment_date="Dec 11, 2026",
            )
        ]

        info = fetch_dividend_info("AREIT")

        assert len(info.dividend_announcements) == 1
        assert info.ex_dividend_date == "Nov 25, 2026"
        assert "Php0.679/share" in info.recent_declared_dividends
        mock_decl.assert_not_called()

