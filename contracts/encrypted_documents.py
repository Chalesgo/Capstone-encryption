"""Portable encrypted downloads bound to an existing SealGuard document."""
import hashlib
import io
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.http import FileResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .access import can_view
from .models import Contract, ContractVersion, DocumentAccessRequest
from .pdf_storage import MAGIC, decrypt_pdf_bytes, PDFDecryptionError

PACKAGE_MAGIC = b'SEALGUARD-PACKAGE-1\n'
SALT = 'sealguard.encrypted-document.v1'


def package_bytes(contract, file, version=None):
    ciphertext = Path(file.path).read_bytes()
    if not ciphertext.startswith(MAGIC):
        raise PDFDecryptionError('Document is not encrypted in storage.')
    metadata = signing.dumps({'contract': contract.pk, 'version': version.pk if version else None,
                              'file': file.name, 'sha256': hashlib.sha256(ciphertext).hexdigest()}, salt=SALT)
    return PACKAGE_MAGIC + metadata.encode('ascii') + b'\n' + ciphertext


def package_response(contract, file, filename, version=None):
    response = FileResponse(io.BytesIO(package_bytes(contract, file, version)),
                            as_attachment=True, content_type='application/octet-stream',
                            filename=str(Path(filename).with_suffix('.sgpdf')))
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@never_cache
@require_http_methods(['GET', 'POST'])
def open_encrypted_document(request):
    error = ''
    if request.method == 'POST':
        upload = request.FILES.get('document')
        limit = settings.MAX_UPLOAD_SIZE + 65536
        try:
            if not upload or upload.size > limit:
                raise ValueError('Choose an encrypted SealGuard document within the upload limit.')
            data = upload.read(limit + 1)
            if not data.startswith(PACKAGE_MAGIC):
                raise ValueError('Choose a .sgpdf file downloaded from SealGuard.')
            header, ciphertext = data[len(PACKAGE_MAGIC):].split(b'\n', 1)
            metadata = signing.loads(header.decode('ascii'), salt=SALT)
            if hashlib.sha256(ciphertext).hexdigest() != metadata['sha256']:
                raise ValueError('This encrypted document has been changed or damaged.')
            contract = Contract.objects.get(pk=metadata['contract'], is_trashed=False)
            version = (ContractVersion.objects.get(pk=metadata['version'], contract=contract)
                       if metadata['version'] else None)
            file = version.file if version else contract.file
            if file.name != metadata['file']:
                raise ValueError('This document version is no longer available.')
            target = None
            if can_view(request.user, contract):
                target = reverse('preview_contract_version', args=[version.pk]) if version else reverse('preview_contract', args=[contract.pk])
            else:
                from .approval_views import _approved, _verified_in_session
                ids = request.session.get('document_access_requests', {}).values()
                for entry in DocumentAccessRequest.objects.filter(pk__in=ids, link__contract=contract).select_related('link__contract'):
                    if entry.approved_file == file.name and _approved(entry) and _verified_in_session(request, entry):
                        target = reverse('approved_document_pdf', args=[entry.link.token])
                        break
            if not target:
                raise ValueError('You do not have access. Sign in with an authorized account or complete staff approval in this browser.')
            # Check the exact archived ciphertext; a replaced version cannot be
            # substituted for the uploaded package. Only decrypt after authorization.
            if hashlib.sha256(Path(file.path).read_bytes()).hexdigest() != metadata['sha256']:
                raise ValueError('This document version has changed. Contact staff for a new download.')
            decrypt_pdf_bytes(ciphertext)
            return redirect(target)
        except (ValueError, KeyError, UnicodeError, signing.BadSignature, OSError,
                Contract.DoesNotExist, ContractVersion.DoesNotExist):
            error = 'Unable to open this file. It must be an unchanged SealGuard download, and you must currently have permission to view its document version.'
    return render(request, 'access/open_encrypted.html', {'error': error})
