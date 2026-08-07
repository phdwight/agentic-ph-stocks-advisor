"""Analysis-result email delivery.

One protocol, two implementations: ZeptoMail (the operator already runs it) and a
console sender for development and tests. The API key arrives via environment only
and is passed in here — this module never reads the environment itself, so there is
exactly one place configuration comes from (``infra.config``).

Email is best-effort in the analysis flow's eyes: the report is already saved and
visible in the app by the time the mail goes out, so a send failure must never fail
the Celery task. The failure is still raised loudly to the caller (as
``EmailSendError``) — the caller decides to log-and-continue, not this module.
"""

from __future__ import annotations

import html as _html
import logging
from typing import Protocol

import requests

logger = logging.getLogger(__name__)

ZEPTOMAIL_URL = "https://api.zeptomail.com/v1.1/email"


class EmailSendError(RuntimeError):
    """The provider refused or could not be reached.

    Its own type so callers can tell "the mail provider is unhappy" from a
    genuine bug, and choose how loudly to react.
    """


class EmailSender(Protocol):
    def send(self, *, to: str, subject: str, html: str) -> None: ...


class ConsoleEmailSender:
    """Logs the email instead of sending it — the dev/test mode when no key is set."""

    def send(self, *, to: str, subject: str, html: str) -> None:
        logger.info("email (console mode) to=%s subject=%r body=%s", to, subject, html)


class ZeptoMailSender:
    """ZeptoMail transactional send: one HTTPS POST with a Zoho-enczapikey header."""

    def __init__(
        self,
        api_key: str,
        from_address: str,
        http: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._from = from_address
        self._http = http or requests.Session()
        self._timeout = timeout
        self._headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Zoho-enczapikey {api_key}",
        }

    def send(self, *, to: str, subject: str, html: str) -> None:
        payload = {
            "from": {"address": self._from},
            "to": [{"email_address": {"address": to}}],
            "subject": subject,
            "htmlbody": html,
        }
        try:
            response = self._http.post(ZEPTOMAIL_URL, json=payload, headers=self._headers, timeout=self._timeout)
        except requests.RequestException as exc:
            raise EmailSendError(f"ZeptoMail unreachable: {exc}") from exc
        if response.status_code >= 400:
            # The sender address is named because it is by far the most common cause,
            # and ZeptoMail often answers an unverified sender with a bare 500 and an
            # empty body — which says nothing at all unless the log says what was
            # attempted. ZeptoMail verifies exact domains, so a subdomain is not
            # covered by its verified parent.
            raise EmailSendError(
                f"ZeptoMail rejected the send ({response.status_code}) "
                f"from={self._from!r}: {response.text[:200] or '<empty body>'}"
            )
        # Logged because "did the email actually go out?" is the first question when
        # a user says they never got their report. The body is not logged, and the
        # API key never appears here.
        logger.info("email: sent to=%s subject=%r from=%s", to, subject, self._from)


def build_email_sender(api_key: str | None, from_address: str) -> EmailSender:
    if api_key:
        return ZeptoMailSender(api_key, from_address)
    return ConsoleEmailSender()


# ---------------------------------------------------------------------------
# Email bodies — Tala-styled, mirroring the in-app report page
#
# Same design tokens as static/style.css (bg #f2f2f3, ink #1d1f20, accent
# #e8792b, buy #3d8a7e, sell #d85e52), everything inline because mail clients
# strip <style> unpredictably. Layout uses simple centered divs — the report
# is a single column in the app too.
# ---------------------------------------------------------------------------

_INK = "#1d1f20"
_MUTED = "#5d5d60"
_BG = "#f2f2f3"
_CARD = "#fbfbfc"
_INSET = "#e9e9ea"
_DIVIDER = "#d6d6d8"
_ACCENT = "#e8792b"
_ACCENT_DARK = "#a44e18"
_BUY = "#3d8a7e"
_SELL = "#d85e52"
_WAIT = "#424244"

_FONT = "font-family:Arial,Helvetica,sans-serif;"
_KICKER = f"{_FONT}font-size:11px;letter-spacing:2px;text-transform:uppercase;color:{_MUTED};margin:0 0 6px;"


def _shell(inner: str) -> str:
    """Wrap card content in the app's grey ground + branded header/footer."""
    return f"""\
<div style="background:{_BG};padding:24px 12px;">
  <div style="max-width:600px;margin:0 auto;">
    <p style="{_FONT}font-size:13px;font-weight:bold;letter-spacing:3px;color:{_ACCENT_DARK};margin:0 0 12px;">
      &#8599; PH STOCKS ADVISOR
    </p>
    <div style="background:{_CARD};border:1px solid {_DIVIDER};border-radius:6px;padding:22px 24px;color:{_INK};">
{inner}
    </div>
    <p style="{_FONT}font-size:11px;color:{_MUTED};margin:14px 4px 0;line-height:1.5;">
      Educational use only — not financial advice. AI-generated analysis may
      contain errors. Always consult a licensed financial advisor before investing.
    </p>
  </div>
</div>
"""


