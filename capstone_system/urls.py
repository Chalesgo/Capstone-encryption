import os

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from contracts.file_access import protected_media
from django.shortcuts import redirect
from django.contrib.auth.views import LoginView
from django.contrib.staticfiles.views import serve as static_serve
from contracts.forms import SealGuardAuthenticationForm

def home_redirect(request):
    if request.user.is_authenticated:
        return redirect('contract_list')
    return redirect('public_verify')


def health_check(request):
    """Verify that the web process can reach its database and encryption keys."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        keys_available = all(
            os.path.isfile(path)
            for path in (settings.RSA_PRIVATE_KEY_PATH, settings.RSA_PUBLIC_KEY_PATH)
        )
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)
    if not keys_available:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})

urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('admin/', admin.site.urls),
    path('accounts/login/', LoginView.as_view(authentication_form=SealGuardAuthenticationForm), name='login'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', home_redirect),
    path('', include('contracts.urls')),
]

# This project uses Django's runserver for the local/staging deployment. The
# convenience static() helper disables itself when DEBUG=False, so use an
# explicit route to keep uploaded PDFs and seal thumbnails available during
# staging/performance tests. Production hosting should route MEDIA_URL through
# this protected endpoint too; never expose MEDIA_ROOT as a public directory.
if settings.DEBUG or settings.SERVE_STATIC_VIA_DJANGO:
    urlpatterns.append(
        re_path(r'^static/(?P<path>.*)$', static_serve, {'insecure': True}),
    )

# Encrypted PDFs and generated seals always pass through SealGuard's
# authorization-aware media view. Never expose MEDIA_ROOT as a static folder.
if settings.SERVE_MEDIA:
    urlpatterns.append(re_path(r'^media/(?P<path>.*)$', protected_media))
