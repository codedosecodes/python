from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from .models import OutboundEmail

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


def queue_email(
    *,
    organization,
    message_type,
    to_email,
    subject,
    text_body,
    html_body="",
    envelope=None,
    recipient=None,
) -> OutboundEmail:
    message = OutboundEmail.objects.create(
        organization=organization,
        envelope=envelope,
        recipient=recipient,
        message_type=message_type,
        to_email=to_email.strip().lower(),
        subject=subject[:240],
        text_body=text_body,
        html_body=html_body,
    )
    if getattr(settings, "EMAIL_SEND_SYNCHRONOUSLY", settings.DEBUG):
        deliver_email(message.id)
    return message


@transaction.atomic
def deliver_email(message_id) -> bool:
    message = OutboundEmail.objects.select_for_update().filter(id=message_id).first()
    if not message or message.status == OutboundEmail.Status.SENT:
        return bool(message)
    if message.attempt_count >= MAX_ATTEMPTS:
        return False
    message.status = OutboundEmail.Status.PROCESSING
    message.attempt_count += 1
    message.save(update_fields=("status", "attempt_count", "updated_at"))
    try:
        email = EmailMultiAlternatives(
            subject=message.subject,
            body=message.text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[message.to_email],
        )
        if message.html_body:
            email.attach_alternative(message.html_body, "text/html")
        email.send(fail_silently=False)
    except Exception as error:
        delay_minutes = min(60, 2**message.attempt_count)
        message.status = OutboundEmail.Status.FAILED
        message.last_error = str(error)[:1000]
        message.next_retry_at = timezone.now() + timedelta(minutes=delay_minutes)
        message.save(update_fields=("status", "last_error", "next_retry_at", "updated_at"))
        logger.exception(
            "outbound_email_failed",
            extra={"email_id": str(message.id), "attempt": message.attempt_count},
        )
        return False
    message.status = OutboundEmail.Status.SENT
    message.sent_at = timezone.now()
    message.last_error = ""
    message.next_retry_at = None
    message.save(update_fields=("status", "sent_at", "last_error", "next_retry_at", "updated_at"))
    return True


def retry_due_emails(*, now=None, limit=100) -> tuple[int, int]:
    now = now or timezone.now()
    ids = list(
        OutboundEmail.objects.filter(
            status=OutboundEmail.Status.QUEUED,
            attempt_count__lt=MAX_ATTEMPTS,
        ).values_list("id", flat=True)[:limit]
    )
    # Include failed messages whose retry time has arrived without selecting
    # unbounded queue rows in one worker tick.
    failed_ids = list(
        OutboundEmail.objects.filter(
            status=OutboundEmail.Status.FAILED,
            attempt_count__lt=MAX_ATTEMPTS,
            next_retry_at__lte=now,
        ).values_list("id", flat=True)[: max(0, limit - len(ids))]
    )
    sent = sum(deliver_email(identifier) for identifier in dict.fromkeys(ids + failed_ids))
    return sent, len(ids) + len(failed_ids) - sent