def build_verification_email(*, code: str, expires_minutes: int) -> tuple[str, str]:
    """Build ``(subject, html)`` for a registration verification code.

    The code is in the subject too: many mail clients preview only the subject,
    and the whole point is getting the digits in front of the user fast.
    """
    subject = f"{code} is your PH Stock Advisor verification code"
    inner = f"""\
      <p style="{_KICKER}">Confirm your email</p>
      <h2 style="{_FONT}font-size:20px;margin:0 0 14px;">One step left</h2>
      <p style="{_FONT}font-size:14px;line-height:1.55;margin:0 0 16px;">
        Enter this code to finish creating your PH Stock Advisor account:
      </p>
      <p style="{_FONT}font-size:32px;letter-spacing:8px;font-weight:bold;margin:0 0 16px;
                background:#fdf1e7;color:{_ACCENT_DARK};border:1px solid #f5bd8f;
                border-radius:6px;padding:14px 18px;text-align:center;">
        {_html.escape(code)}
      </p>
      <p style="{_FONT}font-size:13px;color:{_MUTED};margin:0 0 4px;">
        The code expires in {expires_minutes} minutes.
      </p>
      <p style="{_FONT}font-size:12px;color:{_MUTED};margin:16px 0 0;line-height:1.5;">
        If you didn't try to create an account, you can ignore this email —
        nothing happens without the code.
      </p>"""
    return subject, _shell(inner)


def _band_and_color(verdict: str, score: int | None) -> tuple[str, str, int]:
    """Mirror the report page's verdict panel: ``(band label, color, meter %)``.

    With a score, the five-band label drives the display (the binary verdict is
    a compatibility artifact); legacy scoreless reports fall back to it.
    """
    from ph_stocks_advisor.data.models import score_band

    if score is not None:
        band = score_band(score)
        if band in ("BUY", "STRONG BUY"):
            return band, _BUY, score
        if band == "WAIT":
            return band, _WAIT, score
        return band, _SELL, score
    if verdict == "BUY":
        return verdict, _BUY, 80
    return verdict, _SELL, 18


def build_report_email(
    *,
    symbol: str,
    verdict: str,
    score: int | None,
    summary: str,
    report_url: str,
) -> tuple[str, str]:
    """Build ``(subject, html)`` for a completed-analysis notification.

    Formatted like the in-app report page: the Tala verdict panel (band word,
    score, avoid→buy meter) followed by the same sections the app renders,
    through the same ``parse_sections`` + ``_body_to_html`` pipeline.
    """
    from ph_stocks_advisor.export.formatter import parse_sections
    from ph_stocks_advisor.export.html import _body_to_html

    band, color, meter_pct = _band_and_color(verdict, score)
    score_text = f" · score {score}/100" if score is not None else ""
    subject = f"{symbol} analysis ready — {band}{score_text}"

    score_html = ""
    if score is not None:
        score_html = f"""\
        <td align="right" style="{_FONT}font-size:15px;color:{color};vertical-align:bottom;">
          <b style="font-size:26px;">{score}</b> <span style="color:{_MUTED};font-size:12px;">/ 100</span>
        </td>"""

    sections_html = []
    for title, body in parse_sections(summary or ""):
        if title.lower().startswith("verdict"):
            continue  # the verdict panel is the single place the verdict appears
        rendered = _body_to_html(body).replace("<p></p>", "")
        sections_html.append(f"""\
      <div style="border-top:1px solid {_DIVIDER};margin-top:18px;padding-top:14px;">
        <p style="{_KICKER}">{_html.escape(title)}</p>
        <div style="{_FONT}font-size:14px;line-height:1.6;">{rendered}</div>
      </div>""")

    inner = f"""\
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="{_FONT}"><h2 style="font-size:24px;margin:0;">{_html.escape(symbol)}</h2></td>
      </tr></table>
      <p style="{_KICKER}margin-top:14px;">Verdict</p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="{_FONT}font-size:28px;font-weight:bold;color:{color};">{_html.escape(band)}</td>
{score_html}
      </tr></table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px;">
        <tr><td style="background:{_INSET};border-radius:4px;height:8px;font-size:0;line-height:0;">
          <div style="width:{meter_pct}%;max-width:100%;background:{color};border-radius:4px;height:8px;">&nbsp;</div>
        </td></tr>
        <tr><td style="{_FONT}font-size:11px;color:{_MUTED};padding-top:4px;">
          Avoid <span style="float:right;">Buy</span>
        </td></tr>
      </table>
      <p style="{_FONT}font-size:12px;color:{_MUTED};margin:10px 0 0;line-height:1.5;">
        Consolidated from six specialist analyses of {_html.escape(symbol)}.
        Assumes a new position — not advice for an existing holding.
      </p>
{"".join(sections_html)}
      <p style="margin:22px 0 4px;">
        <a href="{_html.escape(report_url, quote=True)}"
           style="{_FONT}background:{_ACCENT};color:#ffffff;font-size:14px;padding:11px 20px;
                  border-radius:6px;text-decoration:none;display:inline-block;">
          View the full report
        </a>
      </p>"""
    return subject, _shell(inner)
