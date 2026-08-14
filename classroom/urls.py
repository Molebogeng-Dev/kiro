"""Classroom content routes."""

from django.urls import path

from . import views

app_name = "classroom"

urlpatterns = [
    path("assignments/", views.assignment_list, name="assignment_list"),
    path("assignments/new/", views.assignment_create, name="assignment_create"),
    path("materials/", views.material_list, name="material_list"),
    path("materials/new/", views.material_create, name="material_create"),
]
