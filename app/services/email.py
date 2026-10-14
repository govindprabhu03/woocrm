"""Real email delivery (Strategy implementation of workflows.EmailSender) via the SendGrid v3 API."""
import logging

import httpx

from app.config import Settings
from app.services.workflows import EmailSender, RecordingSender

log = logging.getLogger("woocrm.email")


class SendGridSender:
    URL = "https://api.sendgrid.com/v3/mail/send"

    def __init__(self, api_key: str, from_email: str, from_name: str = "WooCRM", sandbox: bool = False,
                 footer: str = "You are receiving this because you are a customer. Reply STOP to unsubscribe.",
                 http: httpx.Client | None = None):
        self._key, self._from, self._name = api_key, from_email, from_name
        self._sandbox, self._footer = sandbox, footer
        self._http = http or httpx.Client(timeout=10)

    def send(self, to: str, subject: str, body: str) -> bool:
        """True only if SendGrid accepted the message. Never raises: failures become status='failed' in the log."""
        if "@" not in to or "\n" in to or "\r" in to:
            log.warning("refusing to send: invalid recipient")
            return False
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self._from, "name": self._name},
            "subject": " ".join(subject.split())[:200] or "(no subject)",  # collapses newlines: no header injection
            "content": [{"type": "text/plain", "value": f"{body}\n\n--\n{self._footer}"}],
        }
        if self._sandbox:  # SendGrid validates the request but delivers nothing
            payload["mail_settings"] = {"sandbox_mode": {"enable": True}}
        try:
            r = self._http.post(self.URL, json=payload, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as e:
            log.error("sendgrid network error: %s", type(e).__name__)
            return False
        if r.status_code in (200, 202):
            return True
        log.error("sendgrid rejected message: HTTP %s %s", r.status_code, r.text[:200])
        return False


def build_sender(settings: Settings) -> EmailSender:
    """Real delivery when SENDGRID_API_KEY + MAIL_FROM are set; otherwise the recording sender (log only)."""
    if settings.sendgrid_api_key and settings.mail_from:
        log.info("Email: SendGrid%s from %s", " (SANDBOX)" if settings.sendgrid_sandbox else "", settings.mail_from)
        return SendGridSender(settings.sendgrid_api_key, settings.mail_from, sandbox=settings.sendgrid_sandbox)
    return RecordingSender()
