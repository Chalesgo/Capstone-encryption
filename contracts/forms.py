from html import escape
from html.parser import HTMLParser

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
import fitz
from .models import Contract, Tutorial
from .signals import (
    failed_login_count,
    is_account_locked,
    lockout_remaining_seconds,
    record_failed_login,
)


class SealGuardAuthenticationForm(AuthenticationForm):
    """Audit invalid submissions that never reach Django authentication."""

    def clean(self):
        submitted_username = self.data.get('username', '') or self.data.get('email', '')
        if is_account_locked(submitted_username):
            record_failed_login(self.request, submitted_username)
            self.lockout_seconds_remaining = lockout_remaining_seconds(submitted_username)
            self.login_error_message = (
                'This account is temporarily locked. Please try again in '
                f'{self.lockout_seconds_remaining} seconds.'
            )
            raise ValidationError(self.error_messages['invalid_login'], code='invalid_login')
        try:
            cleaned_data = super().clean()
        except ValidationError:
            if not getattr(self.request, '_sealguard_login_failed_recorded', False):
                record_failed_login(
                    self.request,
                    self.data.get('username', '') or self.data.get('email', ''),
                )
            self._set_remaining_attempts_message(submitted_username)
            raise

        if not getattr(self.request, '_sealguard_login_failed_recorded', False):
            username = cleaned_data.get('username', '') if cleaned_data else ''
            if not getattr(self, 'user_cache', None):
                record_failed_login(self.request, username)
        return cleaned_data

    def _set_remaining_attempts_message(self, username):
        from django.contrib.auth.models import User

        if not User.objects.filter(username__iexact=username).exists():
            return
        remaining = max(settings.LOGIN_FAILURE_THRESHOLD - failed_login_count(username), 0)
        if remaining:
            self.login_error_message = (
                'The username or password is incorrect. Please try again. '
                f'You have {remaining} attempts left.'
            )
        else:
            self.lockout_seconds_remaining = lockout_remaining_seconds(username)
            self.login_error_message = (
                'This account is temporarily locked. Please try again in '
                f'{self.lockout_seconds_remaining} seconds.'
            )


class _TutorialHTMLSanitizer(HTMLParser):
    allowed_tags = {'p', 'br', 'strong', 'b', 'em', 'i', 'ul', 'ol', 'li'}
    inline_icons = {
        'help': 'Help', 'shield': 'Shield', 'file': 'Document', 'upload': 'Upload',
        'lock': 'Encryption', 'search': 'Search', 'folder': 'Folder',
        'dashboard': 'Dashboard', 'eye': 'View', 'download': 'Download',
        'history': 'History', 'warning': 'Warning', 'check': 'Check', 'user': 'User',
        'plus': 'Add', 'trash': 'Trash', 'restore': 'Restore',
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.blocked_depth = 0
        self.inline_icon_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.blocked_depth += 1
            return
        if self.blocked_depth:
            return
        if self.inline_icon_depth:
            self.inline_icon_depth += 1
            return
        if tag == 'span':
            icon_name = dict(attrs).get('data-icon')
            if icon_name in self.inline_icons:
                label = escape(self.inline_icons[icon_name], quote=True)
                self.output.append(
                    f'<span class="tutorial-inline-icon" data-icon="{icon_name}" '
                    f'role="img" aria-label="{label}" contenteditable="false">'
                    f'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" '
                    f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
                    f'stroke-linejoin="round"><use href="#tutorial-icon-{icon_name}"></use></svg></span>'
                )
                self.inline_icon_depth = 1
            return
        if tag in self.allowed_tags:
            self.output.append(f'<{tag}>')

    def handle_startendtag(self, tag, attrs):
        if self.blocked_depth:
            return
        if tag == 'br':
            self.output.append('<br>')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.blocked_depth:
            self.blocked_depth -= 1
            return
        if self.blocked_depth:
            return
        if self.inline_icon_depth:
            self.inline_icon_depth -= 1
            return
        if tag in self.allowed_tags and tag != 'br':
            self.output.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.blocked_depth:
            self.output.append(escape(data))


def sanitize_tutorial_html(value):
    sanitizer = _TutorialHTMLSanitizer()
    sanitizer.feed(value or '')
    sanitizer.close()
    return ''.join(sanitizer.output).strip()

class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ['title', 'file']

    def clean_file(self):
        file = self.cleaned_data.get('file')
        if not file:
            return file

        error_message = (
            "This file is not a PDF. Please convert your document to PDF format before uploading."
        )

        if file.size > settings.MAX_UPLOAD_SIZE:
            raise forms.ValidationError(
                f"This file is too large. The maximum allowed size is "
                f"{settings.MAX_UPLOAD_SIZE // (1024 * 1024)} MB."
            )

        # Check 1: extension
        if not file.name.lower().endswith('.pdf'):
            raise forms.ValidationError(error_message)

        # Check 2: browser-reported MIME type (weak on its own, but cheap to check)
        content_type = getattr(file, 'content_type', '')
        if content_type and content_type != 'application/pdf':
            raise forms.ValidationError(error_message)

        # Check 3: actual file signature — PDFs always start with "%PDF-"
        # This is the check that actually can't be spoofed by renaming a file.
        try:
            file.seek(0)
            header = file.read(5)
            file.seek(0)
        except Exception:
            raise forms.ValidationError("Unable to read the uploaded file. Please try again.")

        if header != b'%PDF-':
            raise forms.ValidationError(error_message)

        try:
            file.seek(0)
            pdf_data = file.read()
            with fitz.open(stream=pdf_data, filetype='pdf') as document:
                if document.is_encrypted:
                    raise forms.ValidationError(
                        'Password-protected PDFs are not supported. Remove the password and try again.'
                    )
        except forms.ValidationError:
            raise
        except Exception:
            raise forms.ValidationError(error_message)
        finally:
            file.seek(0)

        return file


class TutorialForm(forms.ModelForm):
    class Meta:
        model = Tutorial
        fields = ['title', 'summary', 'icon', 'content']

    def clean_content(self):
        content = sanitize_tutorial_html(self.cleaned_data.get('content', ''))
        visible_text = content.replace('<br>', ' ')
        for tag in _TutorialHTMLSanitizer.allowed_tags:
            visible_text = visible_text.replace(f'<{tag}>', '').replace(f'</{tag}>', '')
        if not visible_text.strip():
            raise forms.ValidationError('Add at least one tutorial instruction.')
        return content
