"""Authorized visual revision comparison with a bounded text summary."""
import difflib
import hashlib

import fitz
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache

from .access import view_document_access, visible_logs
from .models import Contract
from .pdf_storage import read_pdf, PDFDecryptionError
from .utils import log_activity


def extract_lines(data):
    """Text lines from the first 100 pages / 60,000 characters of PDF bytes."""
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
        return pdf.page_count, lines, limited


def describe(version):
    data = read_pdf(version.file.path)
    pages, lines, limited = extract_lines(data)
    return {'version': version, 'pages': pages, 'size': len(data),
            'digest': hashlib.sha256(data).hexdigest(), 'lines': lines,
            'limited': limited}


def _lines_missing_from(lines, other_lines, cap=200):
    """Lines of one document whose text does not appear anywhere in the other.

    Re-exported PDFs (for example from Canva) often reorder their text, so an
    order-insensitive comparison points at real edits better than a line diff.
    """
    other = {' '.join(line.split()) for line in other_lines}
    missing, seen = [], set()
    for line in lines:
        key = ' '.join(line.split())
        if key and key not in other and key not in seen:
            seen.add(key)
            missing.append(line)
    return missing[:cap], len(missing) > cap


@login_required
@never_cache
def compare_reported_copy(request, log_id):
    """Show a copy reported as Possible Modification beside its official record."""
    if not (request.user.is_superuser or request.user.is_staff):
        raise Http404
    entry = get_object_or_404(visible_logs(request.user).select_related('contract'), pk=log_id)
    contract = entry.contract
    if not (entry.evidence_file and contract and not contract.is_trashed
            and entry.verification_result == 'Possible Modification'):
        raise Http404
    versions = contract.versions.exclude(file='')
    official = None
    if entry.version_number:
        official = versions.filter(version_number=entry.version_number).first()
    official = official or versions.order_by('-version_number').first()
    if official is None:
        raise Http404

    context = {'entry': entry, 'contract': contract, 'official': official}
    try:
        official_pages, official_lines, official_limited = extract_lines(read_pdf(official.file.path))
        with entry.evidence_file.open('rb') as handle:
            reported_data = handle.read()
        reported_pages, reported_lines, reported_limited = extract_lines(reported_data)
        only_official, official_more = _lines_missing_from(official_lines, reported_lines)
        only_reported, reported_more = _lines_missing_from(reported_lines, official_lines)
        context.update(
            official_pages=official_pages, reported_pages=reported_pages,
            reported_size=len(reported_data),
            only_official=only_official, only_reported=only_reported,
            truncated=official_more or reported_more,
            limited=official_limited or reported_limited,
            no_text=not official_lines or not reported_lines,
        )
        log_activity(request, 'viewed', contract=contract, version_number=official.version_number,
                     note=f'Compared reported copy (activity #{entry.id}) with official record v{official.version_number}')
    except (OSError, PDFDecryptionError, RuntimeError, ValueError):
        context['error'] = 'One of these documents could not be opened. Contact staff to check the stored files.'
    return render(request, 'evidence_comparison.html', context)


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
