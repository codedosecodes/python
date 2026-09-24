import uuid

from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    title = models.CharField(max_length=120, blank=True)
    company = models.CharField(max_length=180, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)


class Organization(UUIDModel):
    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=180, unique=True)
    display_name = models.CharField(max_length=180, blank=True)
    primary_color = models.CharField(max_length=7, default="#1769e0")
    default_expiration_days = models.PositiveSmallIntegerField(default=30)
    default_reminder_days = models.PositiveSmallIntegerField(default=3)
    default_subject = models.CharField(max_length=240, blank=True)
    default_message = models.TextField(blank=True)
    members = models.ManyToManyField(
        User, through="OrganizationMembership", related_name="organizations"
    )

    def __str__(self):
        return self.name


class OrganizationMembership(UUIDModel):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        ADMIN = "ADMIN", "Administrator"
        MEMBER = "MEMBER", "Member"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("organization", "user"), name="unique_org_member")
        ]


class OrganizationInvitation(UUIDModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invitations"
    )
    invited_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="sent_organization_invitations"
    )
    email = models.EmailField()
    role = models.CharField(
        max_length=16,
        choices=OrganizationMembership.Role.choices,
        default=OrganizationMembership.Role.MEMBER,
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("organization", "email", "accepted_at"), name="org_invite_email_idx"
            )
        ]


class Folder(UUIDModel):
    class Kind(models.TextChoices):
        ENVELOPE = "ENVELOPE", "Envelope folder"
        TEMPLATE = "TEMPLATE", "Template folder"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="folders")
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="folders")
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "kind", "name"), name="unique_org_folder_name"
            )
        ]


class EnvelopeQuerySet(models.QuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)


class Envelope(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SENT = "SENT", "Sent"
        DELIVERED = "DELIVERED", "Delivered"
        VIEWED = "VIEWED", "Viewed"
        PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED", "Partially completed"
        COMPLETED = "COMPLETED", "Completed"
        DECLINED = "DECLINED", "Declined"
        VOIDED = "VOIDED", "Voided"
        EXPIRED = "EXPIRED", "Expired"

    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="envelopes"
    )
    sender = models.ForeignKey(User, on_delete=models.PROTECT, related_name="sent_envelopes")
    folder = models.ForeignKey(
        Folder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="envelopes",
        limit_choices_to={"kind": Folder.Kind.ENVELOPE},
    )
    subject = models.CharField(max_length=240)
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reminder_enabled = models.BooleanField(default=False)
    reminder_interval_days = models.PositiveSmallIntegerField(default=3)
    allow_comments = models.BooleanField(default=True)
    void_reason = models.TextField(blank=True)
    certificate_storage_key = models.CharField(max_length=512, blank=True)
    certificate_sha256 = models.CharField(max_length=64, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    objects = EnvelopeQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(
                fields=("organization", "status", "-updated_at"),
                name="env_org_status_idx",
            )
        ]


class EnvelopeRecipient(UUIDModel):
    class Role(models.TextChoices):
        SIGNER = "SIGNER", "Needs to sign"
        COPY = "COPY", "Receives a copy"
        VIEWER = "VIEWER", "Needs to view"
        IN_PERSON = "IN_PERSON", "In person signer"
        APPROVER = "APPROVER", "Approver"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        DELIVERED = "DELIVERED", "Delivered"
        VIEWED = "VIEWED", "Viewed"
        SIGNED = "SIGNED", "Signed"
        DECLINED = "DECLINED", "Declined"

    envelope = models.ForeignKey(Envelope, on_delete=models.CASCADE, related_name="recipients")
    name = models.CharField(max_length=180)
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.SIGNER)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    routing_order = models.PositiveSmallIntegerField(default=1)
    color = models.CharField(max_length=7, default="#7c5ce7")
    authentication_type = models.CharField(max_length=32, default="EMAIL")
    private_message = models.TextField(blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("routing_order", "created_at")
        indexes = [
            models.Index(
                fields=("envelope", "routing_order", "status"),
                name="recipient_route_idx",
            )
        ]


class Document(UUIDModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="documents"
    )
    uploaded_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="uploaded_documents"
    )
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    byte_size = models.PositiveBigIntegerField()
    page_count = models.PositiveIntegerField(default=1)
    storage_key = models.CharField(max_length=512, unique=True)
    sha256 = models.CharField(max_length=64)


