from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .domain import create_signing_token, hash_signing_token, validate_field_value
from .models import (
    AuditEvent,
    Document,
    DocumentField,
    Envelope,
    EnvelopeDocument,
    EnvelopeRecipient,
    Notification,
    Organization,
    Signature,
    SigningSession,
    User,
)
from .pdf import simple_pdf
from .pdf_flatten import flatten_envelope_document
from .storage import LocalPrivateStorage, decode_signature_image, validate_pdf


def audit(
    envelope: Envelope,
    event_type: str,
    *,
    actor_type="USER",
    actor_id="",
    recipient=None,
    metadata=None,
):
    return AuditEvent.objects.create(
        organization=envelope.organization,
        envelope=envelope,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=str(actor_id),
        recipient=recipient,
        metadata=metadata or {},
    )


def get_envelope_for_member(
    envelope_id, user: User, organization: Organization, *, draft_only=False
) -> Envelope:
    queryset = Envelope.objects.for_organization(organization).filter(
        id=envelope_id, deleted_at__isnull=True
    )
    if draft_only:
        queryset = queryset.filter(status=Envelope.Status.DRAFT)
    envelope = queryset.first()
    if envelope is None:
        raise PermissionDenied("Envelope is unavailable in this workspace.")
    return envelope


@transaction.atomic
def create_draft(*, organization, sender, subject="Untitled envelope") -> Envelope:
    envelope = Envelope.objects.create(
        organization=organization,
        sender=sender,
        subject=organization.default_subject or subject,
        message=organization.default_message,
        reminder_enabled=True,
        reminder_interval_days=organization.default_reminder_days,
    )
    audit(envelope, "ENVELOPE_CREATED", actor_id=sender.id)
    return envelope


@transaction.atomic
def add_document(*, envelope, uploaded_by, upload) -> EnvelopeDocument:
    if envelope.status != Envelope.Status.DRAFT:
        raise ValidationError("Sent envelopes cannot accept new documents.")
    validate_pdf(upload)
    stored = LocalPrivateStorage().save(upload)
    try:
        document = Document.objects.create(
            organization=envelope.organization,
            uploaded_by=uploaded_by,
            filename=upload.name.rsplit("/", 1)[-1][:255],
            mime_type="application/pdf",
            byte_size=stored.byte_size,
            storage_key=stored.key,
            sha256=stored.sha256,
        )
        position = envelope.envelope_documents.count() + 1
        envelope_document = EnvelopeDocument.objects.create(
            envelope=envelope, document=document, position=position
        )
        audit(
            envelope,
            "DOCUMENT_UPLOADED",
            actor_id=uploaded_by.id,
            metadata={"document_id": str(document.id), "sha256": stored.sha256},
        )
        return envelope_document
    except Exception:
        LocalPrivateStorage().delete(stored.key)
        raise


@transaction.atomic
def remove_document(*, envelope, envelope_document_id, actor):
    item = (
        envelope.envelope_documents.select_related("document")
        .filter(id=envelope_document_id)
        .first()
    )
    if envelope.status != Envelope.Status.DRAFT or item is None:
        raise ValidationError("Document cannot be removed.")
    item.delete()
    for position, remaining in enumerate(envelope.envelope_documents.order_by("position"), 1):
        if remaining.position != position:
            remaining.position = position
            remaining.save(update_fields=("position", "updated_at"))
    audit(envelope, "DOCUMENT_REMOVED", actor_id=actor.id)


@transaction.atomic
def add_recipient(
    *, envelope, actor, name, email, role, routing_order, private_message=""
) -> EnvelopeRecipient:
    if envelope.status != Envelope.Status.DRAFT:
        raise ValidationError("Recipients on sent envelopes require correction mode.")
    recipient = EnvelopeRecipient.objects.create(
        envelope=envelope,
        name=name.strip(),
        email=email.strip().lower(),
        role=role,
        routing_order=routing_order,
        private_message=private_message.strip(),
        color=("#7c5ce7", "#167d68", "#d56822", "#2474c6")[envelope.recipients.count() % 4],
    )
    audit(envelope, "RECIPIENT_ADDED", actor_id=actor.id, recipient=recipient)
    return recipient


