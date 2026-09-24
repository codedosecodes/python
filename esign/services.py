from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils.text import slugify

from .forms import SignUpForm
from .models import Organization, OrganizationMembership


def require_organization(request) -> Organization:
    if request.organization is None:
        raise PermissionDenied("An active organization membership is required.")
    return request.organization


@transaction.atomic
def register_account(form: SignUpForm):
    user = form.save()
    base_slug = slugify(form.cleaned_data["organization_name"]) or "workspace"
    slug = base_slug
    suffix = 2
    while Organization.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    organization = Organization.objects.create(
        name=form.cleaned_data["organization_name"].strip(), slug=slug
    )
    OrganizationMembership.objects.create(
        organization=organization, user=user, role=OrganizationMembership.Role.OWNER
    )
    return user, organization
