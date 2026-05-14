"""Transactional email — pluggable backend (console for dev, SES for prod).

Configuration (environment):
    EMAIL_BACKEND       "console" (default) | "ses"
    EMAIL_FROM          default no-reply@vpp-italia.local
    AWS_SES_REGION      default eu-west-1 (SES is not available in eu-south-1)
    FRONTEND_BASE_URL   default http://localhost:3000

In production, set EMAIL_BACKEND=ses and ensure the AWS_SES_REGION's SES
identity (sender and, in sandbox, recipients) are verified.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class EmailMessage:
    to: str
    subject: str
    body_text: str
    body_html: str | None = None


class EmailBackend(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class ConsoleEmailBackend:
    """Dev / staging fallback — logs the email instead of sending it."""

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "email.sent_console",
            to=message.to,
            subject=message.subject,
            body=message.body_text,
        )


class SESEmailBackend:
    """AWS SES backend. Uses boto3's blocking client inside a thread so the
    event loop is not stalled."""

    def __init__(self, region: str, sender: str) -> None:
        import boto3  # local import: optional in dev

        self._client = boto3.client("ses", region_name=region)
        self._sender = sender

    async def send(self, message: EmailMessage) -> None:
        body: dict[str, dict[str, str]] = {"Text": {"Data": message.body_text, "Charset": "UTF-8"}}
        if message.body_html:
            body["Html"] = {"Data": message.body_html, "Charset": "UTF-8"}

        payload = {
            "Source": self._sender,
            "Destination": {"ToAddresses": [message.to]},
            "Message": {
                "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                "Body": body,
            },
        }

        def _send_sync() -> None:
            self._client.send_email(**payload)

        try:
            await asyncio.to_thread(_send_sync)
            logger.info("email.sent_ses", to=message.to, subject=message.subject)
        except Exception as exc:
            # Surface as a generic error to the caller — callers should NOT
            # leak whether the email exists / was delivered.
            logger.error("email.ses_failed", to=message.to, error=str(exc))
            raise


def get_email_backend() -> EmailBackend:
    """Return the configured backend. Defaults to console for safety."""
    backend = os.getenv("EMAIL_BACKEND", "console").lower()
    sender = os.getenv("EMAIL_FROM", "no-reply@vpp-italia.local")
    if backend == "ses":
        region = os.getenv("AWS_SES_REGION", "eu-west-1")
        return SESEmailBackend(region=region, sender=sender)
    return ConsoleEmailBackend()


def frontend_base_url() -> str:
    return os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")


def render_password_reset_email(*, full_name: str, reset_url: str, is_invite: bool) -> EmailMessage:
    if is_invite:
        subject = "Bienvenue sur VPP Italia — definissez votre mot de passe"
        intro = (
            f"Bonjour {full_name},\n\n"
            "Un compte VPP Italia vient d'etre cree pour vous. "
            "Cliquez sur le lien ci-dessous pour definir votre mot de passe "
            "et activer votre compte. Ce lien expire dans 7 jours."
        )
    else:
        subject = "Reinitialisation de votre mot de passe VPP Italia"
        intro = (
            f"Bonjour {full_name},\n\n"
            "Vous avez demande la reinitialisation de votre mot de passe. "
            "Cliquez sur le lien ci-dessous pour en definir un nouveau. "
            "Ce lien expire dans 1 heure et n'est utilisable qu'une seule fois."
        )

    body_text = (
        f"{intro}\n\n"
        f"{reset_url}\n\n"
        "Si vous n'avez pas demande cette operation, ignorez ce message.\n\n"
        "— L'equipe VPP Italia"
    )
    body_html = (
        "<p>" + intro.replace("\n\n", "</p><p>") + "</p>"
        f'<p><a href="{reset_url}">{reset_url}</a></p>'
        "<p>Si vous n'avez pas demande cette operation, ignorez ce message.</p>"
        "<p>— L'equipe VPP Italia</p>"
    )

    return EmailMessage(
        to="placeholder",  # set by caller
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
