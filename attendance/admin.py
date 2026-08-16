from django.contrib import admin

from .models import Attendance, FaceEnrollment


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("student", "date", "method", "arrived_at", "departed_at")
    list_filter = ("method", "date")
    search_fields = ("student__username", "student__first_name", "student__last_name")
    autocomplete_fields = ["student"]


@admin.register(FaceEnrollment)
class FaceEnrollmentAdmin(admin.ModelAdmin):
    # The descriptor is biometric data; show that a student is enrolled and by
    # whom, but do not surface the vector itself in a list.
    list_display = ("student", "enrolled_by", "consent_confirmed", "enrolled_at")
    list_filter = ("consent_confirmed",)
    search_fields = ("student__username",)
    autocomplete_fields = ["student", "enrolled_by"]
    readonly_fields = ("enrolled_at", "updated_at")
