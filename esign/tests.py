import re
import tempfile

from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from .envelope_service import clone_envelope, create_folder, move_envelopes
from .models import (
    Document,
    DocumentField,
    Envelope,
    EnvelopeDocument,
    EnvelopeRecipient,
    Folder,
    Organization,
    OrganizationMembership,
    OutboundEmail,
    User,
)
from .organization_service import accept_invitation, change_member_role, invite_member
from .pdf import simple_pdf


class AccountRegistrationTests(TestCase):
    def test_signup_atomically_creates_owner_membership_and_session(self):
        response = self.client.post(
            reverse("signup"),
            {
                "full_name": "Alex Morgan",
                "organization_name": "Northstar Legal",
                "email": "alex@example.com",
                "password1": "Correct-Horse-Cloud-91",
                "password2": "Correct-Horse-Cloud-91",
            },
        )
        self.assertRedirects(response, reverse("dashboard"))
        user = User.objects.get(email="alex@example.com")
        membership = OrganizationMembership.objects.get(user=user)
        self.assertEqual(membership.role, OrganizationMembership.Role.OWNER)
        self.assertEqual(membership.organization.name, "Northstar Legal")
        self.assertEqual(
            self.client.session["active_organization_id"],
            str(membership.organization_id),
        )


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            password="test-password-123",
        )
        self.other_user = User.objects.create_user(
            username="sarah@example.com",
            email="sarah@example.com",
            password="test-password-123",
        )
        self.organization = Organization.objects.create(name="Northstar", slug="northstar")
        self.other_organization = Organization.objects.create(name="Other", slug="other")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.user,
            role=OrganizationMembership.Role.OWNER,
        )
        OrganizationMembership.objects.create(
            organization=self.other_organization,
            user=self.other_user,
            role=OrganizationMembership.Role.OWNER,
        )
        Envelope.objects.create(
            organization=self.organization,
            sender=self.user,
            subject="Visible agreement",
        )
        Envelope.objects.create(
            organization=self.other_organization,
            sender=self.other_user,
            subject="Private agreement",
        )

    def test_dashboard_only_renders_active_tenant_data(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Visible agreement")
        self.assertNotContains(response, "Private agreement")

    def test_anonymous_dashboard_redirects_to_login(self):
        response = Client().get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard')}")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CompleteSigningFlowTests(TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(PRIVATE_STORAGE_ROOT=self.storage.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.storage.cleanup)
        self.user = User.objects.create_user(
            username="sender@example.com",
            email="sender@example.com",
            password="a-long-test-password",
            first_name="Alex",
        )
        self.organization = Organization.objects.create(name="Northstar", slug="northstar-flow")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.user,
            role=OrganizationMembership.Role.OWNER,
        )
        self.client.force_login(self.user)

    def test_sender_can_upload_place_send_and_recipient_can_complete(self):
        create_response = self.client.post(reverse("envelope_create"))
        envelope = Envelope.objects.get(organization=self.organization)
        self.assertRedirects(create_response, reverse("envelope_edit", args=(envelope.id,)))

        upload = SimpleUploadedFile(
            "agreement.pdf",
            simple_pdf(["Development agreement", "Please review and sign below."]),
            content_type="application/pdf",
        )
        self.client.post(reverse("document_upload", args=(envelope.id,)), {"documents": upload})
        self.client.post(
            reverse("recipient_create", args=(envelope.id,)),
            {
                "name": "Jane Doe",
                "email": "jane@example.com",
                "role": "SIGNER",
                "routing_order": 1,
            },
        )
        document = envelope.envelope_documents.get()
        recipient = envelope.recipients.get()
        self.client.post(
            reverse("envelope_editor", args=(envelope.id,)),
            {
                "envelope_document": document.id,
                "recipient": recipient.id,
                "field_type": "SIGNATURE",
                "page_number": 1,
                "x": "0.55",
                "y": "0.75",
                "width": "0.22",
                "height": "0.06",
                "required": "on",
                "label": "Signature",
            },
        )
        send_response = self.client.post(reverse("envelope_send", args=(envelope.id,)))
        self.assertRedirects(send_response, reverse("envelope_detail", args=(envelope.id,)))
        self.assertEqual(len(mail.outbox), 1)
        token = re.search(r"/sign/([^/]+)/", mail.outbox[0].body).group(1)

        anonymous = Client()
        self.assertEqual(anonymous.get(reverse("signing", args=(token,))).status_code, 200)
        finish = anonymous.post(
            reverse("signing", args=(token,)),
            {"signature_text": "Jane Doe", "idempotency_key": "completion-1"},
        )
        self.assertEqual(finish.status_code, 200)
        envelope.refresh_from_db()
        self.assertEqual(envelope.status, Envelope.Status.COMPLETED)
        self.assertTrue(envelope.certificate_storage_key)
        self.assertTrue(envelope.audit_events.filter(event_type="ENVELOPE_COMPLETED").exists())
        self.assertEqual(
            self.client.get(reverse("certificate_download", args=(envelope.id,))).status_code,
            200,
        )


class EnvelopeOrganizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner@example.com", email="owner@example.com", password="safe-password-123"
        )
        self.organization = Organization.objects.create(name="Owner Org", slug="owner-org")
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.user,
            role=OrganizationMembership.Role.OWNER,
        )
        self.envelope = Envelope.objects.create(
            organization=self.organization, sender=self.user, subject="Source agreement"
        )

    def test_folder_move_and_delete_preserves_envelope(self):
        folder = create_folder(
            organization=self.organization,
            owner=self.user,
            name="Legal",
        )
        self.assertEqual(
            move_envelopes(
                organization=self.organization,
                envelope_ids=[self.envelope.id],
                folder=folder,
            ),
            1,
        )
        self.envelope.refresh_from_db()
        self.assertEqual(self.envelope.folder, folder)
        folder.delete()
        self.envelope.refresh_from_db()
        self.assertIsNone(self.envelope.folder)

    def test_cross_tenant_folder_cannot_receive_envelope(self):
        other = Organization.objects.create(name="Other", slug="other-folder-org")
        other_folder = Folder.objects.create(
            organization=other,
            owner=self.user,
            name="Private",
            kind=Folder.Kind.ENVELOPE,
        )
        with self.assertRaises(PermissionDenied):
            move_envelopes(
                organization=self.organization,
                envelope_ids=[self.envelope.id],
                folder=other_folder,
            )

    def test_clone_creates_new_clean_lifecycle_and_copies_configuration(self):
        recipient = EnvelopeRecipient.objects.create(
            envelope=self.envelope,
            name="Jane Doe",
            email="jane@example.com",
            routing_order=2,
        )
        document = Document.objects.create(
            organization=self.organization,
            uploaded_by=self.user,
            filename="contract.pdf",
            mime_type="application/pdf",
            byte_size=20,
            storage_key="documents/source.pdf",
            sha256="a" * 64,
        )
        envelope_document = EnvelopeDocument.objects.create(
            envelope=self.envelope, document=document, position=1
        )
        DocumentField.objects.create(
            envelope_document=envelope_document,
            recipient=recipient,
            field_type=DocumentField.Type.TEXT,
            page_number=1,
            x="0.1",
            y="0.2",
            width="0.3",
            height="0.05",
            value="old response",
            validation={"maxLength": 20},
        )
        clone = clone_envelope(source=self.envelope, sender=self.user)
        self.assertNotEqual(clone.id, self.envelope.id)
        self.assertEqual(clone.status, Envelope.Status.DRAFT)
        self.assertFalse(clone.signing_sessions.exists())
        self.assertEqual(clone.recipients.get().routing_order, 2)
        cloned_field = DocumentField.objects.get(envelope_document__envelope=clone)
        self.assertEqual(cloned_field.value, "")
        self.assertEqual(cloned_field.validation, {"maxLength": 20})
        self.assertTrue(clone.audit_events.filter(event_type="ENVELOPE_CLONED").exists())


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_SEND_SYNCHRONOUSLY=True,
)
class OrganizationInvitationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner-invite@example.com",
            email="owner-invite@example.com",
            password="safe-password-123",
        )
        self.invited = User.objects.create_user(
            username="invited@example.com",
            email="invited@example.com",
            password="safe-password-123",
        )
        self.organization = Organization.objects.create(name="Invite Corp", slug="invite-corp")
        self.owner_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.owner,
            role=OrganizationMembership.Role.OWNER,
        )
        self.factory = RequestFactory()

    def test_invitation_stores_hash_sends_email_and_can_be_accepted_once(self):
        request = self.factory.get("/", HTTP_HOST="testserver")
        invitation = invite_member(
            organization=self.organization,
            invited_by=self.owner,
            email=self.invited.email,
            role=OrganizationMembership.Role.ADMIN,
            request=request,
        )
        self.assertEqual(len(mail.outbox), 1)
        raw_token = re.search(r"/organizations/invitations/([^/]+)/", mail.outbox[0].body).group(1)
        self.assertNotIn(raw_token, invitation.token_hash)
        membership = accept_invitation(raw_token=raw_token, user=self.invited)
        self.assertEqual(membership.role, OrganizationMembership.Role.ADMIN)
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)
        with self.assertRaises(PermissionDenied):
            accept_invitation(raw_token=raw_token, user=self.invited)

    def test_new_invitation_revokes_previous_pending_invitation(self):
        request = self.factory.get("/", HTTP_HOST="testserver")
        first = invite_member(
            organization=self.organization,
            invited_by=self.owner,
            email=self.invited.email,
            role=OrganizationMembership.Role.MEMBER,
            request=request,
        )
        second = invite_member(
            organization=self.organization,
            invited_by=self.owner,
            email=self.invited.email,
            role=OrganizationMembership.Role.ADMIN,
            request=request,
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.revoked_at)
        self.assertIsNone(second.revoked_at)

    def test_last_owner_cannot_be_demoted(self):
        with self.assertRaises(ValidationError):
            change_member_role(
                organization=self.organization,
                acting_membership=self.owner_membership,
                membership=self.owner_membership,
                role=OrganizationMembership.Role.MEMBER,
            )

    def test_outbound_email_record_tracks_actual_send(self):
        request = self.factory.get("/", HTTP_HOST="testserver")
        invite_member(
            organization=self.organization,
            invited_by=self.owner,
            email=self.invited.email,
            role=OrganizationMembership.Role.MEMBER,
            request=request,
        )
        record = OutboundEmail.objects.get()
        self.assertEqual(record.message_type, OutboundEmail.Type.ORGANIZATION_INVITE)
        self.assertEqual(record.status, OutboundEmail.Status.SENT)
        self.assertEqual(record.attempt_count, 1)
        self.assertIsNotNone(record.sent_at)
