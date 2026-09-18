"""Serve only recognized media after checking its owning document."""
import re
import io
from .pdf_storage import read_pdf, MAGIC
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.db.models import Q

from .access import can_manage, can_view, is_read_only_user, visible_logs
from .models import Contract


def protected_media(request, path):
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise Http404
    name = target.relative_to(root).as_posix()
    owners = Contract.objects.filter(
        Q(file=name) | Q(versions__file=name) | Q(seal_image=name)
    ).distinct()
    # Legacy QR images are generated outside FileFields.
    qr = re.fullmatch(r'seals/qr_(\d+)\.png', name)
    if qr:
        owners = Contract.objects.filter(pk=int(qr.group(1)))
    # The shared blank seal is public site branding, not a document-derived seal.
    allowed = name == 'seals/default_seal.png' or any(
        can_view(request.user, contract)
        and (not is_read_only_user(request.user) or contract.is_public)
        or (contract.seal_image.name == name and can_manage(request.user, contract))
        for contract in owners
    )
    if not allowed and request.user.is_authenticated:
        allowed = visible_logs(request.user).filter(evidence_file=name).exists()
    if not allowed and name.startswith('verification_previews/'):
        token = request.session.get('verify_preview_token')
        allowed = bool(token and name == f'verification_previews/{token}.pdf')
    if not allowed:
        raise Http404
    with target.open('rb') as probe:
        is_encrypted = probe.read(len(MAGIC)) == MAGIC
    response = FileResponse(io.BytesIO(read_pdf(target)), content_type='application/pdf') if target.suffix.lower() == '.pdf' or is_encrypted else FileResponse(target.open('rb'))
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
