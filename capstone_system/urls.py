from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve
from django.shortcuts import redirect
from django.contrib.auth.views import LoginView
from contracts.forms import SealGuardAuthenticationForm

def home_redirect(request):
    if request.user.is_authenticated:
        return redirect('contract_list')
    return redirect('public_verify')

urlpatterns = [
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
# its dedicated web server or storage service instead.
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {
        'document_root': settings.MEDIA_ROOT,
    }),
]
