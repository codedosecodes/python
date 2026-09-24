from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .models import (
    DocumentField,
    Envelope,
    EnvelopeDocument,
    EnvelopeRecipient,
    Template,
    TemplateDocument,
    TemplateField,
    TemplateRecipient,
)
from .workflows import audit


def get_template_for_member(template_id, organization) -> Template:
    template = Template.objects.filter(id=template_id, organization=organization).first()
    if template is None:
        raise PermissionDenied("Template is unavailable in this workspace.")
    return template


@transaction.atomic
def create_template_from_envelope(
    *, envelope: Envelope, owner, name: str, description=""
) -> Template:
    if not name.strip():
        raise ValidationError("Template name is required.")
    template = Template.objects.create(
        organization=envelope.organization,
        owner=owner,
        name=name.strip()[:240],
        description=description.strip(),
    )
    recipient_map = {}
    for recipient in envelope.recipients.order_by("routing_order", "created_at"):
        role = TemplateRecipient.objects.create(
            template=template,
            role_name=recipient.name or recipient.get_role_display(),
            recipient_role=recipient.role,
            routing_order=recipient.routing_order,
            color=recipient.color,
        )
        recipient_map[recipient.id] = role
    document_map = {}
    for item in envelope.envelope_documents.select_related("document").order_by("position"):
        template_document = TemplateDocument.objects.create(
            template=template, document=item.document, position=item.position
        )
        document_map[item.id] = template_document
    for field in DocumentField.objects.filter(envelope_document__envelope=envelope):
        TemplateField.objects.create(
            template_document=document_map[field.envelope_document_id],
            template_recipient=recipient_map[field.recipient_id],
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
    return template


@transaction.atomic
def instantiate_template(
    *, template: Template, sender, assignments: dict[str, dict[str, str]]
) -> Envelope:
    envelope = Envelope.objects.create(
        organization=template.organization,
        sender=sender,
        subject=template.name,
        message=template.description,
    )
    document_map = {}
    for template_document in template.template_documents.select_related("document"):
        item = EnvelopeDocument.objects.create(
            envelope=envelope,
            document=template_document.document,
            position=template_document.position,
        )
        document_map[template_document.id] = item
    recipient_map = {}
    for role in template.template_recipients.all():
        assignment = assignments.get(str(role.id), {})
        name = assignment.get("name", "").strip()
        email = assignment.get("email", "").strip().lower()
        if not name or "@" not in email:
            raise ValidationError(f"Provide a name and valid email for {role.role_name}.")
        recipient_map[role.id] = EnvelopeRecipient.objects.create(
            envelope=envelope,
            name=name,
            email=email,
            role=role.recipient_role,
            routing_order=role.routing_order,
            color=role.color,
        )
    for field in TemplateField.objects.filter(template_document__template=template):
        DocumentField.objects.create(
            envelope_document=document_map[field.template_document_id],
            recipient=recipient_map[field.template_recipient_id],
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
    template.usage_count += 1
    template.save(update_fields=("usage_count", "updated_at"))
    audit(
        envelope,
        "ENVELOPE_CREATED_FROM_TEMPLATE",
        actor_id=sender.id,
        metadata={"template_id": str(template.id)},
    )
    return envelope


@transaction.atomic
def duplicate_template(*, template: Template, owner) -> Template:
    temporary = instantiate_template(
        template=template,
        sender=owner,
        assignments={
            str(role.id): {
                "name": role.role_name,
                "email": f"role-{index}@template.invalid",
            }
            for index, role in enumerate(template.template_recipients.all(), 1)
        },
    )
    duplicate = create_template_from_envelope(
        envelope=temporary,
        owner=owner,
        name=f"{template.name} — Copy",
        description=template.description,
    )
    temporary.delete()
    return duplicate