def validate_for_send(envelope: Envelope) -> None:
    errors = []
    if not envelope.envelope_documents.exists():
        errors.append("Upload at least one PDF document.")
    actionable = envelope.recipients.filter(
        role__in=(
            EnvelopeRecipient.Role.SIGNER,
            EnvelopeRecipient.Role.APPROVER,
            EnvelopeRecipient.Role.IN_PERSON,
        )
    )
    if not actionable.exists():
        errors.append("Add at least one signing or approving recipient.")
    for recipient in actionable:
        if (
            recipient.role in (EnvelopeRecipient.Role.SIGNER, EnvelopeRecipient.Role.IN_PERSON)
            and not recipient.fields.filter(field_type=DocumentField.Type.SIGNATURE).exists()
        ):
            errors.append(f"Place a signature field for {recipient.name}.")
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def send_envelope(*, envelope_id, actor, organization, request) -> list[str]:
    envelope = Envelope.objects.select_for_update().get(
        id=envelope_id, organization=organization, sender=actor
    )
    if envelope.status != Envelope.Status.DRAFT:
        raise ValidationError("Only draft envelopes can be sent.")
    validate_for_send(envelope)
    now = timezone.now()
    envelope.status = Envelope.Status.SENT
    envelope.sent_at = now
    if envelope.expires_at is None:
        envelope.expires_at = now + timedelta(days=30)
    envelope.save(update_fields=("status", "sent_at", "expires_at", "updated_at"))
    audit(envelope, "ENVELOPE_SENT", actor_id=actor.id)
    first_order = (
        envelope.recipients.exclude(role=EnvelopeRecipient.Role.COPY)
        .order_by("routing_order")
        .values_list("routing_order", flat=True)
        .first()
    )
    links = []
    for recipient in envelope.recipients.filter(routing_order=first_order):
        raw, digest = create_signing_token()
        SigningSession.objects.create(
            envelope=envelope,
            recipient=recipient,
            token_hash=digest,
            expires_at=envelope.expires_at,
        )
        recipient.status = EnvelopeRecipient.Status.SENT
        recipient.save(update_fields=("status", "updated_at"))
        link = request.build_absolute_uri(reverse("signing", args=(raw,)))
        send_mail(
            envelope.subject,
            f"{actor.get_full_name() or actor.email} sent you an agreement. Review and sign: {link}\n\n{envelope.message}",
            settings.DEFAULT_FROM_EMAIL,
            [recipient.email],
        )
        audit(envelope, "INVITATION_SENT", actor_id=actor.id, recipient=recipient)
        links.append(link)
    return links


def resolve_signing_session(raw_token: str, *, lock=False) -> SigningSession:
    queryset = SigningSession.objects.select_related("envelope", "recipient", "envelope__sender")
    if lock:
        queryset = queryset.select_for_update()
    session = queryset.filter(token_hash=hash_signing_token(raw_token)).first()
    now = timezone.now()
    if session is None:
        raise PermissionDenied("This signing link is invalid.")
    if session.revoked_at or session.envelope.status == Envelope.Status.VOIDED:
        raise PermissionDenied("This envelope is no longer available.")
    if session.expires_at <= now or session.envelope.status == Envelope.Status.EXPIRED:
        raise PermissionDenied("This signing link has expired.")
    return session


@transaction.atomic
def mark_viewed(raw_token: str) -> SigningSession:
    session = resolve_signing_session(raw_token, lock=True)
    if not session.recipient.viewed_at:
        session.recipient.viewed_at = timezone.now()
        session.recipient.status = EnvelopeRecipient.Status.VIEWED
        session.recipient.save(update_fields=("viewed_at", "status", "updated_at"))
        audit(
            session.envelope,
            "RECIPIENT_VIEWED",
            actor_type="RECIPIENT",
            recipient=session.recipient,
        )
    return session


