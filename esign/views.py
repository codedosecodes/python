import csv
import json
import uuid
import zipfile
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, TemplateView

from .envelope_service import (
    clone_envelope,
    correct_recipient,
    create_folder,
    delete_folder,
    move_envelopes,
    rename_folder,
    restore_envelopes,
    soft_delete_envelopes,
)
from .forms import (
    ContactForm,
    EnvelopeForm,
    FieldForm,
    OrganizationForm,
    ProfileForm,
    RecipientForm,
    SignUpForm,
)
from .models import (
    Contact,
    DocumentField,
    Envelope,
    EnvelopeRecipient,
    Folder,
    Notification,
    OrganizationInvitation,
    OrganizationMembership,
)
from .organization_service import (
    accept_invitation,
    change_member_role,
    invite_member,
    require_role,
    resolve_invitation,
)
from .services import register_account, require_organization
from .storage import LocalPrivateStorage
from .template_service import (
    create_template_from_envelope,
    duplicate_template,
    get_template_for_member,
    instantiate_template,
)
from .workflows import (
    ACTIVE_STATUSES,
    add_document,
    add_recipient,
    audit,
    create_draft,
    decline_envelope,
    finish_signing,
    get_envelope_for_member,
    mark_viewed,
    remove_document,
    send_envelope,
    send_reminders,
    void_envelope,
)


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        user, organization = register_account(form)
        self.object = user
        login(self.request, user)
        self.request.session["active_organization_id"] = str(organization.id)
        return HttpResponseRedirect(self.get_success_url())


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "esign/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        envelopes = Envelope.objects.for_organization(organization)
        counts = {
            row["status"]: row["total"]
            for row in envelopes.values("status").annotate(total=Count("id"))
        }
        context.update(
            {
                "organization": organization,
                "recent_envelopes": envelopes.select_related("sender")
                .prefetch_related("recipients")
                .order_by("-updated_at")[:6],
                "waiting_count": sum(
                    counts.get(status, 0)
                    for status in (
                        Envelope.Status.SENT,
                        Envelope.Status.DELIVERED,
                        Envelope.Status.VIEWED,
                        Envelope.Status.PARTIALLY_COMPLETED,
                    )
                ),
                "action_count": envelopes.filter(status=Envelope.Status.DRAFT).count(),
                "expiring_count": envelopes.filter(expires_at__isnull=False)
                .exclude(
                    status__in=(
                        Envelope.Status.COMPLETED,
                        Envelope.Status.VOIDED,
                        Envelope.Status.EXPIRED,
                    )
                )
                .count(),
                "completed_count": counts.get(Envelope.Status.COMPLETED, 0),
                "unread_notifications": self.request.user.notifications.filter(
                    organization=organization, read_at__isnull=True
                ).count(),
            }
        )
        return context


class GlobalSearchView(LoginRequiredMixin, View):
    def get(self, request):
        organization = require_organization(request)
        query = request.GET.get("q", "").strip()
        results = {"envelopes": [], "templates": [], "contacts": [], "documents": []}
        if len(query) >= 2:
            results["envelopes"] = list(
                organization.envelopes.filter(
                    Q(subject__icontains=query)
                    | Q(recipients__name__icontains=query)
                    | Q(recipients__email__icontains=query),
                    deleted_at__isnull=True,
                )
                .distinct()
                .order_by("-updated_at")[:20]
            )
            results["templates"] = list(
                organization.templates.filter(
                    Q(name__icontains=query) | Q(description__icontains=query)
                ).order_by("-updated_at")[:10]
            )
            results["contacts"] = list(
                organization.contacts.filter(
                    Q(name__icontains=query)
                    | Q(email__icontains=query)
                    | Q(company__icontains=query)
                ).order_by("name")[:20]
            )
            results["documents"] = list(
                organization.documents.filter(filename__icontains=query).order_by("-created_at")[
                    :20
                ]
            )
        return render(request, "esign/search.html", {"query": query, **results})


class EnvelopeCreateView(LoginRequiredMixin, View):
    def post(self, request):
        envelope = create_draft(organization=require_organization(request), sender=request.user)
        return redirect("envelope_edit", envelope.id)


