"""Marking routes."""

from django.urls import path

from . import views

app_name = "marking"

urlpatterns = [
    # The Sprint 2 test endpoint. Sprints 3 and 4 add the portal-facing views.
    path("submit/", views.submit_paper, name="submit_paper"),
]