class EnvelopeDocument(UUIDModel):
    envelope = models.ForeignKey(
        Envelope, on_delete=models.CASCADE, related_name="envelope_documents"
    )
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="envelope_uses")
    position = models.PositiveSmallIntegerField()
    completed_storage_key = models.CharField(max_length=512, blank=True)
    completed_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=("envelope", "position"), name="unique_envelope_doc_position"
            )
        ]


class DocumentField(UUIDModel):
    class Type(models.TextChoices):
        SIGNATURE = "SIGNATURE", "Signature"
        INITIAL = "INITIAL", "Initial"
        STAMP = "STAMP", "Stamp"
        DATE_SIGNED = "DATE_SIGNED", "Date signed"
        NAME = "NAME", "Name"
        EMAIL = "EMAIL", "Email"
        COMPANY = "COMPANY", "Company"
        TITLE = "TITLE", "Title"
        TEXT = "TEXT", "Text"
        NUMBER = "NUMBER", "Number"
        DATE = "DATE", "Date"
        CHECKBOX = "CHECKBOX", "Checkbox"
        RADIO = "RADIO", "Radio"
        DROPDOWN = "DROPDOWN", "Dropdown"
        ATTACHMENT = "ATTACHMENT", "Attachment"
        NOTE = "NOTE", "Note"

    envelope_document = models.ForeignKey(
        EnvelopeDocument, on_delete=models.CASCADE, related_name="fields"
    )
    recipient = models.ForeignKey(
        EnvelopeRecipient, on_delete=models.CASCADE, related_name="fields"
    )
    field_type = models.CharField(max_length=16, choices=Type.choices)
    page_number = models.PositiveIntegerField()
    x = models.DecimalField(
        max_digits=8,
        decimal_places=7,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    y = models.DecimalField(
        max_digits=8,
        decimal_places=7,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    width = models.DecimalField(
        max_digits=8,
        decimal_places=7,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    height = models.DecimalField(
        max_digits=8,
        decimal_places=7,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    required = models.BooleanField(default=True)
    label = models.CharField(max_length=180, blank=True)
    value = models.TextField(blank=True)
    validation = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(page_number__gte=1), name="field_page_positive"),
            models.CheckConstraint(
                condition=Q(x__gte=0, x__lte=1, y__gte=0, y__lte=1),
                name="field_origin_normalized",
            ),
        ]


class Signature(UUIDModel):
    class Type(models.TextChoices):
        DRAWN = "DRAWN", "Drawn"
        ADOPTED = "ADOPTED", "Adopted"
        UPLOADED = "UPLOADED", "Uploaded"

    class Purpose(models.TextChoices):
        SIGNATURE = "SIGNATURE", "Signature"
        INITIAL = "INITIAL", "Initial"

    recipient = models.ForeignKey(
        EnvelopeRecipient, on_delete=models.PROTECT, related_name="signatures"
    )
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signatures",
    )
    signature_type = models.CharField(max_length=16, choices=Type.choices)
    purpose = models.CharField(max_length=16, choices=Purpose.choices, default=Purpose.SIGNATURE)
    storage_key = models.CharField(max_length=512, blank=True)
    text_representation = models.CharField(max_length=180, blank=True)


class SigningSession(UUIDModel):
    envelope = models.ForeignKey(
        Envelope, on_delete=models.CASCADE, related_name="signing_sessions"
    )
    recipient = models.ForeignKey(
        EnvelopeRecipient, on_delete=models.CASCADE, related_name="signing_sessions"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True)


class Contact(UUIDModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="contacts"
    )
    name = models.CharField(max_length=180)
    email = models.EmailField()
    company = models.CharField(max_length=180, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    title = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "email"), name="unique_org_contact_email"
            )
        ]


