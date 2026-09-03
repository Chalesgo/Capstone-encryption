from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
