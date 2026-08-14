"""Marking routes."""

from django.urls import path

from . import views

app_name = "marking"

urlpatterns = [
    # Sprint 2's JSON endpoint, kept as the quickest way to exercise the engine
    # without a browser.
    path("submit/", views.submit_paper, name="submit_paper"),
    # Sprint 3: the teacher portal.
    path("memorandums/", views.memorandum_list, name="memorandum_list"),
    path("memorandums/new/", views.memorandum_create, name="memorandum_create"),
    path("mark/", views.mark_paper, name="mark_paper"),
    path("papers/", views.marking_history, name="marking_history"),
    path("papers/<int:pk>/", views.paper_detail, name="paper_detail"),
    path("papers/<int:pk>/retry/", views.paper_retry, name="paper_retry"),
]