@transaction.atomic
def finish_signing(
    *,
    raw_token,
    values,
    signature_text,
    idempotency_key,
    request=None,
    signature_payload="",
    signature_method="ADOPTED",
    initials_payload="",
    initials_text="",
) -> Envelope:
    session = resolve_signing_session(raw_token, lock=True)
    if session.completed_at:
        return session.envelope
    if not idempotency_key:
        raise ValidationError("A completion idempotency key is required.")
    recipient = EnvelopeRecipient.objects.select_for_update().get(id=session.recipient_id)
    fields = list(recipient.fields.select_for_update())
    missing = []
    signature_values = {
        "SIGNATURE": signature_text.strip(),
        "INITIAL": initials_text.strip(),
    }
    for field in fields:
        value = values.get(str(field.id), "").strip()
        if field.field_type in (
            DocumentField.Type.SIGNATURE,
            DocumentField.Type.INITIAL,
        ):
            value = signature_values[field.field_type]
        if field.field_type == DocumentField.Type.DATE_SIGNED and not value:
            value = timezone.localdate().isoformat()
        try:
            value = validate_field_value(
                field.field_type, value, field.validation, required=field.required
            )
        except ValueError as error:
            missing.append(f"{field.label or field.get_field_type_display()}: {error}")
        field.value = value
    if missing:
        raise ValidationError([f"Check field {label}" for label in missing])
    DocumentField.objects.bulk_update(fields, ("value", "updated_at"))
    if signature_text.strip():
        storage_key = ""
        if signature_payload:
            content, suffix, _width, _height = decode_signature_image(signature_payload)
            storage_key = LocalPrivateStorage().save_bytes(content, suffix).key
        signature = Signature.objects.create(
            recipient=recipient,
            signature_type=signature_method
            if signature_method in Signature.Type.values
            else Signature.Type.ADOPTED,
            purpose=Signature.Purpose.SIGNATURE,
            storage_key=storage_key,
            text_representation=signature_text.strip(),
        )
        audit(
            session.envelope,
            "SIGNATURE_ADOPTED",
            actor_type="RECIPIENT",
            recipient=recipient,
            metadata={
                "signature_id": str(signature.id),
                "method": signature.signature_type,
            },
        )
    if initials_text.strip():
        storage_key = ""
        if initials_payload:
            content, suffix, _width, _height = decode_signature_image(initials_payload)
            storage_key = LocalPrivateStorage().save_bytes(content, suffix).key
        Signature.objects.create(
            recipient=recipient,
            signature_type=Signature.Type.DRAWN if initials_payload else Signature.Type.ADOPTED,
            purpose=Signature.Purpose.INITIAL,
            storage_key=storage_key,
            text_representation=initials_text.strip(),
        )
        audit(
            session.envelope,
            "INITIALS_ADOPTED",
            actor_type="RECIPIENT",
            recipient=recipient,
        )
    now = timezone.now()
    recipient.status = EnvelopeRecipient.Status.SIGNED
    recipient.completed_at = now
    recipient.save(update_fields=("status", "completed_at", "updated_at"))
    session.completed_at = now
    session.idempotency_key = idempotency_key[:120]
    session.save(update_fields=("completed_at", "idempotency_key", "updated_at"))
    audit(
        session.envelope,
        "RECIPIENT_COMPLETED",
        actor_type="RECIPIENT",
        recipient=recipient,
    )
    envelope = session.envelope
    current_order_incomplete = (
        envelope.recipients.filter(routing_order=recipient.routing_order)
        .exclude(status=EnvelopeRecipient.Status.SIGNED)
        .exclude(role=EnvelopeRecipient.Role.COPY)
        .exists()
    )
    if not current_order_incomplete:
        next_recipients = (
            envelope.recipients.filter(
                routing_order__gt=recipient.routing_order,
                status=EnvelopeRecipient.Status.PENDING,
            )
            .exclude(role=EnvelopeRecipient.Role.COPY)
            .order_by("routing_order")
        )
        next_order = next_recipients.values_list("routing_order", flat=True).first()
        if next_order is not None:
            for next_recipient in next_recipients.filter(routing_order=next_order):
                raw, digest = create_signing_token()
                SigningSession.objects.create(
                    envelope=envelope,
                    recipient=next_recipient,
                    token_hash=digest,
                    expires_at=envelope.expires_at,
                )
                next_recipient.status = EnvelopeRecipient.Status.SENT
                next_recipient.save(update_fields=("status", "updated_at"))
                link = (
                    request.build_absolute_uri(reverse("signing", args=(raw,)))
                    if request
                    else reverse("signing", args=(raw,))
                )
                send_mail(
                    envelope.subject,
                    f"It is your turn to sign: {link}",
                    settings.DEFAULT_FROM_EMAIL,
                    [next_recipient.email],
                )
                audit(envelope, "NEXT_RECIPIENT_ACTIVATED", recipient=next_recipient)
            envelope.status = Envelope.Status.PARTIALLY_COMPLETED
        else:
            complete_envelope(envelope)
    else:
        envelope.status = Envelope.Status.PARTIALLY_COMPLETED
    envelope.save(
        update_fields=(
            "status",
            "completed_at",
            "certificate_storage_key",
            "certificate_sha256",
            "updated_at",
        )
    )
    return envelope


