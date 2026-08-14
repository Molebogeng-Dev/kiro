"""Admin for users and parent/student links.

The admin is the fallback tool for linking a learner to a parent when the
self-service path at registration was not used.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import ParentStudentLink, User


class ParentLinkInline(admin.TabularInline):
    """Parents attached to a student, editable from the student's page."""

    model = ParentStudentLink
    fk_name = "student"
    extra = 1
    autocomplete_fields = ["parent"]
    verbose_name = "linked parent"
    verbose_name_plural = "linked parents"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "first_name", "last_name", "is_staff")
    list_filter = ("role", "is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")
    inlines = [ParentLinkInline]

    fieldsets = DjangoUserAdmin.fieldsets + (
        ("iSgela", {"fields": ("role", "school")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("iSgela", {"fields": ("email", "role", "school")}),
    )


@admin.register(ParentStudentLink)
class ParentStudentLinkAdmin(admin.ModelAdmin):
    list_display = ("student", "parent", "created_at")
    search_fields = ("student__username", "parent__username")
    autocomplete_fields = ["student", "parent"]
