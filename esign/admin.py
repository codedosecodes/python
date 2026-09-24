from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    AuditEvent,
    Envelope,
    EnvelopeRecipient,
    Organization,
    OrganizationMembership,
    User,
)


@admin.register(User)
class SignFlowUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Profile", {"fields": ("title", "company", "email_verified_at")}),
    )


admin.site.register(Organization)
admin.site.register(OrganizationMembership)
admin.site.register(Envelope)
admin.site.register(EnvelopeRecipient)
admin.site.register(AuditEvent)
