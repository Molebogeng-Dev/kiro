"""Dashboard routes."""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("teacher/", views.teacher_dashboard, name="teacher_dashboard"),
    path("student/", views.student_dashboard, name="student_dashboard"),
    path("parent/", views.parent_dashboard, name="parent_dashboard"),
    path("parent/child/<int:child_id>/", views.parent_child, name="parent_child"),
    path("parent/paper/<int:pk>/", views.parent_paper, name="parent_paper"),
]
