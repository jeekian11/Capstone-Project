from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from accounts.views import role_redirect, CompulabLoginView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', role_redirect, name='home'),
    path('accounts/login/', CompulabLoginView.as_view(), name='login'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('accounts/', include('accounts.urls')),
    path('labs/', include('labs.urls')),
    path('schedule/', include('scheduling.urls')),
    path('issues/', include('issues.urls')),
    path('notifications/', include('notifications.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)