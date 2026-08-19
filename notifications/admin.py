"""Read-only-ish admin for the notification audit trail.

Notifications are created by the marking engine, not by hand, so the admin here
is for looking, not authoring: what was sent, to whom, whether it arrived, and
why not when it failed.
"""

from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("parent", "paper", "status", "sent_at")
    list_filter = ("status", "sent_at")
    search_fields = (
        "parent__username",
        "parent__email",
        "paper__id",
        "summary_text",
    )
    readonly_fields = (
        "paper",
        "parent",
        "summary_text",
        "status",
        "error",
        "sent_at",
    )
    date_hierarchy = "sent_at"
