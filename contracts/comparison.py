"""Authorized visual revision comparison with a bounded text summary."""
import difflib
import hashlib

import fitz
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache

from .access import view_document_access
from .models import Contract
from .pdf_storage import read_pdf, PDFDecryptionError
from .utils import log_activity


def describe(version):
    data = read_pdf(version.file.path)
    with fitz.open(stream=data, filetype='pdf') as pdf:
        lines, remaining, limited = [], 60000, pdf.page_count > 100
        for page in range(min(pdf.page_count, 100)):
            text = pdf[page].get_text()
            if len(text) > remaining:
                limited = True
            lines.extend(line.strip() for line in text[:remaining].splitlines() if line.strip())
            remaining -= len(text)
            if remaining <= 0:
                limited = True
                break
        return {'version': version, 'pages': pdf.page_count, 'size': len(data),
                'digest': hashlib.sha256(data).hexdigest(), 'lines': lines,
                'limited': limited}


@login_required
@view_document_access
@never_cache
def compare_revisions(request, contract_id):
    contract = get_object_or_404(Contract, pk=contract_id, is_trashed=False)
    versions = list(contract.versions.exclude(file='').select_related('created_by').order_by('-version_number'))
    context = {'contract': contract, 'versions': versions}
    if len(versions) >= 2:
        def select(name, default):
            try:
                number = int(request.GET.get(name, default.version_number))
            except (ValueError, TypeError):
                raise Http404
            return next((v for v in versions if v.version_number == number), None)
        left, right = select('left', versions[1]), select('right', versions[0])
        if left is None or right is None:
            raise Http404
        context.update(left_version=left, right_version=right)
        try:
            before, after = describe(left), describe(right)
            differences = []
            for tag, a, b, c, d in difflib.SequenceMatcher(None, before['lines'], after['lines']).get_opcodes():
                if tag != 'equal':
                    differences.append({'kind': {'replace': 'Changed', 'delete': 'Removed', 'insert': 'Added'}[tag],
                                        'before': '\n'.join(before['lines'][a:b]),
                                        'after': '\n'.join(after['lines'][c:d])})
            context.update(before=before, after=after, differences=differences,
                           identical=before['digest'] == after['digest'],
                           limited=before['limited'] or after['limited'],
                           no_text=not before['lines'] or not after['lines'])
            log_activity(request, 'viewed', contract=contract, version_number=right.version_number,
                         note=f'Compared revisions v{left.version_number} and v{right.version_number}')
        except (OSError, PDFDecryptionError, RuntimeError, ValueError):
            context['error'] = 'One of these versions could not be opened. Contact staff to check the stored document.'
    return render(request, 'comparison.html', context)
