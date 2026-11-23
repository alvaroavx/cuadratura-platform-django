from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("integrations.urls")),
    path("", include("accounts.urls")),
    path("", include("organizations.urls")),
    path("", include("sales.urls")),
    path("", include("reports.urls")),
    path("", include("imports.urls")),
]
