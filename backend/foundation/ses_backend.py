"""Sending email through AWS SES (decided 27 Nov 2026, DEV-400: AWS SES for email).

A Django email backend, like the SendGrid one (DEV-350), so the queue, retries, masking and wiping
in foundation/mail.py stay exactly as they are; only the hand-over changes. The SES v2 API over
HTTPS, signed with AWS Signature Version 4 (foundation/aws.py): credentials and region come only
from the server's environment and are never logged. A failure raises, so the queue tries again.
`SES_CONFIGURATION_SET` (optional) names the SES configuration set that reports deliveries.
"""
from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from . import aws


class SesError(RuntimeError):
    pass


def payload(message: Any) -> dict[str, Any]:
    """What SES is asked to send: plain text only, no tracking (it would add links to mail)."""
    body: dict[str, Any] = {
        "FromEmailAddress": message.from_email or settings.DEFAULT_FROM_EMAIL,
        "Destination": {"ToAddresses": list(message.to)},
        "Content": {"Simple": {
            "Subject": {"Data": message.subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": message.body, "Charset": "UTF-8"}},
        }},
    }
    config_set = getattr(settings, "SES_CONFIGURATION_SET", "")
    if config_set:
        body["ConfigurationSetName"] = config_set
    ref = (message.extra_headers or {}).get("X-Zoiko-Ref")
    if ref:   # comes back in the delivery event; an id, nothing personal
        body["EmailTags"] = [{"Name": "zoiko_ref", "Value": str(ref)[:32]}]
    return body


class SesBackend(BaseEmailBackend):
    def send_messages(self, email_messages: Any) -> int:
        sent = 0
        for message in email_messages:
            try:
                url = f"https://email.{aws.region()}.amazonaws.com/v2/email/outbound-emails"
                aws.call(service="ses", url=url, body=json.dumps(payload(message)).encode(),
                         headers={"Content-Type": "application/json"})
            except aws.AwsError as exc:
                if self.fail_silently:
                    continue
                raise SesError(str(exc)) from None
            sent += 1
        return sent
