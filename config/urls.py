from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('accounts.urls')),          # login, logout, change password
    path('pos/', include('pos.urls')),
    path('master/', include('masteradmin.urls')),
    path('suppliers/', include('suppliers.urls')),
    path('import/', include('importer.urls')),
    path('prices/', include('pricelist.urls')),
    path('', include('sku_manager.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
