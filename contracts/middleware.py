import logging
import time

from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin
from .process_memory import process_memory_mb


memory_logger = logging.getLogger('contracts.memory')


class MemoryDiagnosticsMiddleware(MiddlewareMixin):
    """Log memory around requests when memory, latency, or status is abnormal."""
    WARN_RSS_MB = 300
    SLOW_REQUEST_SECONDS = 5

    def __init__(self, get_response):
        super().__init__(get_response)

    def process_view(self, request, view_func, view_args, view_kwargs):
        route = getattr(getattr(request, 'resolver_match', None), 'url_name', None) or 'unresolved'
        memory = process_memory_mb()
        memory_logger.info(
            'request_memory_start method=%s route=%s memory_mb=%s',
            request.method, route, memory,
        )

    def __call__(self, request):
        started = time.perf_counter()
        before = process_memory_mb()
        before_rss = before.get('vmrss', 0)
        route = 'unresolved'
        try:
            response = self.get_response(request)
        except Exception:
            route = getattr(getattr(request, 'resolver_match', None), 'url_name', None) or route
            memory_logger.exception(
                'request_exception method=%s route=%s elapsed_ms=%s memory_mb=%s',
                request.method, route,
                round((time.perf_counter() - started) * 1000),
                process_memory_mb(),
            )
            raise

        elapsed = time.perf_counter() - started
        route = getattr(getattr(request, 'resolver_match', None), 'url_name', None) or route
        after = process_memory_mb()
        after_rss = after.get('vmrss', 0)
        if (after_rss >= self.WARN_RSS_MB
                or response.status_code >= 500
                or elapsed >= self.SLOW_REQUEST_SECONDS):
            memory_logger.warning(
                'request_memory_end method=%s route=%s status=%s elapsed_ms=%s '
                'before_mb=%s after_mb=%s peak_mb=%s',
                request.method, route, response.status_code, round(elapsed * 1000),
                before_rss, after_rss, after.get('vmhwm'),
            )
        return response


def is_pdf_navigation(request):
    """A browser opening a PDF URL directly; it is redirected to the viewer."""
    return (request.META.get('HTTP_SEC_FETCH_MODE') == 'navigate'
            or 'text/html' in request.META.get('HTTP_ACCEPT', ''))


class InternalPDFViewerMiddleware:
    """Route browser PDF navigation through the SealGuard canvas viewer.

    PDF.js still receives authorized PDF bytes. This is a UI boundary, not
    protection against an authorized user extracting network responses.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (response.status_code == 200
                and response.get('Content-Type', '').split(';')[0] == 'application/pdf'
                and 'attachment' not in response.get('Content-Disposition', '')):
            response['Cache-Control'] = 'private, no-store'
            response['X-Content-Type-Options'] = 'nosniff'
            if is_pdf_navigation(request):
                from urllib.parse import urlencode
                from django.templatetags.static import static
                response.close()
                navigation = redirect(static('contracts/mobile-pdf.html') + '?' + urlencode({'file': request.get_full_path()}))
                navigation['Cache-Control'] = 'private, no-store'
                return navigation
        return response


class NullOriginApprovalMiddleware:
    """Keep token validation for QR scanners that submit with Origin: null.

    Some embedded QR browsers and pages opened from downloaded files omit a
    normal web origin. We remove only that sentinel origin for the public
    access-request POST, while CsrfViewMiddleware still validates the cookie
    and hidden form token. A same-host referer is supplied for HTTPS tunnels
    when the scanner omitted one as well.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.method == 'POST'
                and request.path.startswith('/document-access/')
                and request.META.get('HTTP_ORIGIN') == 'null'):
            request.META.pop('HTTP_ORIGIN', None)
            if request.is_secure() and not request.META.get('HTTP_REFERER'):
                request.META['HTTP_REFERER'] = request.build_absolute_uri()
        return self.get_response(request)


class PDFStorageFailureMiddleware:
    """Do not expose cryptographic details or return unauthenticated PDF bytes."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        from .pdf_storage import PDFDecryptionError
        if isinstance(exception, PDFDecryptionError):
            from django.http import HttpResponse
            response = HttpResponse('This PDF could not be authenticated in storage. Please contact an administrator.', status=503)
            response['Cache-Control'] = 'private, no-store'
            return response


class RequiredPasswordChangeMiddleware:
    """Keep newly provisioned accounts in the password-change screen."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            path = request.path
            security = getattr(user, 'account_security', None)
            if security and security.must_change_password and path not in {
                '/accounts/password-change-required/', '/accounts/logout/',
            }:
                return redirect('password_change_required')
        return self.get_response(request)


# Resident accounts exist only to verify documents and read published ones.
# This is an allowlist so that a page added later stays closed to residents
# until it is deliberately listed here. Document views still check that the
# requested document is published (contracts.access.is_published).
RESIDENT_URL_NAMES = frozenset({
    'public_verify', 'verification_preview',
    'verify_physical', 'verify_physical_qr', 'verify_physical_page',
    'open_encrypted_document', 'request_document_access', 'approved_document_pdf',
    'preview_contract', 'preview_contract_version', 'download_contract',
    'login', 'logout', 'password_change', 'password_change_done',
    'resident_register', 'health_check',
})


class ResidentAccessMiddleware:
    """Keep resident accounts out of the staff workspace and admin."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .access import is_resident
        user = getattr(request, 'user', None)
        if user and is_resident(user) and not self._allowed(request):
            if request.method in ('GET', 'HEAD'):
                return redirect('public_verify')
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('Resident accounts can only verify documents and view published documents.')
        return self.get_response(request)

    @staticmethod
    def _allowed(request):
        from django.conf import settings
        from django.urls import Resolver404, resolve
        path = request.path_info
        if path == '/' or path.startswith((settings.STATIC_URL, settings.MEDIA_URL)):
            return True
        try:
            return resolve(path).url_name in RESIDENT_URL_NAMES
        except Resolver404:
            return False