class SelfSignCreateView(LoginRequiredMixin, View):
    def post(self, request):
        organization = require_organization(request)
        envelope = create_draft(
            organization=organization,
            sender=request.user,
            subject="Self-signed document",
        )
        EnvelopeRecipient.objects.create(
            envelope=envelope,
            name=request.user.get_full_name() or request.user.email,
            email=request.user.email,
            role=EnvelopeRecipient.Role.SIGNER,
            routing_order=1,
            color="#1769e0",
        )
        audit(envelope, "SELF_SIGN_STARTED", actor_id=request.user.id)
        request.session["self_sign_envelope_id"] = str(envelope.id)
        messages.info(request, "Upload your document, then place your own signing fields.")
        return redirect("envelope_edit", envelope.id)


class EnvelopeEditView(LoginRequiredMixin, View):
    template_name = "esign/envelope_edit.html"

    def get_envelope(self, request, envelope_id):
        return get_envelope_for_member(
            envelope_id, request.user, require_organization(request), draft_only=True
        )

    def get(self, request, envelope_id):
        envelope = self.get_envelope(request, envelope_id)
        return render(
            request,
            self.template_name,
            {
                "envelope": envelope,
                "form": EnvelopeForm(instance=envelope),
                "recipient_form": RecipientForm(),
            },
        )

    def post(self, request, envelope_id):
        envelope = self.get_envelope(request, envelope_id)
        form = EnvelopeForm(request.POST, instance=envelope)
        if form.is_valid():
            form.save()
            messages.success(request, "Draft saved.")
            return redirect("envelope_edit", envelope.id)
        return render(
            request,
            self.template_name,
            {"envelope": envelope, "form": form, "recipient_form": RecipientForm()},
            status=400,
        )


class DocumentUploadView(LoginRequiredMixin, View):
    def post(self, request, envelope_id):
        envelope = get_envelope_for_member(
            envelope_id, request.user, require_organization(request), draft_only=True
        )
        uploads = request.FILES.getlist("documents")
        if not uploads:
            messages.error(request, "Choose at least one PDF.")
        for upload in uploads:
            try:
                add_document(envelope=envelope, uploaded_by=request.user, upload=upload)
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
        return redirect("envelope_edit", envelope.id)


class RecipientCreateView(LoginRequiredMixin, View):
    def post(self, request, envelope_id):
        envelope = get_envelope_for_member(
            envelope_id, request.user, require_organization(request), draft_only=True
        )
        form = RecipientForm(request.POST)
        if form.is_valid():
            add_recipient(envelope=envelope, actor=request.user, **form.cleaned_data)
        else:
            messages.error(request, "Check the recipient name, email, role, and routing order.")
        return redirect("envelope_edit", envelope.id)


class EnvelopeEditorView(LoginRequiredMixin, View):
    template_name = "esign/editor.html"

    def get(self, request, envelope_id):
        envelope = get_envelope_for_member(
            envelope_id, request.user, require_organization(request), draft_only=True
        )
        documents = [
            {
                "id": str(item.id),
                "name": item.document.filename,
                "url": reverse("document_content", args=(envelope.id, item.id, "original")),
            }
            for item in envelope.envelope_documents.select_related("document")
        ]
        fields = [
            {
                "id": str(field.id),
                "documentId": str(field.envelope_document_id),
                "recipientId": str(field.recipient_id),
                "type": field.field_type,
                "page": field.page_number,
                "x": float(field.x),
                "y": float(field.y),
                "width": float(field.width),
                "height": float(field.height),
                "required": field.required,
                "label": field.label,
                "validation": field.validation,
                "options": field.options,
                "color": field.recipient.color,
                "recipientName": field.recipient.name,
            }
            for field in DocumentField.objects.filter(
                envelope_document__envelope=envelope
            ).select_related("recipient")
        ]
        return render(
            request,
            self.template_name,
            {
                "envelope": envelope,
                "field_choices": DocumentField.Type.choices,
                "editor_documents": documents,
                "editor_fields": fields,
            },
        )

    def post(self, request, envelope_id):
        envelope = get_envelope_for_member(
            envelope_id, request.user, require_organization(request), draft_only=True
        )
        form = FieldForm(request.POST)
        allowed_document = envelope.envelope_documents.filter(
            id=request.POST.get("envelope_document")
        ).first()
        allowed_recipient = envelope.recipients.filter(id=request.POST.get("recipient")).first()
        if form.is_valid() and allowed_document and allowed_recipient:
            field = form.save(commit=False)
            field.envelope_document = allowed_document
            field.recipient = allowed_recipient
            field.full_clean()
            field.save()
            return redirect("envelope_editor", envelope.id)
        messages.error(
            request,
            "The field could not be placed. Check its recipient and page position.",
        )
        return redirect("envelope_editor", envelope.id)


