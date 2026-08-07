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


def build_verification_email(*, code: str, expires_minutes: int) -> tuple[str, str]:
    """Build ``(subject, html)`` for a registration verification code.

    The code is in the subject too: many mail clients preview only the subject,
    and the whole point is getting the digits in front of the user fast.
    """
    subject = f"{code} is your PH Stock Advisor verification code"
    body = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;color:#1f2933;">
  <h2 style="margin:0 0 8px;">Confirm your email</h2>
  <p style="margin:0 0 16px;">
    Enter this code to finish creating your PH Stock Advisor account:
  </p>
  <p style="font-size:32px;letter-spacing:8px;font-weight:bold;margin:0 0 16px;
            background:#f3f4f6;border-radius:8px;padding:14px 18px;text-align:center;">
    {_html.escape(code)}
  </p>
  <p style="margin:0 0 4px;color:#6b7280;">
    The code expires in {expires_minutes} minutes.
  </p>
  <p style="margin:16px 0 0;font-size:12px;color:#6b7280;">
    If you didn't try to create an account, you can ignore this email —
    nothing happens without the code.
  </p>
</div>
"""
    return subject, body


def build_report_email(
    *,
    symbol: str,
    verdict: str,
    score: int | None,
    summary: str,
    report_url: str,
) -> tuple[str, str]:
    """Build ``(subject, html)`` for a completed-analysis notification.

    Kept deliberately plain-HTML (inline styles, no images) so it renders the
    same in every mail client; the full styled report lives behind the link.
    """
    score_text = f" · score {score}/100" if score is not None else ""
    subject = f"{symbol} analysis ready — {verdict}{score_text}"

    verdict_color = "#0e7c6b" if verdict == "BUY" else "#b3401e"
    paragraphs = "".join(
        f'<p style="margin:0 0 12px;">{_html.escape(p.strip())}</p>' for p in summary.split("\n\n") if p.strip()
    )
    body = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;color:#1f2933;">
  <h2 style="margin:0 0 4px;">{_html.escape(symbol)}</h2>
  <p style="margin:0 0 16px;font-size:18px;">
    Verdict: <strong style="color:{verdict_color};">{_html.escape(verdict)}</strong>{_html.escape(score_text)}
  </p>
  {paragraphs}
  <p style="margin:20px 0 0;">
    <a href="{_html.escape(report_url, quote=True)}"
       style="background:#e8630a;color:#ffffff;padding:10px 18px;border-radius:8px;
              text-decoration:none;display:inline-block;">
      View the full report
    </a>
  </p>
  <p style="margin:24px 0 0;font-size:12px;color:#6b7280;">
    PH Stock Advisor AI — this is an automated analysis, not financial advice.
  </p>
</div>
"""
    return subject, body
