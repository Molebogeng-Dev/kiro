"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("marking/", include("marking.urls")),
    path("classroom/", include("classroom.urls")),
    # Dashboards live at the root: /teacher/, /student/, /parent/
    path("", include("core.urls")),
]