class EnvelopeSendView(LoginRequiredMixin, View):
    def post(self, request, envelope_id):
        try:
            links = send_envelope(
                envelope_id=envelope_id,
                actor=request.user,
                organization=require_organization(request),
                request=request,
            )
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
            return redirect("envelope_editor", envelope_id)
        messages.success(request, f"Envelope sent to {len(links)} recipient(s).")
        return redirect("envelope_detail", envelope_id)


class EnvelopeReminderView(LoginRequiredMixin, View):
    def post(self, request, envelope_id):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        try:
            count = send_reminders(envelope=envelope, actor=request.user, request=request)
            messages.success(request, f"Sent {count} secure reminder link(s).")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        return redirect("envelope_detail", envelope.id)


class EnvelopeVoidView(LoginRequiredMixin, View):
    def post(self, request, envelope_id):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        try:
            void_envelope(
                envelope=envelope,
                actor=request.user,
                reason=request.POST.get("reason", ""),
            )
            messages.success(request, "Envelope voided and all signing links revoked.")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        return redirect("envelope_detail", envelope.id)


class FieldAPI(LoginRequiredMixin, View):
    editable = (
        "recipient",
        "field_type",
        "page_number",
        "x",
        "y",
        "width",
        "height",
        "required",
        "label",
        "validation",
        "options",
    )

    def _field(self, request, envelope_id, field_id):
        envelope = get_envelope_for_member(
            envelope_id, request.user, require_organization(request), draft_only=True
        )
        field = (
            DocumentField.objects.select_related("recipient")
            .filter(id=field_id, envelope_document__envelope=envelope)
            .first()
        )
        if field is None:
            raise Http404
        return envelope, field

    def post(self, request, envelope_id, field_id):
        envelope, field = self._field(request, envelope_id, field_id)
        try:
            payload = json.loads(request.body or "{}")
            for name in ("x", "y", "width", "height"):
                if name in payload:
                    value = Decimal(str(payload[name]))
                    if not Decimal("0") <= value <= Decimal("1"):
                        raise ValueError
                    setattr(field, name, value)
            if "page_number" in payload:
                field.page_number = max(1, int(payload["page_number"]))
            if "required" in payload:
                field.required = bool(payload["required"])
            if "label" in payload:
                field.label = str(payload["label"])[:180]
            if "recipient" in payload:
                field.recipient = envelope.recipients.get(id=payload["recipient"])
            if "validation" in payload and isinstance(payload["validation"], dict):
                field.validation = payload["validation"]
            if "options" in payload and isinstance(payload["options"], list):
                field.options = [str(option)[:120] for option in payload["options"][:50]]
            field.full_clean()
            field.save()
        except (
            ValueError,
            InvalidOperation,
            json.JSONDecodeError,
            EnvelopeRecipient.DoesNotExist,
            ValidationError,
        ):
            return HttpResponseBadRequest("Invalid field properties.")
        audit(
            envelope,
            "FIELD_UPDATED",
            actor_id=request.user.id,
            metadata={"field_id": str(field.id)},
        )
        return JsonResponse({"ok": True, "updatedAt": field.updated_at.isoformat()})

    def delete(self, request, envelope_id, field_id):
        envelope, field = self._field(request, envelope_id, field_id)
        identifier = str(field.id)
        field.delete()
        audit(
            envelope,
            "FIELD_REMOVED",
            actor_id=request.user.id,
            metadata={"field_id": identifier},
        )
        return JsonResponse({"ok": True})