class Template(UUIDModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="templates"
    )
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name="templates")
    folder = models.ForeignKey(
        Folder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="templates",
        limit_choices_to={"kind": Folder.Kind.TEMPLATE},
    )
    name = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    usage_count = models.PositiveIntegerField(default=0)


class TemplateDocument(UUIDModel):
    template = models.ForeignKey(
        Template, on_delete=models.CASCADE, related_name="template_documents"
    )
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="template_uses")
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=("template", "position"), name="unique_template_doc_position"
            )
        ]


class TemplateRecipient(UUIDModel):
    template = models.ForeignKey(
        Template, on_delete=models.CASCADE, related_name="template_recipients"
    )
    role_name = models.CharField(max_length=120)
    recipient_role = models.CharField(
        max_length=16,
        choices=EnvelopeRecipient.Role.choices,
        default=EnvelopeRecipient.Role.SIGNER,
    )
    routing_order = models.PositiveSmallIntegerField(default=1)
    color = models.CharField(max_length=7, default="#7c5ce7")

    class Meta:
        ordering = ("routing_order", "created_at")


class TemplateField(UUIDModel):
    template_document = models.ForeignKey(
        TemplateDocument, on_delete=models.CASCADE, related_name="fields"
    )
    template_recipient = models.ForeignKey(
        TemplateRecipient, on_delete=models.CASCADE, related_name="fields"
    )
    field_type = models.CharField(max_length=16, choices=DocumentField.Type.choices)
    page_number = models.PositiveIntegerField()
    x = models.DecimalField(
        max_digits=8,
        decimal_places=7,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    y = models.DecimalField(
        max_digits=8,
        decimal_places=7,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    width = models.DecimalField(
        max_digits=8,
        decimal_places=7,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    height = models.DecimalField(
        max_digits=8,
        decimal_places=7,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    required = models.BooleanField(default=True)
    label = models.CharField(max_length=180, blank=True)
    validation = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=list, blank=True)


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="audit_events"
    )
    envelope = models.ForeignKey(Envelope, on_delete=models.PROTECT, related_name="audit_events")
    event_type = models.CharField(max_length=64, db_index=True)
    actor_type = models.CharField(max_length=24)
    actor_id = models.CharField(max_length=64, blank=True)
    recipient = models.ForeignKey(
        EnvelopeRecipient,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="audit_events",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("timestamp", "id")
        indexes = [
            models.Index(
                fields=("organization", "envelope", "timestamp"),
                name="audit_envelope_idx",
            )
        ]


class Notification(UUIDModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="notifications"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    body = models.TextField(blank=True)
    read_at = models.DateTimeField(null=True, blank=True)


class OutboundEmail(UUIDModel):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        PROCESSING = "PROCESSING", "Processing"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    class Type(models.TextChoices):
        SIGNING_INVITATION = "SIGNING_INVITATION", "Signing invitation"
        REMINDER = "REMINDER", "Reminder"
        COMPLETION = "COMPLETION", "Completion"
        DECLINED = "DECLINED", "Declined"
        VOIDED = "VOIDED", "Voided"
        EXPIRATION_WARNING = "EXPIRATION_WARNING", "Expiration warning"
        EXPIRED = "EXPIRED", "Expired"
        ORGANIZATION_INVITE = "ORGANIZATION_INVITE", "Organization invitation"
        EMAIL_VERIFICATION = "EMAIL_VERIFICATION", "Email verification"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="outbound_emails"
    )
    envelope = models.ForeignKey(
        Envelope, null=True, blank=True, on_delete=models.CASCADE, related_name="outbound_emails"
    )
    recipient = models.ForeignKey(
        EnvelopeRecipient,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outbound_emails",
    )
    message_type = models.CharField(max_length=32, choices=Type.choices)
    to_email = models.EmailField()
    subject = models.CharField(max_length=240)
    text_body = models.TextField()
    html_body = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
