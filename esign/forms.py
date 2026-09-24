from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.validators import validate_email

from .models import (
    Contact,
    DocumentField,
    Envelope,
    EnvelopeRecipient,
    Organization,
    User,
)


class SignUpForm(UserCreationForm):
    full_name = forms.CharField(max_length=150, label="Full name")
    organization_name = forms.CharField(max_length=180, label="Organization")
    email = forms.EmailField()

    class Meta:
        model = User
        fields = ("full_name", "email", "organization_name", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        validate_email(email)
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        full_name = self.cleaned_data["full_name"].strip().split(maxsplit=1)
        user.first_name = full_name[0]
        user.last_name = full_name[1] if len(full_name) > 1 else ""
        user.email = self.cleaned_data["email"]
        user.username = user.email
        if commit:
            user.save()
        return user


class EnvelopeForm(forms.ModelForm):
    class Meta:
        model = Envelope
        fields = (
            "subject",
            "message",
            "expires_at",
            "reminder_enabled",
            "reminder_interval_days",
            "allow_comments",
        )
        widgets = {
            "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "message": forms.Textarea(attrs={"rows": 3}),
        }


class RecipientForm(forms.ModelForm):
    class Meta:
        model = EnvelopeRecipient
        fields = ("name", "email", "role", "routing_order", "private_message")


class FieldForm(forms.ModelForm):
    class Meta:
        model = DocumentField
        fields = (
            "recipient",
            "field_type",
            "page_number",
            "x",
            "y",
            "width",
            "height",
            "required",
            "label",
        )


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ("name", "email", "company", "title", "phone")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "title", "company", "phone")


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = (
            "name",
            "display_name",
            "primary_color",
            "default_expiration_days",
            "default_reminder_days",
            "default_subject",
            "default_message",
        )
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "default_message": forms.Textarea(attrs={"rows": 4}),
        }
