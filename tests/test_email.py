"""
Tests for analysis-result email delivery.

Verifies the sender selection (ZeptoMail when a key is set, console otherwise),
the ZeptoMail HTTP contract (payload shape, loud failures), the report email
builder (subject, escaping, report link), and the best-effort hook in the
``analyse_stock`` task path (never fails the task, skips non-email users).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
import requests

from ph_stocks_advisor.infra.email import (
    ZEPTOMAIL_URL,
    ConsoleEmailSender,
    EmailSendError,
    ZeptoMailSender,
    build_email_sender,
    build_report_email,
)
from ph_stocks_advisor.web.tasks import _email_report

# ---------------------------------------------------------------------------
# Sender selection
# ---------------------------------------------------------------------------


def test_no_api_key_builds_console_sender() -> None:
    assert isinstance(build_email_sender(None, "from@example.com"), ConsoleEmailSender)
    assert isinstance(build_email_sender("", "from@example.com"), ConsoleEmailSender)


def test_api_key_builds_zeptomail_sender() -> None:
    assert isinstance(build_email_sender("key", "from@example.com"), ZeptoMailSender)


def test_console_sender_logs_instead_of_sending(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="ph_stocks_advisor.infra.email"):
        ConsoleEmailSender().send(to="u@example.com", subject="Hi", html="<b>x</b>")
    assert "u@example.com" in caplog.text
    assert "console mode" in caplog.text


# ---------------------------------------------------------------------------
# ZeptoMail HTTP contract
# ---------------------------------------------------------------------------


def _fake_session(status_code: int, text: str = "") -> MagicMock:
    session = MagicMock(spec=requests.Session)
    session.post.return_value = MagicMock(status_code=status_code, text=text)
    return session


def test_zeptomail_posts_expected_payload() -> None:
    session = _fake_session(201)
    sender = ZeptoMailSender("secret-key", "from@example.com", http=session)
    sender.send(to="u@example.com", subject="TEL analysis", html="<p>hi</p>")

    (url,), kwargs = session.post.call_args
    assert url == ZEPTOMAIL_URL
    assert kwargs["json"] == {
        "from": {"address": "from@example.com"},
        "to": [{"email_address": {"address": "u@example.com"}}],
        "subject": "TEL analysis",
        "htmlbody": "<p>hi</p>",
    }
    assert kwargs["headers"]["authorization"] == "Zoho-enczapikey secret-key"


def test_zeptomail_error_status_raises_with_from_address() -> None:
    sender = ZeptoMailSender("k", "from@example.com", http=_fake_session(500, ""))
    with pytest.raises(EmailSendError, match=r"from='from@example\.com'.*<empty body>"):
        sender.send(to="u@example.com", subject="s", html="h")


def test_zeptomail_network_failure_raises_email_send_error() -> None:
    session = MagicMock(spec=requests.Session)
    session.post.side_effect = requests.ConnectionError("boom")
    sender = ZeptoMailSender("k", "from@example.com", http=session)
    with pytest.raises(EmailSendError, match="unreachable"):
        sender.send(to="u@example.com", subject="s", html="h")


# ---------------------------------------------------------------------------
# Report email builder
# ---------------------------------------------------------------------------


def test_report_email_subject_carries_verdict_and_score() -> None:
    subject, html = build_report_email(
        symbol="TEL",
        verdict="BUY",
        score=72,
        summary="Solid dividends.\n\nCheap valuation.",
        report_url="https://app.example.com/report/TEL",
    )
    assert subject == "TEL analysis ready — BUY · score 72/100"
    assert "https://app.example.com/report/TEL" in html
    assert "<p" in html and "Solid dividends." in html and "Cheap valuation." in html


def test_report_email_without_score_omits_score() -> None:
    subject, _ = build_report_email(
        symbol="TEL", verdict="NOT BUY", score=None, summary="", report_url="u"
    )
    assert subject == "TEL analysis ready — NOT BUY"


def test_report_email_escapes_html_in_summary() -> None:
    _, html = build_report_email(
        symbol="TEL",
        verdict="BUY",
        score=60,
        summary="<script>alert(1)</script>",
        report_url="u",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Task hook (best-effort semantics)
# ---------------------------------------------------------------------------


def test_email_report_skips_users_without_an_address(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = MagicMock()
    monkeypatch.setattr("ph_stocks_advisor.infra.config.get_email_sender", factory)
    _email_report("TEL", "BUY", 70, "summary", "anonymous")
    factory.assert_not_called()


def test_email_report_sends_to_the_requesting_user(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = MagicMock()
    monkeypatch.setattr(
        "ph_stocks_advisor.infra.config.get_email_sender", lambda *a, **k: sender
    )
    _email_report("TEL", "BUY", 70, "summary", "u@example.com")
    kwargs = sender.send.call_args.kwargs
    assert kwargs["to"] == "u@example.com"
    assert "TEL" in kwargs["subject"]
    assert "/report/TEL" in kwargs["html"]


def test_email_report_never_raises(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    sender = MagicMock()
    sender.send.side_effect = EmailSendError("provider down")
    monkeypatch.setattr(
        "ph_stocks_advisor.infra.config.get_email_sender", lambda *a, **k: sender
    )
    with caplog.at_level(logging.ERROR, logger="ph_stocks_advisor.web.tasks"):
        _email_report("TEL", "BUY", 70, "summary", "u@example.com")  # must not raise
    assert "Failed to email" in caplog.text
