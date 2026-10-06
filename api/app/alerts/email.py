"""Email delivery: Resend (HTTPS API) or any SMTP server; in development, the Mailpit catcher
that docker-compose runs (web UI on :8025) when neither is configured.

Messages carry an HTML part with inline images (the mini chart, referenced as `cid:…`) and a
plain-text part. The keys stay server-side: they are read from `.env` by the backend only.
"""

import asyncio
import base64
import smtplib
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, ClassVar

import httpx

from app.alerts.engine import EmailRoute
from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

RESEND_URL = "https://api.resend.com/emails"
CATCHER_PORT = 1025
CATCHER_FROM = "Breakout <alerts@breakout.local>"


class EmailError(RuntimeError):
    pass


@dataclass(frozen=True)
class InlineImage:
    cid: str  # referenced in the HTML as src="cid:<cid>"
    filename: str
    content: bytes
    subtype: str = "png"


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    html: str
    text: str
    images: tuple[InlineImage, ...] = field(default_factory=tuple)


class EmailSender(ABC):
    name: ClassVar[str]

    @abstractmethod
    async def send(self, message: Message) -> None: ...

    async def aclose(self) -> None:
        return None


class ResendSender(EmailSender):
    name: ClassVar[str] = "resend"

    def __init__(
        self,
        api_key: str,
        sender: str,
        *,
        url: str = RESEND_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._sender = sender
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=15)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def send(self, message: Message) -> None:
        body: dict[str, Any] = {
            "from": self._sender,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        if message.images:
            body["attachments"] = [
                {
                    "filename": image.filename,
                    "content": base64.b64encode(image.content).decode(),
                    "content_id": image.cid,
                }
                for image in message.images
            ]
        try:
            response = await self._client.post(self._url, json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            raise EmailError(f"Resend unreachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", response.text)
            except ValueError:
                detail = response.text
            raise EmailError(f"Resend refused the email (HTTP {response.status_code}): {detail}")

    async def aclose(self) -> None:
        await self._client.aclose()


def build_mime(message: Message, sender: str) -> EmailMessage:
    mime = EmailMessage()
    mime["From"] = sender
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime.set_content(message.text)
    html = message.html
    cids = {image.cid: make_msgid(image.cid) for image in message.images}
    for cid, msgid in cids.items():
        html = html.replace(f"cid:{cid}", f"cid:{msgid[1:-1]}")
    mime.add_alternative(html, subtype="html")
    html_part = mime.get_body(("html",))
    assert html_part is not None
    for image in message.images:
        html_part.add_related(
            image.content, "image", image.subtype, cid=cids[image.cid], filename=image.filename
        )
    return mime


class SmtpSender(EmailSender):
    name: ClassVar[str] = "smtp"

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        *,
        security: str = "starttls",
        username: str | None = None,
        password: str | None = None,
        timeout: float = 15,
    ) -> None:
        self._host, self._port, self._sender = host, port, sender
        self._security, self._username, self._password = security, username, password
        self._timeout = timeout

    def _send_sync(self, message: Message) -> None:
        mime = build_mime(message, self._sender)
        context = ssl.create_default_context()
        smtp: smtplib.SMTP
        if self._security == "ssl":
            smtp = smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=context)
        else:
            smtp = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        with smtp:
            if self._security == "starttls":
                smtp.starttls(context=context)
            if self._username:
                smtp.login(self._username, self._password or "")
            smtp.send_message(mime)

    async def send(self, message: Message) -> None:
        try:
            await asyncio.to_thread(self._send_sync, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailError(f"SMTP {self._host}:{self._port} failed: {exc}") from exc


def email_route(config: Settings) -> EmailRoute:
    """Whether alerts can be emailed with this configuration, and if not what to set."""
    if config.email_provider == "resend" and config.resend_api_key is not None:
        if not config.email_from:
            return EmailRoute(False, "set EMAIL_FROM (a sender on a domain verified in Resend)")
        return EmailRoute(True)
    if config.email_provider == "smtp" and config.smtp_host:
        return EmailRoute(True)
    if config.mail_catcher_host:
        return EmailRoute(True)
    return EmailRoute(False, "set RESEND_API_KEY and EMAIL_FROM, or SMTP_HOST, in .env")


def sender_from_config(config: Settings) -> EmailSender | None:
    if config.email_provider == "resend" and config.resend_api_key is not None:
        if not config.email_from:
            return None
        return ResendSender(config.resend_api_key.get_secret_value(), config.email_from)
    if config.email_provider == "smtp" and config.smtp_host:
        return SmtpSender(
            config.smtp_host,
            config.smtp_port,
            config.email_from or CATCHER_FROM,
            security=config.smtp_security,
            username=config.smtp_username,
            password=None
            if config.smtp_password is None
            else config.smtp_password.get_secret_value(),
        )
    if config.mail_catcher_host:
        return SmtpSender(config.mail_catcher_host, CATCHER_PORT, CATCHER_FROM, security="none")
    return None