def complete_envelope(envelope: Envelope) -> None:
    storage = LocalPrivateStorage()
    for item in envelope.envelope_documents.select_related("document"):
        completed = storage.save_bytes(flatten_envelope_document(item))
        item.completed_storage_key = completed.key
        item.completed_sha256 = completed.sha256
        item.save(update_fields=("completed_storage_key", "completed_sha256", "updated_at"))
        audit(
            envelope,
            "FINAL_PDF_GENERATED",
            actor_type="SYSTEM",
            metadata={"document_id": str(item.document_id), "sha256": completed.sha256},
        )
    envelope.status = Envelope.Status.COMPLETED
    envelope.completed_at = timezone.now()
    audit(envelope, "ENVELOPE_COMPLETED", actor_type="SYSTEM")
    certificate = simple_pdf(
        [
            "SIGNFLOW CERTIFICATE OF COMPLETION",
            f"Envelope: {envelope.id}",
            f"Title: {envelope.subject}",
            f"Sender: {envelope.sender.email}",
            f"Sent: {envelope.sent_at}",
            f"Completed: {envelope.completed_at}",
            *[
                f"Recipient: {r.name} <{r.email}> | order {r.routing_order} | {r.status} | {r.completed_at}"
                for r in envelope.recipients.all()
            ],
            *[
                f"Document SHA-256: {d.document.sha256} / completed {d.completed_sha256}"
                for d in envelope.envelope_documents.select_related("document")
            ],
        ]
    )
    stored = storage.save_bytes(certificate)
    envelope.certificate_storage_key = stored.key
    envelope.certificate_sha256 = stored.sha256
    Notification.objects.create(
        organization=envelope.organization,
        user=envelope.sender,
        kind="ENVELOPE_COMPLETED",
        title=f"{envelope.subject} completed",
    )
    audit(
        envelope,
        "CERTIFICATE_GENERATED",
        actor_type="SYSTEM",
        metadata={"sha256": stored.sha256},
    )


ACTIVE_STATUSES = (
    Envelope.Status.SENT,
    Envelope.Status.DELIVERED,
    Envelope.Status.VIEWED,
    Envelope.Status.PARTIALLY_COMPLETED,
)


