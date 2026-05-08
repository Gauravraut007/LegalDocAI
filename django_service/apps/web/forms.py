"""Forms used by the web UI."""
from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

User = get_user_model()


class EmailLoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autofocus": True, "class": "form-control",
                                       "placeholder": "you@example.com"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password",
                                          "class": "form-control"}),
    )


class RegisterForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-control",
                                       "placeholder": "you@example.com"}),
    )
    full_name = forms.CharField(
        max_length=255, required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        min_length=12, max_length=128,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        help_text="Min 12 chars; one upper, one lower, one digit, one symbol.",
    )

    def clean_email(self):
        v = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=v).exists():
            raise forms.ValidationError("Email already registered")
        return v

    def clean_password(self):
        v = self.cleaned_data["password"]
        validate_password(v)
        return v


class PasswordChangeForm(forms.Form):
    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    new_password = forms.CharField(
        min_length=12, max_length=128,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        v = self.cleaned_data["current_password"]
        if not self.user.check_password(v):
            raise forms.ValidationError("Incorrect current password")
        return v

    def clean_new_password(self):
        v = self.cleaned_data["new_password"]
        validate_password(v, user=self.user)
        return v


class ProfileForm(forms.Form):
    full_name = forms.CharField(
        max_length=255, required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    avatar = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={"class": "form-control",
                                     "placeholder": "https://..."}),
    )


class WorkspaceForm(forms.Form):
    name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    plan = forms.ChoiceField(
        choices=[("free", "Free"), ("pro", "Pro"), ("enterprise", "Enterprise")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class InvitationForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    role = forms.ChoiceField(
        choices=[("admin", "Admin"), ("member", "Member"), ("viewer", "Viewer")],
        initial="member",
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class DocumentUploadForm(forms.Form):
    file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
        help_text="PDF, DOCX, DOC, TXT, or image (PNG/JPG/TIFF). Max 200 MB.",
    )
    title = forms.CharField(
        max_length=500, required=False,
        widget=forms.TextInput(attrs={"class": "form-control",
                                       "placeholder": "Optional"}),
    )


class ChatSessionForm(forms.Form):
    title = forms.CharField(
        max_length=500, required=False,
        widget=forms.TextInput(attrs={"class": "form-control",
                                       "placeholder": "e.g. NDA review"}),
    )
    document_ids = forms.MultipleChoiceField(
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
        help_text="Pick at least one ready document. Hold Ctrl / ⌘ to multi-select.",
    )

    def __init__(self, *args, document_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["document_ids"].choices = document_choices or []
