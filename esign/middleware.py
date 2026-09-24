from dataclasses import dataclass

from .models import Organization, OrganizationMembership


@dataclass(frozen=True, slots=True)
class OrganizationContext:
    organization: Organization
    membership: OrganizationMembership


class ActiveOrganizationMiddleware:
    """Resolve an authenticated user's active tenant from a verified membership."""

    session_key = "active_organization_id"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organization = None
        request.organization_membership = None
        if request.user.is_authenticated:
            memberships = OrganizationMembership.objects.select_related("organization").filter(
                user=request.user, is_active=True
            )
            requested_id = request.session.get(self.session_key)
            membership = (
                memberships.filter(organization_id=requested_id).first() if requested_id else None
            )
            membership = membership or memberships.order_by("created_at").first()
            if membership:
                request.organization = membership.organization
                request.organization_membership = membership
                request.session[self.session_key] = str(membership.organization_id)
        return self.get_response(request)