@transaction.atomic
def void_envelope(*, envelope: Envelope, actor: User, reason: str) -> None:
    locked = Envelope.objects.select_for_update().get(id=envelope.id)
    if locked.status not in ACTIVE_STATUSES:
        raise ValidationError("Only an active envelope can be voided.")
    reason = reason.strip()
    if not reason:
        raise ValidationError("A reason for voiding is required.")
    locked.status = Envelope.Status.VOIDED
    locked.void_reason = reason
    locked.save(update_fields=("status", "void_reason", "updated_at"))
    locked.signing_sessions.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit(locked, "ENVELOPE_VOIDED", actor_id=actor.id, metadata={"reason": reason})
    for recipient in locked.recipients.exclude(status=EnvelopeRecipient.Status.SIGNED):
        send_mail(
            f"Voided: {locked.subject}",
            f"The sender voided this envelope. Reason: {reason}",
            settings.DEFAULT_FROM_EMAIL,
            [recipient.email],
        )


@transaction.atomic
def send_reminders(*, envelope: Envelope, actor: User, request) -> int:
    locked = Envelope.objects.select_for_update().get(id=envelope.id)
    if locked.status not in ACTIVE_STATUSES:
        raise ValidationError("Only active envelopes can receive reminders.")
    count = 0
    recipients = locked.recipients.filter(
        status__in=(
            EnvelopeRecipient.Status.SENT,
            EnvelopeRecipient.Status.DELIVERED,
            EnvelopeRecipient.Status.VIEWED,
        )
    )
    for recipient in recipients:
        recipient.signing_sessions.filter(
            revoked_at__isnull=True, completed_at__isnull=True
        ).update(revoked_at=timezone.now())
        raw, digest = create_signing_token()
        SigningSession.objects.create(
            envelope=locked,
            recipient=recipient,
            token_hash=digest,
            expires_at=locked.expires_at,
        )
        link = request.build_absolute_uri(reverse("signing", args=(raw,)))
        send_mail(
            f"Reminder: {locked.subject}",
            f"Your agreement is waiting for your review: {link}",
            settings.DEFAULT_FROM_EMAIL,
            [recipient.email],
        )
        audit(locked, "REMINDER_SENT", actor_id=actor.id, recipient=recipient)
        count += 1
    return count


@transaction.atomic
def decline_envelope(*, raw_token: str, reason: str) -> Envelope:
    session = resolve_signing_session(raw_token, lock=True)
    reason = reason.strip()
    recipient = EnvelopeRecipient.objects.select_for_update().get(id=session.recipient_id)
    recipient.status = EnvelopeRecipient.Status.DECLINED
    recipient.save(update_fields=("status", "updated_at"))
    envelope = Envelope.objects.select_for_update().get(id=session.envelope_id)
    envelope.status = Envelope.Status.DECLINED
    envelope.save(update_fields=("status", "updated_at"))
    envelope.signing_sessions.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit(
        envelope,
        "RECIPIENT_DECLINED",
        actor_type="RECIPIENT",
        recipient=recipient,
        metadata={"reason": reason},
    )
    Notification.objects.create(
        organization=envelope.organization,
        user=envelope.sender,
        kind="RECIPIENT_DECLINED",
        title=f"{recipient.name} declined {envelope.subject}",
        body=reason,
    )
    send_mail(
        f"Declined: {envelope.subject}",
        f"{recipient.name} declined to sign. {reason}",
        settings.DEFAULT_FROM_EMAIL,
        [envelope.sender.email],
    )
    return envelope


@transaction.atomic
def expire_due_envelopes(*, now=None) -> int:
    now = now or timezone.now()
    envelopes = list(
        Envelope.objects.select_for_update().filter(status__in=ACTIVE_STATUSES, expires_at__lte=now)
    )
    for envelope in envelopes:
        envelope.status = Envelope.Status.EXPIRED
        envelope.save(update_fields=("status", "updated_at"))
        envelope.signing_sessions.filter(revoked_at__isnull=True).update(revoked_at=now)
        audit(envelope, "ENVELOPE_EXPIRED", actor_type="SYSTEM")
        Notification.objects.create(
            organization=envelope.organization,
            user=envelope.sender,
            kind="ENVELOPE_EXPIRED",
            title=f"{envelope.subject} expired",
        )
    return len(envelopes)
