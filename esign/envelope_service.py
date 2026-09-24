from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import DocumentField, Envelope, EnvelopeDocument, EnvelopeRecipient, Folder
from .workflows import ACTIVE_STATUSES, audit


@transaction.atomic
def clone_envelope(*, source: Envelope, sender, subject: str | None = None) -> Envelope:
    clone = Envelope.objects.create(
        organization=source.organization,
        sender=sender,
        folder=source.folder,
        subject=subject or f"{source.subject} — Copy",
        message=source.message,
        reminder_enabled=source.reminder_enabled,
        reminder_interval_days=source.reminder_interval_days,
        allow_comments=source.allow_comments,
    )
    document_map = {
        item.id: EnvelopeDocument.objects.create(
            envelope=clone, document=item.document, position=item.position
        )
        for item in source.envelope_documents.select_related("document").order_by("position")
    }
    recipient_map = {}
    for recipient in source.recipients.order_by("routing_order", "created_at"):
        recipient_map[recipient.id] = EnvelopeRecipient.objects.create(
            envelope=clone,
            name=recipient.name,
            email=recipient.email,
            role=recipient.role,
            routing_order=recipient.routing_order,
            color=recipient.color,
            authentication_type=recipient.authentication_type,
            private_message=recipient.private_message,
        )
    for field in DocumentField.objects.filter(envelope_document__envelope=source):
        DocumentField.objects.create(
            envelope_document=document_map[field.envelope_document_id],
            recipient=recipient_map[field.recipient_id],
            field_type=field.field_type,
            page_number=field.page_number,
            x=field.x,
            y=field.y,
            width=field.width,
            height=field.height,
            required=field.required,
            label=field.label,
            validation=field.validation,
            options=field.options,
        )
    audit(
        clone,
        "ENVELOPE_CLONED",
        actor_id=sender.id,
        metadata={"source_envelope_id": str(source.id)},
    )
    return clone


def create_folder(*, organization, owner, name: str, kind=Folder.Kind.ENVELOPE) -> Folder:
    name = name.strip()
    if not name:
        raise ValidationError("Folder name is required.")
    folder, created = Folder.objects.get_or_create(
        organization=organization,
        kind=kind,
        name=name[:120],
        defaults={"owner": owner},
    )
    if not created:
        raise ValidationError("A folder with this name already exists.")
    return folder


@transaction.atomic
def rename_folder(*, folder: Folder, name: str) -> Folder:
    name = name.strip()
    if not name:
        raise ValidationError("Folder name is required.")
    if (
        Folder.objects.filter(organization=folder.organization, kind=folder.kind, name__iexact=name)
        .exclude(id=folder.id)
        .exists()
    ):
        raise ValidationError("A folder with this name already exists.")
    folder.name = name[:120]
    folder.save(update_fields=("name", "updated_at"))
    return folder


@transaction.atomic
def delete_folder(*, folder: Folder) -> None:
    if folder.kind == Folder.Kind.ENVELOPE:
        folder.envelopes.update(folder=None)
    else:
        folder.templates.update(folder=None)
    folder.delete()


@transaction.atomic
def move_envelopes(*, organization, envelope_ids, folder: Folder | None) -> int:
    if folder and (
        folder.organization_id != organization.id or folder.kind != Folder.Kind.ENVELOPE
    ):
        raise PermissionDenied("Folder is unavailable in this workspace.")
    return Envelope.objects.filter(organization=organization, id__in=envelope_ids).update(
        folder=folder
    )


@transaction.atomic
def soft_delete_envelopes(*, organization, envelope_ids) -> int:
    envelopes = Envelope.objects.filter(organization=organization, id__in=envelope_ids)
    if envelopes.filter(status__in=ACTIVE_STATUSES).exists():
        raise ValidationError("Active envelopes must be voided before deletion.")
    return envelopes.update(deleted_at=timezone.now())


def restore_envelopes(*, organization, envelope_ids) -> int:
    return Envelope.objects.filter(
        organization=organization, id__in=envelope_ids, deleted_at__isnull=False
    ).update(deleted_at=None)


@transaction.atomic
def correct_recipient(*, envelope: Envelope, actor, recipient_id, name, email, routing_order):
    if envelope.status not in ACTIVE_STATUSES:
        raise ValidationError("Only active envelopes can be corrected.")
    recipient = envelope.recipients.select_for_update().filter(id=recipient_id).first()
    if not recipient:
        raise PermissionDenied("Recipient is unavailable.")
    if recipient.status == EnvelopeRecipient.Status.SIGNED:
        raise ValidationError("Completed recipients cannot be changed.")
    before = {
        "name": recipient.name,
        "email": recipient.email,
        "routing_order": recipient.routing_order,
    }
    recipient.name = name.strip()
    recipient.email = email.strip().lower()
    recipient.routing_order = int(routing_order)
    recipient.full_clean()
    recipient.save(update_fields=("name", "email", "routing_order", "updated_at"))
    recipient.signing_sessions.filter(completed_at__isnull=True, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )
    audit(
        envelope,
        "RECIPIENT_CORRECTED",
        actor_id=actor.id,
        recipient=recipient,
        metadata={
            "before": before,
            "after": {
                "name": recipient.name,
                "email": recipient.email,
                "routing_order": recipient.routing_order,
            },
        },
    )
    return recipient
