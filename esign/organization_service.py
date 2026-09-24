from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from .domain import create_signing_token, hash_signing_token
from .email_service import queue_email
from .models import OrganizationInvitation, OrganizationMembership, OutboundEmail, User


def require_role(request, *roles) -> OrganizationMembership:
    membership = request.organization_membership
    if not membership or membership.role not in roles:
        raise PermissionDenied("Your organization role does not permit this action.")
    return membership


@transaction.atomic
def invite_member(*, organization, invited_by, email, role, request) -> OrganizationInvitation:
    email = email.strip().lower()
    if role not in OrganizationMembership.Role.values:
        raise ValidationError("Choose a valid member role.")
    if OrganizationMembership.objects.filter(
        organization=organization, user__email__iexact=email, is_active=True
    ).exists():
        raise ValidationError("This person is already a member.")
    OrganizationInvitation.objects.filter(
        organization=organization,
        email__iexact=email,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())
    raw, digest = create_signing_token()
    invitation = OrganizationInvitation.objects.create(
        organization=organization,
        invited_by=invited_by,
        email=email,
        role=role,
        token_hash=digest,
        expires_at=timezone.now() + timedelta(days=7),
    )
    link = request.build_absolute_uri(reverse("organization_invitation", args=(raw,)))
    queue_email(
        organization=organization,
        message_type=OutboundEmail.Type.ORGANIZATION_INVITE,
        to_email=email,
        subject=f"Join {organization.name} on SignFlow",
        text_body=f"{invited_by.get_full_name() or invited_by.email} invited you to join {organization.name}. Accept within 7 days: {link}",
        html_body=f'<p>You were invited to join <strong>{escape(organization.name)}</strong>.</p><p><a href="{escape(link)}">Accept invitation</a></p>',
    )
    return invitation


def resolve_invitation(raw_token: str, *, lock=False) -> OrganizationInvitation:
    queryset = OrganizationInvitation.objects.select_related("organization", "invited_by")
    if lock:
        queryset = queryset.select_for_update()
    invitation = queryset.filter(token_hash=hash_signing_token(raw_token)).first()
    if not invitation:
        raise PermissionDenied("This invitation is invalid.")
    if invitation.revoked_at:
        raise PermissionDenied("This invitation was revoked.")
    if invitation.accepted_at:
        raise PermissionDenied("This invitation has already been accepted.")
    if invitation.expires_at <= timezone.now():
        raise PermissionDenied("This invitation has expired.")
    return invitation


@transaction.atomic
def accept_invitation(*, raw_token, user: User) -> OrganizationMembership:
    invitation = resolve_invitation(raw_token, lock=True)
    if user.email.lower() != invitation.email.lower():
        raise PermissionDenied("Sign in using the invited email address.")
    membership, _ = OrganizationMembership.objects.update_or_create(
        organization=invitation.organization,
        user=user,
        defaults={"role": invitation.role, "is_active": True},
    )
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=("accepted_at", "updated_at"))
    return membership


@transaction.atomic
def change_member_role(*, organization, acting_membership, membership, role):
    if acting_membership.role != OrganizationMembership.Role.OWNER:
        raise PermissionDenied("Only owners can change member roles.")
    if (
        membership.organization_id != organization.id
        or role not in OrganizationMembership.Role.values
    ):
        raise PermissionDenied("Membership is unavailable.")
    if membership.role == OrganizationMembership.Role.OWNER and role != membership.role:
        owners = organization.memberships.filter(
            role=OrganizationMembership.Role.OWNER, is_active=True
        ).count()
        if owners <= 1:
            raise ValidationError("An organization must retain at least one owner.")
    membership.role = role
    membership.save(update_fields=("role", "updated_at"))
    return membership
