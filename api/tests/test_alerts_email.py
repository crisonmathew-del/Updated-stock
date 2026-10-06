"""Email senders: the Resend request, the MIME layout with an inline chart, a real SMTP
conversation (with a minimal in-test server) and which sender a configuration picks."""

import asyncio
import base64
import email
import json
from collections.abc import AsyncIterator
from email import policy
from email.message import EmailMessage

import httpx
import pytest
from pydantic import SecretStr

from app.alerts.email import (
    EmailError,
    InlineImage,
    Message,
    ResendSender,
    SmtpSender,
    build_mime,
    email_route,
    sender_from_config,
)
from app.core.config import Settings

PNG = b"\x89PNG\r\n\x1a\nfake"
MESSAGE = Message(
    to="owner@example.com",
    subject="SPOT breaking out (provisional)",
    html='<p>Breakout</p><img src="cid:chart">',
    text="Breakout",
    images=(InlineImage("chart", "SPOT.png", PNG),),
)


async def test_resend_sends_html_text_and_the_inline_chart() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "abc"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = ResendSender("re_key", "Breakout <alerts@example.com>", client=client)
    await sender.send(MESSAGE)
    await sender.aclose()
    request = seen[0]
    assert request.headers["Authorization"] == "Bearer re_key"
    body = json.loads(request.content)
    assert body["from"] == "Breakout <alerts@example.com>"
    assert body["to"] == ["owner@example.com"]
    assert (body["subject"], body["text"]) == (MESSAGE.subject, "Breakout")
    assert body["attachments"] == [
        {"filename": "SPOT.png", "content": base64.b64encode(PNG).decode(), "content_id": "chart"}
    ]


async def test_resend_refusals_say_why() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "The example.com domain is not verified."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = ResendSender("re_key", "alerts@example.com", client=client)
    with pytest.raises(EmailError, match=r"HTTP 403\): The example.com domain is not verified"):
        await sender.send(MESSAGE)


def test_mime_has_text_and_html_with_the_chart_related_to_the_html() -> None:
    mime = build_mime(MESSAGE, "Breakout <alerts@example.com>")
    assert mime.get_content_type() == "multipart/alternative"
    text, related = mime.iter_parts()
    assert text.get_content_type() == "text/plain"
    assert related.get_content_type() == "multipart/related"
    html, image = related.iter_parts()
    cid = image["Content-ID"].strip("<>")
    assert f'src="cid:{cid}"' in html.get_content()
    assert image.get_content_type() == "image/png"
    assert image.get_content() == PNG


class FakeSmtp:
    """Just enough SMTP to accept one message per connection (no TLS, no auth)."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.envelopes: list[tuple[str, list[str]]] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def reply(line: str) -> None:
            writer.write(f"{line}\r\n".encode())

        reply("220 fake ESMTP")
        sender, rcpts = "", []
        while line := (await reader.readline()).decode().rstrip("\r\n"):
            verb = line.split(" ", 1)[0].upper()
            if verb == "EHLO":
                reply("250-fake")
                reply("250 8BITMIME")
            elif verb == "MAIL":
                sender = line.split(":", 1)[1].strip(" <>")
                reply("250 OK")
            elif verb == "RCPT":
                rcpts.append(line.split(":", 1)[1].strip(" <>"))
                reply("250 OK")
            elif verb == "DATA":
                reply("354 go ahead")
                await writer.drain()
                lines = []
                while (data := await reader.readline()) != b".\r\n":
                    lines.append(data[1:] if data.startswith(b"..") else data)
                parsed = email.message_from_bytes(b"".join(lines), policy=policy.default)
                self.messages.append(parsed)
                self.envelopes.append((sender, rcpts))
                reply("250 queued")
            elif verb == "QUIT":
                reply("221 bye")
                await writer.drain()
                break
            else:
                reply("250 OK")
            await writer.drain()
        writer.close()


@pytest.fixture
async def smtp_server() -> AsyncIterator[tuple[FakeSmtp, int]]:
    fake = FakeSmtp()
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield fake, port
    server.close()
    await server.wait_closed()


async def test_smtp_delivers_the_message(smtp_server: tuple[FakeSmtp, int]) -> None:
    fake, port = smtp_server
    sender = SmtpSender("127.0.0.1", port, "Breakout <alerts@breakout.local>", security="none")
    await sender.send(MESSAGE)
    assert fake.envelopes == [("alerts@breakout.local", ["owner@example.com"])]
    received = fake.messages[0]
    assert received["Subject"] == "SPOT breaking out (provisional)"
    assert received["To"] == "owner@example.com"
    image = next(p for p in received.walk() if p.get_content_type() == "image/png")
    assert image.get_content() == PNG


async def test_smtp_failures_name_the_server() -> None:
    sender = SmtpSender("127.0.0.1", 1, "alerts@example.com", security="none", timeout=2)
    with pytest.raises(EmailError, match=r"SMTP 127.0.0.1:1 failed"):
        await sender.send(MESSAGE)


def config(**values: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "mail_catcher_host": None}
    base.update(values)
    return Settings(**base)  # type: ignore[arg-type]


def test_the_configuration_picks_the_sender() -> None:
    nothing = config()
    assert sender_from_config(nothing) is None
    route = email_route(nothing)
    assert not route.configured
    assert "RESEND_API_KEY" in route.reason

    resend = config(resend_api_key=SecretStr("re_x"), email_from="alerts@example.com")
    assert isinstance(sender_from_config(resend), ResendSender)
    assert email_route(resend).configured
    no_from = config(resend_api_key=SecretStr("re_x"))
    assert sender_from_config(no_from) is None
    assert "EMAIL_FROM" in email_route(no_from).reason

    smtp = config(email_provider="smtp", smtp_host="smtp.example.com")
    assert isinstance(sender_from_config(smtp), SmtpSender)
    # Development: nothing configured, but compose runs Mailpit.
    catcher = config(mail_catcher_host="mailpit")
    assert isinstance(sender_from_config(catcher), SmtpSender)
    assert email_route(catcher).configured
