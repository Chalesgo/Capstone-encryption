from html import escape
from html.parser import HTMLParser

from django import forms
from .models import Contract, Tutorial


class _TutorialHTMLSanitizer(HTMLParser):
    allowed_tags = {'p', 'br', 'strong', 'b', 'em', 'i', 'ul', 'ol', 'li'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.blocked_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.blocked_depth += 1
            return
        if self.blocked_depth:
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

        return file


from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm

class RegisterForm(UserCreationForm):
    first_name = forms.CharField(max_length=100, required=True)

    class Meta:
        model = User
        fields = ['username', 'first_name', 'password1', 'password2']


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
