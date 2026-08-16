"""Classroom content routes."""

from django.urls import path

from . import views

app_name = "classroom"

urlpatterns = [
    path("assignments/", views.assignment_list, name="assignment_list"),
    path("assignments/new/", views.assignment_create, name="assignment_create"),
    path("materials/", views.material_list, name="material_list"),
    path("materials/new/", views.material_create, name="material_create"),
    # Sprint 4: the student portal.
    path("study-materials/", views.student_material_list, name="student_material_list"),
    path(
        "study-materials/<int:pk>/",
        views.student_material_detail,
        name="student_material_detail",
    ),
    path("my-assignments/", views.student_assignment_list, name="student_assignment_list"),
    path(
        "my-assignments/<int:pk>/submit/",
        views.submit_homework,
        name="submit_homework",
    ),
]
