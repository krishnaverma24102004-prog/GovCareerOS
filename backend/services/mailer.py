"""
Outbound email over SMTP (STARTTLS), configured by EMAIL_HOST / EMAIL_PORT / EMAIL_USERNAME / EMAIL_PASSWORD.

Nothing here runs on its own: callers send only when explicitly asked (scripts/send_digests.py --send).
Without a username and password the mailer is "not configured" and send() refuses, so digests are
then only written to disk. Messages are UTF-8 (Hindi names and text survive), with a plain-text part
and an HTML alternative.
"""
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from govcareeros.backend.config.settings import settings

class MailerNotConfigured(RuntimeError):
    pass

def is_configured() -> bool:
    return bool(settings.EMAIL_HOST and settings.EMAIL_USERNAME and settings.EMAIL_PASSWORD)

def build_message(to: str, subject: str, text: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.EMAIL_USERNAME
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=settings.EMAIL_USERNAME.rsplit("@", 1)[-1] if "@" in settings.EMAIL_USERNAME else None)
    msg.set_content(text, charset="utf-8")
    msg.add_alternative(html, subtype="html", charset="utf-8")
    return msg

def send(to: str, subject: str, text: str, html: str, timeout: float = 30) -> None:
    """Blocking send; call through asyncio.to_thread from async code."""
    if not is_configured():
        raise MailerNotConfigured("EMAIL_USERNAME / EMAIL_PASSWORD not set")
    msg = build_message(to, subject, text, html)
    # 465 is implicit TLS; every other port (587, 25) upgrades with STARTTLS
    if settings.EMAIL_PORT == 465:
        smtp_cm = smtplib.SMTP_SSL(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=timeout)
    else:
        smtp_cm = smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=timeout)
    with smtp_cm as smtp:
        if settings.EMAIL_PORT != 465:
            smtp.starttls()
        smtp.login(settings.EMAIL_USERNAME, settings.EMAIL_PASSWORD)
        smtp.send_message(msg)
