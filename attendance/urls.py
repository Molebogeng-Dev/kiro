"""Attendance routes."""

from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    path("", views.attendance_index, name="index"),
    path("roll-call/", views.roll_call, name="roll_call"),
    path("enroll/", views.enroll, name="enroll"),
    path("check-in/", views.check_in, name="check_in"),
    path("mark-present/", views.mark_present, name="mark_present"),
    path("history/", views.history, name="history"),
]
