from django.shortcuts import redirect


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
