"""Auth routes."""

from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("register/school/", views.register_school, name="register_school"),
    path("login/", views.RoleLoginView.as_view(), name="login"),
    # POST-only, per Django's current logout behaviour. The nav submits a form.
    path("logout/", LogoutView.as_view(), name="logout"),
]
