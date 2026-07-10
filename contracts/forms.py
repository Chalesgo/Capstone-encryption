from django import forms
from .models import Contract

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