class ManageView(LoginRequiredMixin, ListView):
    template_name = "esign/manage.html"
    context_object_name = "envelopes"
    paginate_by = 20

    categories = {
        "all": Q(deleted_at__isnull=True),
        "sent": Q(deleted_at__isnull=True, sender__isnull=False) & ~Q(status=Envelope.Status.DRAFT),
        "drafts": Q(deleted_at__isnull=True, status=Envelope.Status.DRAFT),
        "action-required": Q(deleted_at__isnull=True, status=Envelope.Status.DRAFT),
        "waiting": Q(
            deleted_at__isnull=True,
            status__in=(
                Envelope.Status.SENT,
                Envelope.Status.DELIVERED,
                Envelope.Status.VIEWED,
                Envelope.Status.PARTIALLY_COMPLETED,
            ),
        ),
        "completed": Q(deleted_at__isnull=True, status=Envelope.Status.COMPLETED),
        "declined": Q(deleted_at__isnull=True, status=Envelope.Status.DECLINED),
        "voided": Q(deleted_at__isnull=True, status=Envelope.Status.VOIDED),
        "expired": Q(deleted_at__isnull=True, status=Envelope.Status.EXPIRED),
        "deleted": Q(deleted_at__isnull=False),
    }

    def get_queryset(self):
        organization = require_organization(self.request)
        queryset = (
            Envelope.objects.for_organization(organization)
            .select_related("sender", "folder")
            .prefetch_related("recipients", "envelope_documents__document")
        )
        category = self.request.GET.get("category", "all")
        if category == "expiring":
            queryset = queryset.filter(
                deleted_at__isnull=True,
                expires_at__gte=timezone.now(),
                expires_at__lte=timezone.now() + timedelta(days=7),
            ).exclude(
                status__in=(
                    Envelope.Status.COMPLETED,
                    Envelope.Status.VOIDED,
                    Envelope.Status.DECLINED,
                    Envelope.Status.EXPIRED,
                )
            )
        else:
            queryset = queryset.filter(self.categories.get(category, self.categories["all"]))
        query = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "")
        folder = self.request.GET.get("folder", "")
        sender = self.request.GET.get("sender", "").strip()
        recipient = self.request.GET.get("recipient", "").strip()
        date_from = parse_date(self.request.GET.get("date_from", ""))
        date_to = parse_date(self.request.GET.get("date_to", ""))
        if query:
            queryset = queryset.filter(
                Q(subject__icontains=query)
                | Q(recipients__name__icontains=query)
                | Q(recipients__email__icontains=query)
                | Q(envelope_documents__document__filename__icontains=query)
                | Q(sender__email__icontains=query)
            )
        if status in Envelope.Status.values:
            queryset = queryset.filter(status=status)
        if folder == "none":
            queryset = queryset.filter(folder__isnull=True)
        elif folder:
            queryset = queryset.filter(folder_id=folder)
        if sender:
            queryset = queryset.filter(
                Q(sender__email__icontains=sender)
                | Q(sender__first_name__icontains=sender)
                | Q(sender__last_name__icontains=sender)
            )
        if recipient:
            queryset = queryset.filter(
                Q(recipients__name__icontains=recipient) | Q(recipients__email__icontains=recipient)
            )
        if date_from:
            queryset = queryset.filter(updated_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(updated_at__date__lte=date_to)
        sort = self.request.GET.get("sort", "-updated_at")
        sort_fields = {
            "name": "subject",
            "-name": "-subject",
            "updated": "updated_at",
            "-updated": "-updated_at",
            "sent": "sent_at",
            "-sent": "-sent_at",
            "status": "status",
            "-status": "-status",
        }
        return queryset.distinct().order_by(sort_fields.get(sort, "-updated_at"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        context["folders"] = organization.folders.filter(kind=Folder.Kind.ENVELOPE)
        context["current_category"] = self.request.GET.get("category", "all")
        context["category_counts"] = {
            "drafts": organization.envelopes.filter(
                status=Envelope.Status.DRAFT, deleted_at__isnull=True
            ).count(),
            "waiting": organization.envelopes.filter(
                status__in=(
                    Envelope.Status.SENT,
                    Envelope.Status.DELIVERED,
                    Envelope.Status.VIEWED,
                    Envelope.Status.PARTIALLY_COMPLETED,
                ),
                deleted_at__isnull=True,
            ).count(),
            "completed": organization.envelopes.filter(
                status=Envelope.Status.COMPLETED, deleted_at__isnull=True
            ).count(),
            "deleted": organization.envelopes.filter(deleted_at__isnull=False).count(),
        }
        return context

    def post(self, request):
        organization = require_organization(request)
        envelope_ids = request.POST.getlist("envelope_ids")
        action = request.POST.get("action")
        try:
            if action == "delete":
                count = soft_delete_envelopes(organization=organization, envelope_ids=envelope_ids)
            elif action == "restore":
                count = restore_envelopes(organization=organization, envelope_ids=envelope_ids)
            elif action == "move":
                folder_id = request.POST.get("folder")
                folder = (
                    organization.folders.filter(id=folder_id, kind=Folder.Kind.ENVELOPE).first()
                    if folder_id
                    else None
                )
                count = move_envelopes(
                    organization=organization, envelope_ids=envelope_ids, folder=folder
                )
            else:
                raise ValidationError("Choose a bulk action.")
            messages.success(request, f"Updated {count} envelope(s).")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        return redirect(request.get_full_path())


class FolderView(LoginRequiredMixin, View):
    def post(self, request, folder_id=None):
        organization = require_organization(request)
        try:
            if folder_id:
                folder = organization.folders.filter(
                    id=folder_id, kind=Folder.Kind.ENVELOPE
                ).first()
                if not folder:
                    raise Http404
                if request.POST.get("action") == "delete":
                    delete_folder(folder=folder)
                    messages.success(request, "Folder deleted; envelopes moved to All envelopes.")
                else:
                    rename_folder(folder=folder, name=request.POST.get("name", ""))
                    messages.success(request, "Folder renamed.")
            else:
                create_folder(
                    organization=organization, owner=request.user, name=request.POST.get("name", "")
                )
                messages.success(request, "Folder created.")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        return redirect("manage")


class EnvelopeCloneView(LoginRequiredMixin, View):
    def post(self, request, envelope_id):
        source = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        clone = clone_envelope(source=source, sender=request.user)
        messages.success(request, "A new editable copy was created.")
        return redirect("envelope_edit", clone.id)


class EnvelopeCorrectionView(LoginRequiredMixin, View):
    def get(self, request, envelope_id):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        if envelope.status not in ACTIVE_STATUSES:
            raise PermissionDenied("This envelope cannot be corrected.")
        audit(envelope, "ENVELOPE_CORRECTION_STARTED", actor_id=request.user.id)
        return render(request, "esign/correct_envelope.html", {"envelope": envelope})

    def post(self, request, envelope_id):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        try:
            correct_recipient(
                envelope=envelope,
                actor=request.user,
                recipient_id=request.POST.get("recipient_id"),
                name=request.POST.get("name", ""),
                email=request.POST.get("email", ""),
                routing_order=request.POST.get("routing_order", 1),
            )
            audit(envelope, "ENVELOPE_CORRECTION_COMPLETED", actor_id=request.user.id)
            messages.success(
                request, "Recipient corrected. Send a reminder to issue a replacement link."
            )
        except (ValidationError, ValueError) as error:
            messages.error(request, "; ".join(getattr(error, "messages", [str(error)])))
        return redirect("envelope_correct", envelope.id)


class EnvelopeDetailView(LoginRequiredMixin, View):
    def get(self, request, envelope_id):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        return render(request, "esign/envelope_detail.html", {"envelope": envelope})


class PrivateDocumentView(LoginRequiredMixin, View):
    def get(self, request, envelope_id, document_id, kind="original"):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        item = envelope.envelope_documents.select_related("document").filter(id=document_id).first()
        if item is None:
            raise Http404
        key = item.completed_storage_key if kind == "completed" else item.document.storage_key
        if not key:
            raise Http404
        audit(
            envelope,
            "DOCUMENT_DOWNLOADED",
            actor_id=request.user.id,
            metadata={"kind": kind},
        )
        return FileResponse(
            LocalPrivateStorage().open(key),
            as_attachment=request.GET.get("download") == "1",
            filename=item.document.filename,
            content_type="application/pdf",
        )


class CertificateDownloadView(LoginRequiredMixin, View):
    def get(self, request, envelope_id):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        if not envelope.certificate_storage_key:
            raise Http404
        return FileResponse(
            LocalPrivateStorage().open(envelope.certificate_storage_key),
            as_attachment=True,
            filename=f"{envelope.subject}-certificate.pdf",
            content_type="application/pdf",
        )


class EnvelopeBundleView(LoginRequiredMixin, View):
    def get(self, request, envelope_id):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        output = BytesIO()
        storage = LocalPrivateStorage()
        used_names = set()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, item in enumerate(envelope.envelope_documents.select_related("document"), 1):
                key = item.completed_storage_key or item.document.storage_key
                base = item.document.filename.removesuffix(".pdf")
                name = f"{base}_{'signed' if item.completed_storage_key else 'original'}.pdf"
                if name in used_names:
                    name = f"{index}_{name}"
                used_names.add(name)
                with storage.open(key) as document:
                    archive.writestr(name, document.read())
            if envelope.certificate_storage_key:
                with storage.open(envelope.certificate_storage_key) as certificate:
                    archive.writestr("Certificate_of_Completion.pdf", certificate.read())
        output.seek(0)
        audit(
            envelope,
            "DOCUMENT_DOWNLOADED",
            actor_id=request.user.id,
            metadata={"kind": "bundle"},
        )
        return FileResponse(
            output,
            as_attachment=True,
            filename=f"{envelope.subject}-documents.zip",
            content_type="application/zip",
        )


class SigningView(View):
    def get(self, request, token):
        session = mark_viewed(token)
        return render(
            request,
            "esign/signing.html",
            {
                "signing_session": session,
                "token": token,
                "idempotency_key": uuid.uuid4(),
            },
        )

    def post(self, request, token):
        try:
            envelope = finish_signing(
                raw_token=token,
                values=request.POST,
                signature_text=request.POST.get("signature_text", ""),
                signature_payload=request.POST.get("signature_payload", ""),
                signature_method=request.POST.get("signature_method", "ADOPTED"),
                initials_text=request.POST.get("initials_text", ""),
                initials_payload=request.POST.get("initials_payload", ""),
                idempotency_key=request.POST.get("idempotency_key", ""),
                request=request,
            )
        except ValidationError as error:
            session = mark_viewed(token)
            return render(
                request,
                "esign/signing.html",
                {
                    "signing_session": session,
                    "token": token,
                    "idempotency_key": request.POST.get("idempotency_key"),
                    "errors": error.messages,
                },
                status=400,
            )
        return render(request, "esign/signing_complete.html", {"envelope": envelope})


class SigningDeclineView(View):
    def post(self, request, token):
        envelope = decline_envelope(raw_token=token, reason=request.POST.get("reason", ""))
        return render(request, "esign/signing_declined.html", {"envelope": envelope})


class SigningDocumentView(View):
    def get(self, request, token, document_id):
        session = mark_viewed(token)
        item = (
            session.envelope.envelope_documents.select_related("document")
            .filter(id=document_id)
            .first()
        )
        if item is None:
            raise Http404
        return FileResponse(
            LocalPrivateStorage().open(item.document.storage_key),
            content_type="application/pdf",
            filename=item.document.filename,
        )


class ContactListView(LoginRequiredMixin, View):
    def get(self, request):
        organization = require_organization(request)
        query = request.GET.get("q", "").strip()
        contacts = Contact.objects.filter(organization=organization)
        if query:
            contacts = (
                contacts.filter(name__icontains=query)
                | contacts.filter(email__icontains=query)
                | contacts.filter(company__icontains=query)
            )
        contacts = contacts.order_by(self._sort(request.GET.get("sort")))
        paginator = Paginator(contacts, 50)
        return render(
            request,
            "esign/contacts.html",
            {"contacts": paginator.get_page(request.GET.get("page")), "form": ContactForm()},
        )

    @staticmethod
    def _sort(value):
        return {"email": "email", "company": "company", "-name": "-name"}.get(value, "name")

    def post(self, request):
        organization = require_organization(request)
        form = ContactForm(request.POST)
        if form.is_valid():
            contact = form.save(commit=False)
            contact.organization = organization
            contact.save()
            messages.success(request, "Contact added.")
        else:
            messages.error(request, "Check the contact details.")
        return redirect("contacts")


class ContactImportView(LoginRequiredMixin, View):
    required_headers = {"name", "email"}
    optional_headers = {"company", "title", "phone"}

    def post(self, request):
        organization = require_organization(request)
        upload = request.FILES.get("csv_file")
        if not upload or upload.size > 2 * 1024 * 1024:
            messages.error(request, "Choose a CSV file smaller than 2 MB.")
            return redirect("contacts")
        try:
            decoded = upload.read().decode("utf-8-sig")
            reader = csv.DictReader(StringIO(decoded))
            headers = {header.strip().lower() for header in (reader.fieldnames or [])}
            if not self.required_headers.issubset(headers):
                raise ValidationError("CSV must include name and email headers.")
            created = updated = 0
            errors = []
            with transaction.atomic():
                for row_number, original in enumerate(reader, 2):
                    row = {
                        (key or "").strip().lower(): (value or "").strip()
                        for key, value in original.items()
                    }
                    form = ContactForm(
                        {
                            "name": row.get("name"),
                            "email": row.get("email"),
                            "company": row.get("company", ""),
                            "title": row.get("title", ""),
                            "phone": row.get("phone", ""),
                        }
                    )
                    if not form.is_valid():
                        errors.append(f"Row {row_number}: check name and email.")
                        continue
                    _contact, was_created = Contact.objects.update_or_create(
                        organization=organization,
                        email__iexact=form.cleaned_data["email"],
                        defaults={**form.cleaned_data, "email": form.cleaned_data["email"].lower()},
                    )
                    created += int(was_created)
                    updated += int(not was_created)
                if errors:
                    raise ValidationError(errors[:20])
            messages.success(
                request, f"Imported {created} new and updated {updated} existing contacts."
            )
        except (UnicodeDecodeError, csv.Error, ValidationError) as error:
            messages.error(request, "; ".join(getattr(error, "messages", [str(error)])))
        return redirect("contacts")

    def get(self, request):
        organization = require_organization(request)
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(("name", "email", "company", "title", "phone"))
        for contact in organization.contacts.order_by("name"):
            writer.writerow(
                (contact.name, contact.email, contact.company, contact.title, contact.phone)
            )
        return HttpResponse(
            output.getvalue(),
            content_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="contacts.csv"'},
        )


class ContactDetailView(LoginRequiredMixin, View):
    def post(self, request, contact_id):
        contact = Contact.objects.filter(
            id=contact_id, organization=require_organization(request)
        ).first()
        if contact is None:
            raise Http404
        if request.POST.get("action") == "delete":
            contact.delete()
            messages.success(request, "Contact deleted.")
        else:
            form = ContactForm(request.POST, instance=contact)
            if form.is_valid():
                form.save()
                messages.success(request, "Contact updated.")
            else:
                messages.error(request, "Check the contact details.")
        return redirect("contacts")


class SettingsView(LoginRequiredMixin, View):
    def get(self, request):
        organization = require_organization(request)
        return render(
            request,
            "esign/settings.html",
            {
                "profile_form": ProfileForm(instance=request.user),
                "organization_form": OrganizationForm(instance=organization),
                "membership": request.organization_membership,
            },
        )

    def post(self, request):
        organization = require_organization(request)
        section = request.POST.get("section")
        if section == "profile":
            form = ProfileForm(request.POST, instance=request.user)
        elif section == "organization" and request.organization_membership.role in (
            "OWNER",
            "ADMIN",
        ):
            form = OrganizationForm(request.POST, instance=organization)
        else:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            messages.success(request, "Settings saved.")
        else:
            messages.error(request, "Check the highlighted settings.")
        return redirect("settings")


class NotificationListView(LoginRequiredMixin, View):
    def get(self, request):
        notifications = Notification.objects.filter(
            user=request.user, organization=require_organization(request)
        ).order_by("-created_at")[:100]
        return render(request, "esign/notifications.html", {"notifications": notifications})

    def post(self, request):
        Notification.objects.filter(
            user=request.user,
            organization=require_organization(request),
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        return redirect("notifications")


class OrganizationMembersView(LoginRequiredMixin, View):
    def get(self, request):
        organization = require_organization(request)
        memberships = organization.memberships.select_related("user").order_by("role", "created_at")
        invitations = organization.invitations.filter(
            accepted_at__isnull=True, revoked_at__isnull=True
        ).order_by("-created_at")
        return render(
            request,
            "esign/organization_members.html",
            {"memberships": memberships, "invitations": invitations},
        )

    def post(self, request):
        organization = require_organization(request)
        membership = require_role(
            request, OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN
        )
        action = request.POST.get("action", "invite")
        try:
            if action == "invite":
                invite_member(
                    organization=organization,
                    invited_by=request.user,
                    email=request.POST.get("email", ""),
                    role=request.POST.get("role", OrganizationMembership.Role.MEMBER),
                    request=request,
                )
                messages.success(request, "Organization invitation sent.")
            elif action == "role":
                target = organization.memberships.get(id=request.POST.get("membership_id"))
                change_member_role(
                    organization=organization,
                    acting_membership=membership,
                    membership=target,
                    role=request.POST.get("role", ""),
                )
                messages.success(request, "Member role updated.")
            elif action == "remove":
                target = organization.memberships.get(id=request.POST.get("membership_id"))
                if target.role == OrganizationMembership.Role.OWNER:
                    raise ValidationError("Transfer ownership before removing an owner.")
                target.delete()
                messages.success(request, "Member removed.")
            elif action == "revoke":
                invitation = organization.invitations.get(id=request.POST.get("invitation_id"))
                invitation.revoked_at = timezone.now()
                invitation.save(update_fields=("revoked_at", "updated_at"))
                messages.success(request, "Invitation revoked.")
        except (
            ValidationError,
            OrganizationMembership.DoesNotExist,
            OrganizationInvitation.DoesNotExist,
        ) as error:
            messages.error(request, "; ".join(getattr(error, "messages", [str(error)])))
        return redirect("organization_members")


class OrganizationInvitationView(View):
    def get(self, request, token):
        invitation = resolve_invitation(token)
        return render(
            request,
            "esign/organization_invitation.html",
            {"invitation": invitation, "token": token},
        )

    def post(self, request, token):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        membership = accept_invitation(raw_token=token, user=request.user)
        request.session["active_organization_id"] = str(membership.organization_id)
        messages.success(request, f"You joined {membership.organization.name}.")
        return redirect("dashboard")


class TemplateListView(LoginRequiredMixin, View):
    def get(self, request):
        templates = request.organization.templates.select_related("owner").prefetch_related(
            "template_recipients", "template_documents"
        )
        query = request.GET.get("q", "").strip()
        if query:
            templates = templates.filter(name__icontains=query)
        return render(
            request,
            "esign/templates.html",
            {"templates": templates.order_by("-updated_at")},
        )


class TemplateCreateView(LoginRequiredMixin, View):
    def post(self, request, envelope_id):
        envelope = get_envelope_for_member(envelope_id, request.user, require_organization(request))
        try:
            template = create_template_from_envelope(
                envelope=envelope,
                owner=request.user,
                name=request.POST.get("name", envelope.subject),
                description=request.POST.get("description", envelope.message),
            )
            messages.success(request, "Reusable template created.")
            return redirect("template_detail", template.id)
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect("envelope_detail", envelope.id)


class TemplateDetailView(LoginRequiredMixin, View):
    def get(self, request, template_id):
        template = get_template_for_member(template_id, require_organization(request))
        return render(request, "esign/template_detail.html", {"template": template})

    def post(self, request, template_id):
        template = get_template_for_member(template_id, require_organization(request))
        action = request.POST.get("action")
        if action == "delete":
            template.delete()
            messages.success(request, "Template deleted.")
            return redirect("templates")
        if action == "duplicate":
            copy = duplicate_template(template=template, owner=request.user)
            messages.success(request, "Template duplicated.")
            return redirect("template_detail", copy.id)
        assignments = {
            str(role.id): {
                "name": request.POST.get(f"name_{role.id}", ""),
                "email": request.POST.get(f"email_{role.id}", ""),
            }
            for role in template.template_recipients.all()
        }
        try:
            envelope = instantiate_template(
                template=template, sender=request.user, assignments=assignments
            )
            messages.success(request, "Draft created from template.")
            return redirect("envelope_edit", envelope.id)
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect("template_detail", template.id)


@require_POST
def recipient_delete(request, envelope_id, recipient_id):
    if not request.user.is_authenticated:
        raise PermissionDenied
    envelope = get_envelope_for_member(
        envelope_id, request.user, require_organization(request), draft_only=True
    )
    recipient = envelope.recipients.filter(id=recipient_id).first()
    if recipient:
        recipient.delete()
    return redirect("envelope_edit", envelope.id)


@require_POST
def document_delete(request, envelope_id, document_id):
    if not request.user.is_authenticated:
        raise PermissionDenied
    envelope = get_envelope_for_member(
        envelope_id, request.user, require_organization(request), draft_only=True
    )
    remove_document(envelope=envelope, envelope_document_id=document_id, actor=request.user)
    return redirect("envelope_edit", envelope.id)


@require_POST
def field_delete(request, envelope_id, field_id):
    if not request.user.is_authenticated:
        raise PermissionDenied
    envelope = get_envelope_for_member(
        envelope_id, request.user, require_organization(request), draft_only=True
    )
    DocumentField.objects.filter(id=field_id, envelope_document__envelope=envelope).delete()
    return redirect("envelope_editor", envelope.id)
